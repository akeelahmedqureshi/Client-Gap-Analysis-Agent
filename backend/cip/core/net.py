"""TLS trust for outbound API connections (LLM, search, source control, CRM, webhooks).

These connections carry credentials, so certificate verification is always on (``CIP_CRAWLER_VERIFY_TLS`` never
applies to them). Trusted: the public CAs (``SSL_CERT_FILE`` when set, otherwise certifi's) plus
``CIP_EXTRA_CA_FILE`` — e.g. the CA of a firewall that inspects HTTPS, which otherwise makes every API call fail
with CERTIFICATE_VERIFY_FAILED.
"""

from __future__ import annotations

import logging
import os
import ssl

import certifi

log = logging.getLogger(__name__)
_context: ssl.SSLContext | None = None


def load_extra(ctx: ssl.SSLContext, path: str | None, setting: str) -> None:
    if not path:
        return
    try:
        ctx.load_verify_locations(cafile=path)
    except (OSError, ssl.SSLError) as exc:
        log.error("%s %s could not be loaded: %s", setting, path, exc)


def api_verify() -> ssl.SSLContext:
    """The ``verify=`` for httpx clients of external APIs (shared, created once)."""
    global _context
    if _context is None:
        from cip.config import get_settings

        ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
        load_extra(ctx, get_settings().extra_ca_file, "CIP_EXTRA_CA_FILE")
        _context = ctx
    return _context


def reset() -> None:  # tests
    global _context
    _context = None
