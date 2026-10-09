"""Domain checks for client URLs (BRS 7.1 validations; PRD CSV-REQ "Unreachable / Redirecting / Parked").

``check_domain`` fetches the homepage once (SSRF-guarded, robots-aware, with the fetcher's retries) and
classifies it:

* ``ok`` — the site answered on its own domain;
* ``redirected`` — it answered, but on a different domain (renamed or acquired company, regional site);
* ``parked`` — a domain-parking or "for sale" page, not a business site;
* ``unreachable`` — no answer (DNS, connection, timeout) or an error status;
* ``blocked`` — robots.txt disallows us, or the address is not public.

Only the homepage is requested; nothing is stored beyond the classification.
"""

from __future__ import annotations

import asyncio

from bs4 import BeautifulSoup

from cip.connectors.research.web import WebFetcher, normalize_url, registrable_domain

PARKED_MARKERS = (
    "this domain is for sale", "this domain name is for sale", "buy this domain", "domain is for sale",
    "the domain may be for sale", "domain may be for sale", "is available for purchase", "inquire about this domain",
    "make an offer on this domain", "this domain is parked", "domain parking", "parked free", "parked domain",
    "courtesy of godaddy", "sedoparking", "parkingcrew", "bodis.com", "hugedomains", "afternic", "dan.com",
    "undeveloped.com", "this web page is parked", "related searches", "domain has expired",
)
BLOCKED_REASONS = ("robots_disallowed", "blocked_address", "unsupported_url")
HINTS = {
    "tls_certificate": "The server could not verify the site's certificate: update the CA certificates on the server "
                       "(e.g. `ca-certificates`, `pip install -U certifi`) or set SSL_CERT_FILE to your proxy's CA bundle",
    "tls_error": "The TLS handshake failed (an intercepting proxy or an outdated server)",
    "dns_error": "The server's DNS could not resolve the domain",
    "unresolvable": "The server's DNS could not resolve the domain. If this server reaches the internet only through "
                    "a proxy (HTTPS_PROXY), set CIP_CRAWLER_PROXY_RESOLVES_DNS=true",
    "connection_refused": "The site refused the connection on port 443",
    "network_unreachable": "The server has no route to the internet (outbound firewall or missing proxy settings)",
    "no_route_to_host": "Outbound traffic is blocked or the host is down (firewall)",
    "connection_reset": "The connection was reset (firewall, bot protection or proxy)",
    "proxy_error": "The configured HTTP(S) proxy failed (check HTTPS_PROXY / NO_PROXY for the API process)",
    "connection_error": "Check that the API process can make outbound HTTPS requests (firewall, proxy settings)",
    "timeout": "The site did not answer in time (CIP_CRAWLER_TIMEOUT_SECONDS) or outbound traffic is silently dropped",
}
MAX_CONCURRENT_CHECKS = 8


def parked_signals(body: str) -> list[str]:
    soup = BeautifulSoup(body or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split()).lower()
    hits = [m for m in PARKED_MARKERS if m in text or m in (body or "").lower()]
    # "related searches" alone is common on real sites; require it alongside another marker.
    return hits if len(hits) > 1 or (hits and hits != ["related searches"]) else []


async def check_domain(fetcher: WebFetcher, url: str) -> dict:
    url = normalize_url(url)
    out = {"url": url, "status": "ok", "final_url": None, "http_status": None, "detail": ""}
    raw = await fetcher.fetch_raw(url, evidence=False)
    if raw is None:
        failure = fetcher.last_failure(url) or {}
        reason = failure.get("reason", "no response")
        hint = HINTS.get(reason, "")
        out.update(status="blocked" if reason in BLOCKED_REASONS else "unreachable",
                   detail=reason.replace("_", " ") + (f": {failure['detail']}" if failure.get("detail") else "")
                   + (f". {hint}" if hint else ""), reason=reason)
        return out
    status, _, final_url, body = raw
    out.update(final_url=final_url, http_status=status)
    if status >= 400:
        out.update(status="unreachable", detail=f"HTTP {status}")
        return out
    signals = parked_signals(body)
    if signals:
        out.update(status="parked", detail="Parked or for-sale page (" + ", ".join(signals[:3]) + ")")
        return out
    if registrable_domain(final_url) != registrable_domain(url):
        out.update(status="redirected", detail=f"Redirects to {registrable_domain(final_url)}")
        return out
    if final_url.rstrip("/") != url.rstrip("/"):
        out["detail"] = f"Redirects to {final_url}"
    return out


async def check_many(fetcher: WebFetcher, urls: dict[int, str]) -> dict[int, dict]:
    """Check several URLs (keyed by row number) with bounded concurrency."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

    async def one(row: int, u: str) -> tuple[int, dict]:
        async with gate:
            return row, await check_domain(fetcher, u)

    return dict(await asyncio.gather(*(one(r, u) for r, u in urls.items())))


def is_problem(check: dict | None) -> bool:
    return bool(check) and check["status"] in ("unreachable", "parked", "blocked")


DOMAIN_LABELS = {"ok": "reachable", "redirected": "redirects to another domain", "parked": "parked / for sale",
                 "unreachable": "unreachable", "blocked": "blocked (robots.txt or non-public address)"}


def summary(check: dict) -> str:
    label = DOMAIN_LABELS.get(check["status"], check["status"])
    return f"{label}: {check['detail']}" if check.get("detail") else label

