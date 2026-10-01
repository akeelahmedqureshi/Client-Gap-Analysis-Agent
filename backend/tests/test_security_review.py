"""Passive security review: site posture, OSV dependency advisories, code patterns, score, gaps."""

import httpx

from cip.connectors.research.security import code_issues, probe_site, score
from cip.connectors.research.web import WebFetcher
from test_pipeline import run_all


async def test_well_configured_site_has_no_web_findings():
    good = [("content-type", "text/html"), ("strict-transport-security", "max-age=31536000"),
            ("content-security-policy", "default-src 'self'; frame-ancestors 'none'"),
            ("x-content-type-options", "nosniff"), ("referrer-policy", "strict-origin-when-cross-origin"),
            ("permissions-policy", "camera=()"), ("server", "nginx"),
            ("set-cookie", "sid=1; Secure; HttpOnly; SameSite=Lax")]

    def handler(req):
        url = str(req.url)
        if url.startswith("http://"):
            return httpx.Response(301, headers={"location": url.replace("http://", "https://")})
        if url.endswith("/.well-known/security.txt"):
            return httpx.Response(200, text="Contact: mailto:security@good.io\\nExpires: 2027-01-01T00:00:00Z")
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        return httpx.Response(200, text="<html>ok</html>", headers=good)

    issues, summary = await probe_site(WebFetcher(transport=httpx.MockTransport(handler)), "https://good.io")
    assert issues == [] and summary["http_redirects_to_https"] and summary["security_txt"]


def test_code_patterns_and_score():
    files = {"api/app.py": "DEBUG = True\nrequests.get(u, verify=False)\ncur.execute(f\"SELECT * FROM t WHERE id={x}\")\n",
             "README.md": "eval( is fine in docs"}
    issues = code_issues(files, lambda p, line: f"https://repo/{p}#L{line}")
    keys = {i.key.split(":")[1] for i in issues}
    assert keys == {"debug-on", "tls-verify-off", "sql-concat"}       # README ignored
    assert next(i for i in issues if "tls" in i.key).line_range == "2"
    s = score(issues)
    assert s["score"] == 100 - 12 - 12 - 5 and s["grade"] == "C"


async def test_security_review_end_to_end(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    sec = ctx.data("security_review")
    keys = {i["key"] for i in sec["issues"]}
    assert {"header:strict-transport-security", "header:content-security-policy", "header:x-frame-options",
            "https-redirect", "security-txt", "cookie:sid", "disclosure:server"} <= keys
    assert "header:x-content-type-options" not in keys                       # that header is present
    assert "disclosure:x-powered-by" not in keys                              # no version number disclosed
    dep = next(i for i in sec["issues"] if i["category"] == "dependency")
    assert dep["severity"] == "high" and "CVE-2099-0001" in dep["references"] and "4.19.2" in dep["recommendation"]
    assert "lower bound" in dep["detail"]                                     # ^4.18.0 is a range
    assert ctx.ledger.get(dep["evidence_id"]).source_type == "advisory"
    code = {i["key"].split(":")[1] for i in sec["issues"] if i["category"] == "code"}
    assert {"cors-wildcard", "weak-hash"} <= code
    assert sec["grade"] in "ABCDF" and 0 <= sec["score"] < 100 and sec["scope"]

    gaps = {g["name"]: g for g in ctx.data("gap_analysis")["gaps"] if g["gap_type"] == "security"}
    assert {"HTTPS enforcement", "Vulnerable dependency remediation", "Secure coding fixes",
            "Web security hardening (headers & cookies)"} <= set(gaps)
    for g in gaps.values():
        assert g["evidence_ids"] and all(e in ctx.ledger for e in g["evidence_ids"])
    md = ctx.data("report")["markdown"]
    assert "### Security review" in md and "CVE-2099-0001" in md


async def test_security_review_respects_rejected_external_research(make_ctx):
    from fakes import SITES, web_transport

    calls = []
    transport = web_transport(SITES)

    async def spy(request):
        calls.append(str(request.url))
        return await transport.handle_async_request(request)

    ctx = make_ctx(approvals=("repository_access", "client_report"),
                   fetcher=WebFetcher(transport=httpx.MockTransport(spy)))
    from cip.agents.orchestrator import Orchestrator
    from cip.core.schemas import AgentStatus
    from test_pipeline import MemStore

    statuses = {"client_research": AgentStatus.SKIPPED}  # external research rejected
    await Orchestrator(MemStore(), retry_delay=0).run(ctx, statuses)
    assert not any("abc-healthcare.com" in u for u in calls)   # the client's site was never contacted
    assert ctx.data("security_review")["site"] == {}


async def test_same_advisory_in_two_repos_is_one_finding(make_ctx):
    from cip.core.schemas import NormalizedRecord

    ctx = make_ctx()
    rec = NormalizedRecord.model_validate(ctx.record.model_dump())
    rec.sources.github.append("https://github.com/abc/mobile-app")  # second repo with the same manifest
    ctx.record = rec
    await run_all(ctx)
    sec = ctx.data("security_review")
    deps = [i for i in sec["issues"] if i["category"] == "dependency"]
    assert len(deps) == 1 and "abc/project, abc/mobile-app" in deps[0]["detail"]
    code = [i for i in sec["issues"] if i["category"] == "code"]
    assert {"abc/project", "abc/mobile-app"} <= {t for i in code for t in ("abc/project", "abc/mobile-app") if t in i["title"]}
    assert len({i["key"] for i in code}) == len(code)
