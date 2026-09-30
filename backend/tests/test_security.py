from cip.core.security.auth import create_access_token, decode_access_token, hash_password, verify_password
from cip.core.security.crypto import TokenCipher
from cip.core.security.secrets import is_sensitive_path, redact, scan


def test_sensitive_paths():
    for p in (".env", "config/.env.production", "deploy/id_rsa", "certs/server.pem", "infra/terraform.tfstate",
              "secrets.yaml"):
        assert is_sensitive_path(p), p
    for p in (".env.example", "src/env.ts", "README.md", "package.json"):
        assert not is_sensitive_path(p), p


STRIPE = "sk" + "_live_" + "abcdefghijklmnop1234567890"  # assembled so scanners don't flag the source


def test_redacts_known_secret_formats():
    text = (
        "AWS=AKIAIOSFODNN7EXAMPLE\n"
        "gh = 'ghp_" + "a" * 36 + "'\n"
        f"const stripe = '{STRIPE}'\n"
        "DATABASE_URL=postgres://admin:hunter2pass@db.internal:5432/prod\n"
        "api_key = \"Zx9Qm2Lp7Rt4Vw8Ky3Nb6\"\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n"
    )
    assert len(scan(text)) >= 5
    clean, n = redact(text)
    assert n >= 5
    for secret in ("AKIAIOSFODNN7EXAMPLE", STRIPE, "hunter2pass", "Zx9Qm2Lp7Rt4Vw8Ky3Nb6", "MIIabc"):
        assert secret not in clean


def test_placeholders_are_not_redacted():
    text = "password = process.env.DB_PASSWORD\napi_key: ${API_KEY}\ntoken = 'changeme'\n"
    clean, n = redact(text)
    assert n == 0 and clean == text


def test_token_cipher_roundtrip():
    c = TokenCipher()
    enc = c.encrypt("gho_secret_token")
    assert "gho_secret_token" not in enc
    assert c.decrypt(enc) == "gho_secret_token"


def test_password_and_jwt():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)
    payload = decode_access_token(create_access_token("usr_1", "org_1", "analyst"))
    assert payload["sub"] == "usr_1" and payload["org"] == "org_1" and payload["role"] == "analyst"
