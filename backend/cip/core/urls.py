"""URL parsing that never raises.

``urllib.parse`` raises ``ValueError`` on some malformed input — e.g. ``[2013-04-24]`` is read as a
bracketed IPv6 host (Python 3.11.4+), and an out-of-range port fails on ``.port``. URLs here come from
user CSVs and from scraped third-party pages, so one bad value must never crash an upload or a crawl:
these drop-in replacements return an empty result instead (no scheme, no host), which callers
already treat as "not a usable URL".
"""

from __future__ import annotations

from urllib.parse import DefragResult, ParseResult
from urllib.parse import urldefrag as _urldefrag
from urllib.parse import urljoin as _urljoin
from urllib.parse import urlparse as _urlparse

_EMPTY = _urlparse("")


def urlparse(url: str, scheme: str = "", allow_fragments: bool = True) -> ParseResult:
    try:
        parsed = _urlparse(url, scheme, allow_fragments)
        parsed.port  # noqa: B018 - validates the port too (raises on e.g. :99999)
        return parsed
    except (ValueError, TypeError):
        return _EMPTY


def urljoin(base: str, url: str) -> str:
    try:
        return _urljoin(base, url)
    except (ValueError, TypeError):
        return ""


def urldefrag(url: str) -> DefragResult:
    try:
        return _urldefrag(url)
    except (ValueError, TypeError):
        return DefragResult(url, "")


def is_valid_http_url(url: str | None) -> bool:
    p = urlparse(url or "")
    return p.scheme in ("http", "https") and bool(p.hostname)
