"""TLS for research fetches, with missing-intermediate repair (AIA fetching), as browsers do.

Many sites send only their own certificate and forget the intermediate one that links it to a trusted root.
Browsers quietly download the intermediate from the certificate's *Authority Information Access* (AIA) URL;
OpenSSL does not, so Python fails with "unable to get local issuer certificate" although the site opens fine in
a browser. When that happens we read the site's certificate (without trusting it), download the issuer
certificates named in its AIA extension, and add them to the shared verification store.

This never weakens verification: the downloaded certificates are only used to *build* the chain, which must still
end at a trusted root from the CA bundle (no partial-chain mode), and the site's hostname is still checked.
The CA bundle is ``SSL_CERT_FILE`` when set (e.g. a corporate proxy's bundle), otherwise certifi's.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import ssl

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

log = logging.getLogger(__name__)

MAX_DEPTH = 3
_context: ssl.SSLContext | None = None
_added: set[str] = set()  # fingerprints of intermediates already in the store
_tried: set[str] = set()  # hosts already repaired (or attempted), so one bad site can't loop
_lock = asyncio.Lock()


def context() -> ssl.SSLContext:
    """The shared verification context for research fetches (mutated in place when intermediates are added)."""
    global _context
    if _context is None:
        _context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    return _context


def reset() -> None:  # tests
    global _context
    _context = None
    _added.clear()
    _tried.clear()


def _leaf_pem(host: str, port: int, timeout: float) -> str:
    """The site's certificate, read without verifying it (only to learn where its issuer is published)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock, ctx.wrap_socket(sock, server_hostname=host) as tls:
        return ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True))


def _issuer_urls(cert: x509.Certificate) -> list[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
            and isinstance(d.access_location, x509.UniformResourceIdentifier)
            and d.access_location.value.lower().startswith(("http://", "https://"))]


def _parse(data: bytes) -> list[x509.Certificate]:
    for loader in (lambda b: [x509.load_der_x509_certificate(b)], lambda b: [x509.load_pem_x509_certificate(b)],
                   pkcs7.load_der_pkcs7_certificates, pkcs7.load_pem_pkcs7_certificates):
        try:
            return list(loader(data))
        except ValueError:
            continue
    return []


async def repair(url: str, *, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None,
                 leaf_pem: str | None = None) -> bool:
    """Fetch the intermediates missing from ``url``'s chain into the shared store. True when something was added."""
    from cip.connectors.research.web import UnsafeURL, assert_public_url, urlparse

    parsed = urlparse(url)
    host, port = parsed.hostname or "", parsed.port or 443
    async with _lock:
        if not host or host in _tried:
            return False
        _tried.add(host)
        try:
            pem = leaf_pem or await asyncio.wait_for(asyncio.to_thread(_leaf_pem, host, port, timeout), timeout + 2)
            cert = x509.load_pem_x509_certificate(pem.encode())
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            log.info("TLS repair: could not read the certificate of %s: %s", host, exc)
            return False
        added = 0
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport) as client:
            for _ in range(MAX_DEPTH):
                if cert.issuer == cert.subject:
                    break  # self-signed: nothing more to fetch
                issuer = None
                for aia_url in _issuer_urls(cert):
                    try:
                        if transport is None:
                            await assert_public_url(aia_url)
                        resp = await client.get(aia_url)
                    except (httpx.HTTPError, UnsafeURL) as exc:
                        log.info("TLS repair: %s unavailable: %s", aia_url, exc)
                        continue
                    found = _parse(resp.content) if resp.status_code < 400 else []
                    issuer = next((c for c in found if c.subject == cert.issuer), found[0] if found else None)
                    if issuer is not None:
                        break
                if issuer is None:
                    break
                fp = issuer.fingerprint(issuer.signature_hash_algorithm).hex() if issuer.signature_hash_algorithm else ""
                if fp not in _added:
                    context().load_verify_locations(cadata=issuer.public_bytes(Encoding.PEM).decode())
                    _added.add(fp)
                    added += 1
                cert = issuer
        if added:
            log.warning("TLS repair: %s does not send its intermediate certificate; fetched %d from its AIA URL",
                        host, added)
        return added > 0
