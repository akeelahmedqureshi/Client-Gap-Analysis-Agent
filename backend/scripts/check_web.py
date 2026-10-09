"""Check that this server can research a website, the same way an analysis does.

    cd backend
    python scripts/check_web.py https://www.example.com
    python scripts/check_web.py example.com other-client.com

Prints the domain check (reachable / redirected / parked / unreachable / blocked), the exact cause of a failure
(TLS certificate, DNS, firewall, proxy, timeout…) with a hint, and the network settings the API process uses.
Run it as the same user and with the same environment (.env, proxy variables) as the API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cip.config import get_settings  # noqa: E402
from cip.connectors.research import tls  # noqa: E402
from cip.connectors.research.domain import check_domain  # noqa: E402
from cip.connectors.research.web import WebFetcher  # noqa: E402


def environment() -> None:
    s = get_settings()
    print("Network settings of this process:")
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE",
                 "REQUESTS_CA_BUNDLE"):
        if os.environ.get(name):
            value = os.environ[name]
            print(f"  {name} = {value.split('@')[-1] if 'proxy' in name.lower() else value}")  # hide proxy credentials
    try:
        import certifi
        print(f"  certifi CA bundle: {certifi.where()}")
    except ImportError:
        print("  certifi not installed")
    print(f"  OpenSSL: {ssl.OPENSSL_VERSION}")
    if not s.crawler_verify_tls:
        print("  TLS verification: OFF for research fetches (CIP_CRAWLER_VERIFY_TLS=false)")
    extra = s.crawler_extra_ca_file
    if not extra:
        print("  extra CA file (CIP_CRAWLER_EXTRA_CA_FILE): not set")
    elif not Path(extra).is_file():
        print(f"  extra CA file: {extra} — NOT FOUND (or not readable by this user)")
    else:
        try:
            ssl.create_default_context(cafile=extra)
            print(f"  extra CA file: {extra} (loaded)")
        except (OSError, ssl.SSLError) as exc:
            print(f"  extra CA file: {extra} — NOT A VALID PEM CERTIFICATE: {exc}")
    print(f"  browser rendering: {s.browser_rendering}")
    print(f"  crawler timeout: {s.crawler_timeout_seconds}s, retries: {s.crawler_retries}, user agent: {s.crawler_user_agent}")
    print()


async def explain_certificate(url: str) -> None:
    info = await tls.inspect(url)
    if not info:
        return
    print(f"  certificate: {info['subject']}")
    print(f"  issued by:   {info['issuer']}")
    if info["interceptor"]:
        print(f"  -> HTTPS from this server is intercepted by a {info['interceptor']} firewall (HTTPS / deep inspection):")
        print("     the site is fine, but this network replaces its certificate with one signed by the firewall's own CA.")
        print("     Fix: ask the network admin to exempt this server from HTTPS inspection, or export the firewall's CA")
        print("     certificate (PEM) and set CIP_CRAWLER_EXTRA_CA_FILE=/path/to/firewall-ca.pem in .env, then restart.")
    elif not info["issuer_url"]:
        print("  -> the certificate names no URL for its issuer, so the missing certificate cannot be fetched automatically.")


async def main(urls: list[str]) -> int:
    environment()
    fetcher = WebFetcher()  # same rendering setting as analyses (CIP_BROWSER_RENDERING)
    if fetcher.renderer is not None and not fetcher.renderer.available:
        print(f"Browser renderer unavailable: {fetcher.renderer._unavailable_reason}\n")
    bad = 0
    async with fetcher._render_session():  # one browser for all checks, closed before exit
        for url in urls:
            check = await check_domain(fetcher, url)
            print(f"{check['url']}: {check['status'].upper()}")
            if check.get("final_url"):
                print(f"  final URL: {check['final_url']} (HTTP {check['http_status']})")
            if check.get("detail"):
                print(f"  {check['detail']}")
            if check.get("reason") == "tls_certificate":
                await explain_certificate(check["url"])
            if check["status"] in ("unreachable", "blocked"):
                bad += 1
            else:
                page = await fetcher.fetch(url)
                print(f"  page read: {'yes' if page else 'no'}" + (f" — “{page.title[:80]}”, {len(page.text):,} characters"
                                                                     + (" (browser)" if page.rendered else "")
                                                                     if page else ""))
                failure = fetcher.last_failure(check["url"]) if page is None else None
                if failure:
                    print(f"  {failure['reason'].replace('_', ' ')}: {failure.get('detail') or ''}")
                elif page is not None and len(page.text.strip()) < 200:
                    print("  -> almost no readable text: the site may need the browser renderer or block crawlers")
            print()
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")  # shows retries and TLS repair
    sys.exit(asyncio.run(main(sys.argv[1:])))
