"""Research layer: per-domain politeness, retries, failure states, shared cache, PDFs and domain checks."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from cip.connectors.research.cache import CachedPage, DbPageCache, MemoryPageCache
from cip.connectors.research.domain import check_domain, parked_signals
from cip.connectors.research.web import WebFetcher, extract_pdf
from cip.core import usage
from cip.db import session as db
from cip.services.runner import runner

from fakes import SITES, html, web_transport
from test_api import SAMPLE_CSV, client, register  # noqa: F401  (fixture)


def make_pdf(text: str) -> bytes:
    """A minimal one-page PDF with ``text`` (correct xref offsets)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def counting(handler):
    calls: list[str] = []
    if isinstance(handler, httpx.MockTransport):
        handler = handler.handle_request

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)
    return httpx.MockTransport(wrapped), calls


# --------------------------------------------------------------------------- retries & failure states


async def test_transient_errors_are_retried_and_persistent_ones_recorded():
    attempts = {"n": 0}

    def flaky(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/flaky":
            attempts["n"] += 1
            return httpx.Response(503) if attempts["n"] < 3 else httpx.Response(200, text=html("OK", "<p>fine</p>"),
                                                                                 headers={"content-type": "text/html"})
        if request.url.path == "/down":
            return httpx.Response(502)
        if request.url.path == "/slow":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(404)

    f = WebFetcher(transport=httpx.MockTransport(flaky))
    meter = usage.UsageMeter()
    token = usage.activate(meter)
    agent = usage.set_agent("client_research")
    try:
        assert (await f.fetch("https://site.example/flaky")).title == "OK"
        assert attempts["n"] == 3  # two retries
        assert await f.fetch("https://site.example/down") is None
        assert await f.fetch("https://site.example/slow") is None
        assert await f.fetch("https://site.example/missing") is None  # a 404 is an answer, not a failure
    finally:
        usage.reset_agent(agent)
        usage.deactivate(token)
    reasons = {x["url"].rsplit("/", 1)[-1]: (x["reason"], x["attempts"]) for x in f.failures}
    assert reasons == {"down": ("http_502", 3), "slow": ("timeout", 3)}
    u = meter.agent_usage("client_research")
    assert u["web_failures"] == 2 and {x["reason"] for x in u["failures"]} == {"http_502", "timeout"}


async def test_budget_exhaustion_is_not_retried():
    transport, calls = counting(lambda r: httpx.Response(200, text=html("x", ""), headers={"content-type": "text/html"}))
    f = WebFetcher(transport=transport)
    meter = usage.UsageMeter(web_request_budget=1)
    token = usage.activate(meter)
    try:
        assert await f.fetch("https://a.example/one") is None  # robots.txt uses the only request
    finally:
        usage.deactivate(token)
    assert len(calls) == 1 and f.failures[-1]["reason"] == "budget_exhausted"


async def test_requests_to_one_domain_are_limited(monkeypatch):
    monkeypatch.setenv("CIP_CRAWLER_DOMAIN_CONCURRENCY", "2")
    from cip.config import get_settings
    get_settings.cache_clear()
    live = {"a.example": 0, "b.example": 0}
    peak = {"a.example": 0, "b.example": 0}

    async def handler(request):
        host = request.url.host
        live[host] += 1
        peak[host] = max(peak[host], live[host])
        await asyncio.sleep(0.01)
        live[host] -= 1
        return httpx.Response(200, text=html("p", ""), headers={"content-type": "text/html"})

    f = WebFetcher(transport=httpx.MockTransport(handler))
    await asyncio.gather(*(f.fetch(f"https://{h}/p{i}") for h in live for i in range(8)))
    get_settings.cache_clear()
    assert peak == {"a.example": 2, "b.example": 2}


# --------------------------------------------------------------------------- cache


async def test_cache_is_shared_between_fetchers_and_keeps_the_fetch_date():
    cache = MemoryPageCache()
    transport, calls = counting(web_transport())
    first = await WebFetcher(transport=transport, cache=cache).fetch("https://abc-healthcare.com/")
    assert first and not first.cached and calls
    calls.clear()
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    for item in cache.items.values():
        item.fetched_at = old

    second = WebFetcher(transport=transport, cache=cache)
    meter = usage.UsageMeter()
    token = usage.activate(meter)
    try:
        again = await second.fetch("https://abc-healthcare.com/")
    finally:
        usage.deactivate(token)
    assert again.cached and again.fetched_at == old and again.title == first.title and calls == []
    assert second.cached_at[again.url] == old and meter.totals["cache_hits"] == 1

    # A refresh fetches fresh and updates the cache.
    fresh = await WebFetcher(transport=transport, cache=cache, refresh=True).fetch("https://abc-healthcare.com/")
    assert not fresh.cached and calls
    assert (await cache.get("https://abc-healthcare.com/")).fetched_at > old
    # Expired entries are ignored.
    assert await MemoryPageCache(ttl_hours=1).get("https://abc-healthcare.com/") is None


async def test_db_cache_round_trip_and_org_scoping(client):  # noqa: F811
    await register(client)
    from cip.db.models import Organization
    from sqlalchemy import select
    async with db.sessionmaker()() as s:
        org_id = (await s.execute(select(Organization.id))).scalar_one()
    cache = DbPageCache(org_id, 24)
    await cache.put(CachedPage(url="https://x.example/", final_url="https://x.example/", status=200, kind="html",
                               body=html("Cached", "<p>hi</p>"), title="Cached"))
    hit = await cache.get("https://x.example/")
    assert hit.title == "Cached" and hit.fetched_at.tzinfo is not None
    assert await DbPageCache("org_other", 24).get("https://x.example/") is None
    await cache.put(CachedPage(url="https://x.example/", final_url="https://x.example/", status=200, kind="html",
                               body="<html></html>", title="Replaced"))
    assert (await cache.get("https://x.example/")).title == "Replaced"
    from cip.connectors.research.cache import purge_expired
    assert await purge_expired(24, now=datetime.now(timezone.utc) + timedelta(days=2)) == 1


async def test_second_run_reuses_research_and_dates_evidence_by_fetch(client):  # noqa: F811
    h = await register(client)
    up = (await client.post("/api/uploads", headers=h, files={"file": ("c.csv", SAMPLE_CSV, "text/csv")})).json()
    project_id = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})).json()["created_projects"][0]
    cache = MemoryPageCache()
    previous = runner.context_hook

    def hook(ctx):
        previous(ctx)
        ctx.fetcher = WebFetcher(transport=web_transport(), cache=cache)

    runner.context_hook = hook
    try:
        gates = ["external_research", "repository_access", "client_report"]
        first = (await client.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": gates})).json()
        await runner.wait(first["run_id"])
        earlier = datetime.now(timezone.utc) - timedelta(hours=5)
        for item in cache.items.values():
            item.fetched_at = earlier
        second = (await client.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": gates})).json()
        await runner.wait(second["run_id"])
    finally:
        runner.context_hook = previous
    run = (await client.get(f"/api/runs/{second['run_id']}", headers=h)).json()
    assert run["status"] in ("completed", "completed_with_errors")
    assert run["usage"]["cache_hits"] > 0
    evidence = (await client.get(f"/api/runs/{second['run_id']}/evidence?source_type=website", headers=h)).json()
    site = [e for e in evidence if e["source_url"].startswith("https://abc-healthcare.com")]
    def when(e):
        return datetime.fromisoformat(e["collected_at"]).replace(tzinfo=timezone.utc)
    # Pages read from the cache date their evidence by the original fetch; pages fetched live (the passive
    # security checks) are dated now.
    cached = [e for e in site if not e["claim"].startswith("Security:")]
    live = [e for e in site if e["claim"].startswith("Security:")]
    assert cached and all(abs(when(e) - earlier) < timedelta(seconds=5) for e in cached)
    assert live and all(when(e) > earlier + timedelta(hours=4) for e in live)


# --------------------------------------------------------------------------- PDFs


def test_pdf_text_extraction():
    title, text = extract_pdf(make_pdf("Pro plan 49 USD per month"), max_pages=5)
    assert "Pro plan 49 USD per month" in text


async def test_crawl_reads_product_pdfs_but_not_others():
    sites = {
        "https://pdfco.example/": html("PDF Co", "<p>Home</p>", links=["/pricing-sheet.pdf", "/terms.pdf", "/about"]),
        "https://pdfco.example/about": html("About", "<p>About us</p>"),
    }
    pdf = make_pdf("Enterprise tier includes SSO and audit logs")

    def handler(request):
        if request.url.path == "/pricing-sheet.pdf":
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        if request.url.path == "/terms.pdf":
            raise AssertionError("terms.pdf should not be fetched")
        return web_transport(sites).handle_request(request)

    f = WebFetcher(transport=httpx.MockTransport(handler))
    pages = await f.crawl("https://pdfco.example/", max_pages=10)
    pdfs = [p for p in pages if p.content_type == "pdf"]
    assert len(pdfs) == 1 and "SSO and audit logs" in pdfs[0].text and pdfs[0].url.endswith("pricing-sheet.pdf")


async def test_unreadable_pdf_is_recorded_not_raised():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"%PDF-1.4 not really", headers={"content-type": "application/pdf"})

    f = WebFetcher(transport=httpx.MockTransport(handler))
    assert await f.fetch("https://x.example/brochure.pdf") is None
    assert f.failures[-1]["reason"] == "unreadable_pdf"


# --------------------------------------------------------------------------- domain checks

PARKED = html("example-health.com", "<h1>example-health.com</h1><p>This domain is for sale! Make an offer on this "
                                    "domain. Related searches: health insurance</p>")


def domain_sites():
    return {
        **SITES,
        "https://parked-health.example/": PARKED,
        "https://renamed.example/": "REDIRECT:https://newbrand.example/",
        "https://newbrand.example/": html("New Brand", "<p>We are New Brand</p>"),
    }


def domain_transport():
    sites = domain_sites()
    base = web_transport(sites)

    def handler(request):
        url = str(request.url)
        body = sites.get(url if url.count("/") > 2 else url + "/")
        if isinstance(body, str) and body.startswith("REDIRECT:"):
            return httpx.Response(301, headers={"location": body[9:]})
        if request.url.host == "gone.example":
            raise httpx.ConnectError("refused", request=request)
        return base.handle_request(request)
    return httpx.MockTransport(handler)


async def test_domain_classification():
    f = WebFetcher(transport=domain_transport())
    assert (await check_domain(f, "abc-healthcare.com"))["status"] == "ok"
    parked = await check_domain(f, "parked-health.example")
    assert parked["status"] == "parked" and "for sale" in parked["detail"]
    moved = await check_domain(f, "renamed.example")
    assert moved["status"] == "redirected" and moved["detail"] == "Redirects to newbrand.example"
    gone = await check_domain(f, "gone.example")
    assert gone["status"] == "unreachable" and gone["detail"] == "connection error"
    assert (await check_domain(f, "https://abc-healthcare.com/nope"))["status"] == "unreachable"
    assert parked_signals(html("Shop", "<p>Related searches</p>")) == []


async def test_upload_preview_domain_checks(client, monkeypatch):  # noqa: F811
    from cip.api.routes import uploads
    monkeypatch.setattr(uploads, "domain_fetcher", lambda: WebFetcher(transport=domain_transport()))
    h = await register(client)
    csv = ("Client Name,Project Name,Project URL\n"
           "ABC Healthcare,Portal,https://abc-healthcare.com\n"
           "Parked Health,Site,https://parked-health.example\n"
           "Renamed Co,App,https://renamed.example\n"
           "Gone Ltd,App,https://gone.example\n")
    up = (await client.post("/api/uploads", headers=h, files={"file": ("c.csv", csv, "text/csv")})).json()
    checked = await client.post(f"/api/uploads/{up['id']}/check-domains", headers=h)
    assert checked.status_code == 200, checked.text
    by_name = {r["client"]["name"]: r["domain_check"]["status"] for r in checked.json()["records"]}
    assert by_name == {"ABC Healthcare": "ok", "Parked Health": "parked", "Renamed Co": "redirected",
                       "Gone Ltd": "unreachable"}
    # The results persist with the upload and travel with imported projects.
    again = (await client.get(f"/api/uploads/{up['id']}", headers=h)).json()
    assert again["domain_checks"] and again["records"][1]["domain_check"]["status"] == "parked"
    await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})
    projects = (await client.get("/api/projects", headers=h)).json()
    assert {p["record"]["domain_check"]["status"] for p in projects} == {"ok", "parked", "redirected", "unreachable"}
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.post(f"/api/uploads/{up['id']}/check-domains", headers=other)).status_code == 404


async def test_client_research_reports_the_domain_check(make_ctx):
    from cip.agents.client_research import ClientResearchAgent
    ctx = make_ctx(fetcher=WebFetcher(transport=domain_transport()))
    ctx.record.client.domain = "parked-health.example"
    result = await ClientResearchAgent().run(ctx)
    assert result.data["domain_check"]["status"] == "parked"
    assert any("parked" in f.title for f in result.findings)
