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
    print(f"  crawler timeout: {s.crawler_timeout_seconds}s, retries: {s.crawler_retries}, user agent: {s.crawler_user_agent}")
    print()


async def main(urls: list[str]) -> int:
    environment()
    fetcher = WebFetcher(rendering="never")
    bad = 0
    for url in urls:
        check = await check_domain(fetcher, url)
        print(f"{check['url']}: {check['status'].upper()}")
        if check.get("final_url"):
            print(f"  final URL: {check['final_url']} (HTTP {check['http_status']})")
        if check.get("detail"):
            print(f"  {check['detail']}")
        if check["status"] in ("unreachable", "blocked"):
            bad += 1
        else:
            page = await fetcher.fetch(url)
            print(f"  page read: {'yes' if page else 'no'}" + (f" — “{page.title[:80]}”, {len(page.text):,} characters"
                                                                 if page else ""))
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")  # shows retries and TLS repair
    sys.exit(asyncio.run(main(sys.argv[1:])))
