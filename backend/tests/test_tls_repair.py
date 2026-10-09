"""Missing intermediate certificates are fetched from the AIA URL (as browsers do), without weakening checks."""

import asyncio
import datetime as dt
import ipaddress
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

from cip.connectors.research import tls

AIA_URL = "http://ca.test/intermediate.cer"


def _cert(subject, issuer_name, key, issuer_key, *, ca, aia=None, san=None):
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if ca:
        b = b.add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                          key_encipherment=False, data_encipherment=False, key_agreement=False,
                                          encipher_only=False, decipher_only=False), critical=True)
    if aia:
        b = b.add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
            AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier(aia))]), critical=False)
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san))]), critical=False)
    return b.sign(issuer_key, hashes.SHA256())


def _pki(root_name="Test Root"):
    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    root = _cert(root_name, root_name, keys[0], keys[0], ca=True)
    inter = _cert("Test Intermediate", root_name, keys[1], keys[0], ca=True)
    leaf = _cert("site", "Test Intermediate", keys[2], keys[1], ca=False, aia=AIA_URL, san="127.0.0.1")
    return root, inter, leaf, keys[2]


async def _serve_leaf_only(leaf, key, tmp_path):
    """An HTTPS server that sends only its own certificate (the misconfiguration)."""
    (tmp_path / "leaf.pem").write_bytes(leaf.public_bytes(Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "key.pem")

    async def handle(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture(autouse=True)
def fresh_store():
    tls.reset()
    yield
    tls.reset()


async def _get(url):
    async with httpx.AsyncClient(verify=tls.context(), trust_env=False) as c:
        return await c.get(url)


async def test_missing_intermediate_is_fetched_and_the_site_verifies(tmp_path):
    root, inter, leaf, key = _pki()
    tls.context().load_verify_locations(cadata=root.public_bytes(Encoding.PEM).decode())  # a trusted root
    server, port = await _serve_leaf_only(leaf, key, tmp_path)
    url = f"https://127.0.0.1:{port}/"
    aia = httpx.MockTransport(lambda r: httpx.Response(200, content=inter.public_bytes(Encoding.DER))
                              if str(r.url) == AIA_URL else httpx.Response(404))
    try:
        with pytest.raises(httpx.ConnectError, match="local issuer"):
            await _get(url)
        assert await tls.repair(url, transport=aia, leaf_pem=leaf.public_bytes(Encoding.PEM).decode())
        assert (await _get(url)).text == "ok"
        assert not await tls.repair(url, transport=aia)  # one attempt per host
    finally:
        server.close()


async def test_an_intermediate_from_an_untrusted_root_still_fails(tmp_path):
    _, inter, leaf, key = _pki("Untrusted Root")  # its root is NOT in the store
    server, port = await _serve_leaf_only(leaf, key, tmp_path)
    url = f"https://127.0.0.1:{port}/"
    aia = httpx.MockTransport(lambda r: httpx.Response(200, content=inter.public_bytes(Encoding.DER)))
    try:
        assert await tls.repair(url, transport=aia, leaf_pem=leaf.public_bytes(Encoding.PEM).decode())
        with pytest.raises(httpx.ConnectError, match="certificate verify failed"):
            await _get(url)
    finally:
        server.close()


async def test_https_inspection_by_a_firewall_is_named_and_its_ca_can_be_trusted(tmp_path, monkeypatch):
    from cip.config import get_settings

    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(2)]
    fw_ca = _cert("FortiGate FG200F", "FortiGate FG200F", keys[0], keys[0], ca=True)
    leaf = _cert("site", "FortiGate FG200F", keys[1], keys[0], ca=False, san="127.0.0.1")  # re-signed by the firewall
    server, port = await _serve_leaf_only(leaf, keys[1], tmp_path)
    url = f"https://127.0.0.1:{port}/"
    try:
        info = await tls.inspect(url)
        assert info["interceptor"] == "Fortinet FortiGate" and "FortiGate FG200F" in info["issuer"]
        assert not await tls.repair(url)  # nothing to download: the firewall's CA must be trusted explicitly
        with pytest.raises(httpx.ConnectError, match="local issuer"):
            await _get(url)

        (tmp_path / "fw.pem").write_bytes(fw_ca.public_bytes(Encoding.PEM))
        monkeypatch.setenv("CIP_CRAWLER_EXTRA_CA_FILE", str(tmp_path / "fw.pem"))
        get_settings.cache_clear()
        tls.reset()
        assert (await _get(url)).text == "ok"
    finally:
        server.close()
        monkeypatch.delenv("CIP_CRAWLER_EXTRA_CA_FILE")
        get_settings.cache_clear()
