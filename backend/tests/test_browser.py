"""JavaScript rendering: heuristics, fallback, and real-browser tests (skipped if no browser)."""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cip.config import get_settings
from cip.connectors.research.browser import BrowserRenderer, looks_script_rendered
from cip.connectors.research.web import WebFetcher, parse_html

SPA_SHELL = '<html><head><title>Acme</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
STATIC = "<html><body><h1>Acme</h1><p>" + ("Online booking and SMS reminders for clinics. " * 20) + "</p></body></html>"


def test_heuristic_detects_spa_shells_only():
    assert looks_script_rendered(SPA_SHELL, parse_html("https://a.com/", 200, SPA_SHELL).text)
    assert looks_script_rendered("<body><noscript>Please enable JavaScript</noscript></body>", "Please enable JavaScript")
    assert not looks_script_rendered(STATIC, parse_html("https://a.com/", 200, STATIC).text)


async def test_falls_back_to_plain_http_when_browser_unavailable():
    import httpx

    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=SPA_SHELL,
                                                             headers={"content-type": "text/html"}))
    renderer = BrowserRenderer()
    renderer._unavailable_reason = "simulated: playwright missing"
    fetcher = WebFetcher(transport=transport, rendering="auto", renderer=renderer)
    page = await fetcher.fetch("https://acme.test/")
    assert page is not None and page.rendered is False and page.title == "Acme"


# ---------------------------------------------------------------- real browser

def _browser_executable() -> str | None:
    exe = os.environ.get("CIP_BROWSER_EXECUTABLE")
    if exe:
        return exe
    for candidate in ("/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell",):
        if Path(candidate).exists():
            return candidate
    return None  # let Playwright use its own installed browser (CI)


class _Site(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}
    pages = {
        "/": SPA_SHELL,
        "/features": SPA_SHELL,
        "/app.js": """
            const path = location.pathname;
            const root = document.getElementById('root');
            root.innerHTML = path === '/features'
              ? '<h1>Features</h1><p>AI assistant answers patient questions. Video consultations included.</p>'
              : '<h1>Acme Clinic Cloud</h1><p>Book an appointment online, SMS reminders and a patient portal.</p>' +
                '<a href="/features">Features</a><img src="/logo.png">';
            // Attempts to reach an internal address must be blocked by the renderer.
            fetch('http://localhost:%PORT%/internal-metadata').catch(() => {});
        """,
        "/logo.png": "png",
        "/internal-metadata": "secret",
    }

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        _Site.hits[path] = _Site.hits.get(path, 0) + 1
        body = self.pages.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        body = body.replace("%PORT%", str(self.server.server_address[1]))
        ctype = "application/javascript" if path.endswith(".js") else "image/png" if path.endswith(".png") \
            else "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    _Site.hits = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
async def renderer():
    settings = get_settings().model_copy(update={"browser_executable": _browser_executable(),
                                                 "crawler_timeout_seconds": 15})
    r = BrowserRenderer(settings, check_public=True)
    # The test site itself is on 127.0.0.1: allow exactly that host string. Anything else that resolves
    # to a private address (e.g. "localhost") still goes through the SSRF check and is blocked.
    r._host_ok["127.0.0.1"] = True
    if not r.available:
        pytest.skip("playwright not installed")
    try:
        async with r.session():
            await r._ensure_browser()
            yield r
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no launchable browser: {exc}")


async def test_renders_spa_and_follows_rendered_links(site, renderer):
    fetcher = WebFetcher(check_public=False, rendering="auto", renderer=renderer)
    pages = await fetcher.crawl(site, max_pages=5)
    by_path = {p.url.rstrip("/").removeprefix(site) or "/": p for p in pages}
    home = by_path["/"]
    assert home.rendered and "Book an appointment online" in home.text
    # The link only exists after JavaScript ran — the crawler found and rendered it.
    assert "/features" in by_path and "AI assistant" in by_path["/features"].text


async def test_browser_blocks_internal_requests_and_heavy_resources(site, renderer):
    page = await WebFetcher(check_public=False, rendering="always", renderer=renderer).fetch(site + "/")
    assert page is not None and page.rendered
    assert _Site.hits.get("/app.js", 0) >= 1
    assert _Site.hits.get("/internal-metadata", 0) == 0   # SSRF attempt via "localhost" was aborted
    assert _Site.hits.get("/logo.png", 0) == 0            # images are not downloaded


async def test_static_pages_are_not_rendered_in_auto_mode(renderer):
    import httpx

    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=STATIC, headers={"content-type": "text/html"}))
    calls = []
    original = renderer.render

    async def spy(url):
        calls.append(url)
        return await original(url)

    renderer.render = spy
    page = await WebFetcher(transport=transport, rendering="auto", renderer=renderer).fetch("https://acme.test/")
    assert page and not page.rendered and calls == []
