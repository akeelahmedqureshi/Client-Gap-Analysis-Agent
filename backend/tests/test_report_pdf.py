"""PDF export: HTML conversion, offline rendering, injected-markup safety, API endpoint."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_api import client, register, upload_and_import  # noqa: F401  (fixture re-export)
from test_browser import _browser_executable

from cip.config import get_settings
from cip.services import report_pdf
from cip.services.runner import runner

MD = """# Client Intelligence Report — ACME
| Feature | Client | Rival |
|---|---|---|
| SSO | ❌ | ✅ |
| Dashboard | 🟡 | ✅ |

<details><summary>Rejected</summary>

- Random News: not comparable

</details>
"""


def test_report_html_tables_icons_and_details():
    html = report_pdf.report_html(MD, "ACME report")
    assert "<table>" in html and "<th>Feature</th>" in html
    assert 'class="dot missing"' in html and 'class="dot ok"' in html and "✅" not in html
    assert "<details open>" in html
    assert "<title>ACME report</title>" in html


@pytest.fixture
def pdf_settings():
    s = get_settings().model_copy(update={"browser_executable": _browser_executable()})
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright not installed")
    return s


async def test_render_pdf(pdf_settings):
    try:
        pdf = await report_pdf.render_pdf(report_pdf.report_html(MD * 30, "ACME"), "ACME", pdf_settings)
    except report_pdf.PdfUnavailable as exc:
        pytest.skip(str(exc))
    assert pdf.startswith(b"%PDF") and len(pdf) > 5_000


class _Beacon(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        _Beacon.hits += 1
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


async def test_injected_markup_cannot_execute_or_fetch(pdf_settings):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Beacon)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    _Beacon.hits = 0
    # Text scraped from a hostile website ends up in the report:
    hostile = (f"Competitor description <img src='http://127.0.0.1:{port}/img'> "
               f"<script>fetch('http://127.0.0.1:{port}/js')</script>"
               f"<link rel=stylesheet href='http://127.0.0.1:{port}/css'>")
    try:
        await report_pdf.render_pdf(report_pdf.report_html(MD + hostile, "x"), "x", pdf_settings)
    except report_pdf.PdfUnavailable as exc:
        pytest.skip(str(exc))
    finally:
        server.shutdown()
        server.server_close()
    assert _Beacon.hits == 0


async def test_pdf_endpoint(client, monkeypatch):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={
        "project_id": project_id,
        "approve_gates": ["external_research", "repository_access", "client_report"]})).json()["run_id"]
    await runner.wait(run_id)

    async def fake_render(html, title, settings=None):
        assert "Evidence Appendix" in html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr("cip.api.routes.runs.render_pdf", fake_render)
    r = await client.get(f"/api/runs/{run_id}/report.pdf", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    audit = (await client.get("/api/audit?action=report.", headers=h)).json()
    assert audit and audit[0]["details"] == {"format": "pdf"}

    async def unavailable(html, title, settings=None):
        raise report_pdf.PdfUnavailable("no browser")

    monkeypatch.setattr("cip.api.routes.runs.render_pdf", unavailable)
    r = await client.get(f"/api/runs/{run_id}/report.pdf", headers=h)
    assert r.status_code == 503 and "no browser" in r.json()["detail"]
    other = await register(client, org="Other Co", email="boss@other-co.com")
    assert (await client.get(f"/api/runs/{run_id}/report.pdf", headers=other)).status_code == 404


async def test_report_lists_render_as_lists_in_strict_markdown(make_ctx):
    from test_pipeline import run_all

    ctx = make_ctx()
    await run_all(ctx)
    html = report_pdf.report_html(ctx.data("report")["markdown"], "r")
    summary = html.split("<h2>1. Executive Summary</h2>")[1].split("<h2>")[0]
    assert summary.count("<li>") >= 6  # key findings + major opportunities are real list items
