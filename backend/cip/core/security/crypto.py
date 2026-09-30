"""Encryption of OAuth / access tokens at rest (Fernet, AES-128-CBC + HMAC)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from cip.config import get_settings


class TokenCipher:
    def __init__(self, key: str | None = None) -> None:
        settings = get_settings()
        key = key or settings.token_encryption_key
        if not key:
            if settings.environment == "production":
                raise RuntimeError("CIP_TOKEN_ENCRYPTION_KEY must be set in production")
            # Development fallback: derive a key from the JWT secret so tokens are
            # still never stored in plaintext.
            key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret.encode()).digest()).decode()
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:  # pragma: no cover - defensive
            raise ValueError("Unable to decrypt stored token") from exc
