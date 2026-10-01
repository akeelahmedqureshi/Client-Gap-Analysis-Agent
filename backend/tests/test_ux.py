"""UX deep dive: static WCAG/mobile/conversion checks, real-browser checks, scoring, agent end to end."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cip.config import get_settings
from cip.connectors.research.ux import (
    BrowserUxAuditor,
    browser_issues,
    conversion_issues,
    detect_practices,
    score,
    static_checks,
)

from test_browser import _browser_executable

BAD = """<html><head><title></title><meta name="viewport" content="width=device-width, user-scalable=no"></head>
<body><h2>Welcome</h2><h4>Sub</h4><img src="/hero.png"><img src="/a.png" alt="">
<form><input type="email" placeholder="Email"><input type="hidden" name="t"><button><svg></svg></button></form>
<a href="/x"><i class="icon"></i></a><a href="/a">Read more</a><a href="/b">Click here</a>
<div style="width:1400px">wide</div><p style="color:#bbb">Faint text that fails contrast</p>
<a href="/t" style="font-size:9px">t</a><a href="/u" style="font-size:9px">u</a><a href="/v" style="font-size:9px">v</a>
</body></html>"""

GOOD = """<html lang="en"><head><title>Acme — Clinic scheduling</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>input, button { min-height: 44px; min-width: 44px; }</style></head>
<body><main><h1>Acme</h1><h2>Book online</h2><img src="/hero.png" alt="Calendar screenshot">
<form><label for="e">Email</label><input id="e" type="email"><button aria-label="Search">🔍</button>
<input type="search" aria-label="Search the site"></form>
<a href="/signup" style="display:inline-block;padding:12px">Start free trial</a>
<a href="/login" style="display:inline-block;padding:12px">Log in</a>
<a href="/help" style="display:inline-block;padding:12px">Help center</a>
<p>Trusted by 2,000 clinics. HIPAA compliant.</p></main>
<script src="https://widget.intercom.io/widget/abc"></script></body></html>"""


def test_static_checks_flag_wcag_basics():
    keys = {i.key: i for i in static_checks("https://a.test/", BAD)}
    assert {"html-lang", "document-title", "zoom-disabled", "image-alt", "form-label", "button-name", "link-name",
            "vague-links", "no-h1", "heading-order", "landmark-main"} <= set(keys)
    assert keys["image-alt"].count == 1  # alt="" (decorative) is fine
    assert keys["form-label"].count == 1  # hidden inputs are not fields
    assert keys["image-alt"].wcag == "1.1.1" and keys["image-alt"].examples[0].startswith("<img")
    assert static_checks("https://a.test/", GOOD) == []


def test_missing_viewport_is_a_mobile_issue():
    issues = static_checks("https://a.test/", "<html lang='en'><head><title>x</title></head><body><main><h1>x</h1>"
                                              "</main></body></html>")
    assert [(i.key, i.category, i.severity) for i in issues] == [("meta-viewport", "mobile", "high")]


def test_practices_and_conversion_issues():
    good = detect_practices("https://a.test/", GOOD)
    assert all(good[k] for k in ("self_serve_cta", "sign_in", "help", "live_chat", "trust", "search"))
    assert not good["sales_cta"]
    assert conversion_issues("https://a.test/", good) == []
    bad = detect_practices("https://b.test/", BAD)
    assert {i.key for i in conversion_issues("https://b.test/", bad)} == {"primary-cta", "help-link", "trust-signals"}


def test_scoring_counts_each_check_once_and_only_measured_categories():
    issues = static_checks("https://a.test/", BAD) + static_checks("https://a.test/2", BAD)
    s = score(issues, {"accessibility", "mobile"})
    assert set(s["categories"]) == {"accessibility", "mobile"} and s["categories"]["mobile"] == 100
    single = score(static_checks("https://a.test/", BAD), {"accessibility"})
    assert s["categories"]["accessibility"] == single["categories"]["accessibility"]  # not double-penalised


# ---------------------------------------------------------------- real browser


class _Site(BaseHTTPRequestHandler):
    pages = {"/bad": BAD, "/good": GOOD}

    def do_GET(self):  # noqa: N802
        body = self.pages.get(self.path.split("?")[0])
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if body:
            self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def auditor():
    settings = get_settings().model_copy(update={"browser_executable": _browser_executable(),
                                                 "crawler_timeout_seconds": 15})
    a = BrowserUxAuditor(settings, check_public=True)
    a.renderer._host_ok["127.0.0.1"] = True
    if not a.available:
        pytest.skip("playwright not installed")
    try:
        async with a.session():
            await a.renderer._ensure_browser()
            yield a
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no launchable browser: {exc}")


async def test_browser_audit_finds_contrast_overflow_and_small_targets(site, auditor):
    bad = await auditor.audit(site + "/bad")
    keys = {i.key: i for i in browser_issues(bad)}
    assert "color-contrast" in keys and "Faint text" in keys["color-contrast"].examples[0]
    assert "mobile-overflow" in keys and "tap-targets" in keys
    assert bad["performance"]["requests"] >= 1 and bad["performance"]["dcl"] is not None
    good = await auditor.audit(site + "/good")
    assert {i.key for i in browser_issues(good)} == set()


# ---------------------------------------------------------------- agent end to end

from contextlib import asynccontextmanager  # noqa: E402

from test_pipeline import run_all  # noqa: E402


class FakeAuditor:
    """In-browser results without a browser: the client's pages fail contrast and are slow."""

    available = True

    def __init__(self):
        self.audited: list[str] = []

    @asynccontextmanager
    async def session(self):
        yield self

    async def audit(self, url):
        self.audited.append(url)
        bad = "abc-healthcare.com" in url
        return {"url": url,
                "desktop": {"contrast": [{"text": "Book now", "ratio": 2.1, "color": "rgb(170, 170, 170)",
                                          "background": "rgb(255, 255, 255)", "html": "<a>Book now</a>"}] if bad else [],
                            "contrastCount": 1 if bad else 0, "contrastChecked": 40},
                "mobile": {"overflow": False, "scrollWidth": 390, "viewportWidth": 390, "targets": [], "targetCount": 0,
                           "smallText": [], "smallTextCount": 0},
                "performance": {"lcp": 5200 if bad else 1200, "dcl": 900, "load": 2000, "requests": 40,
                                "bytes": 1_500_000}}


async def test_ux_review_end_to_end_static(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    d = ctx.data("ux_review")
    assert "simulated" in d["notes"][0]  # no browser against the fake web
    issues = {i["key"]: i for i in d["issues"]}
    assert len(issues["meta-viewport"]["pages"]) == 4  # one issue, every page listed
    ev = ctx.ledger.get(issues["image-alt"]["evidence_id"])
    assert ev.source_url == "https://abc-healthcare.com" and "<img" in ev.extracted_text
    gaps = {g["name"]: g for g in d["gaps"]}
    assert {"Accessibility (WCAG 2.2 AA) fixes", "Mobile-friendly responsive layout", "Clear primary call to action",
            "Help center / FAQ", "Live chat support on the website"} <= set(gaps)
    chat = gaps["Live chat support on the website"]
    assert len(chat["competitors_with"]) == 1 and "intercom" in ctx.ledger.get(chat["evidence_ids"][0]).extracted_text
    assert "Self-serve sign-up entry point" not in gaps  # covered by the missing-CTA / free-trial gaps
    assert "UX quality below competitors" not in gaps    # a summary finding, not a roadmap item
    assert any(f.title.startswith("UX quality below competitors") for f in ctx.outputs["ux_review"].findings)
    merged = [g for g in ctx.data("gap_analysis")["gaps"] if g["feature_id"] == "ux.accessibility"]
    assert len(merged) == 1
    recs = {r["feature"]: r for r in ctx.data("opportunity_prioritization")["recommendations"]}
    assert "Clear primary call to action" in recs
    plan = next(p for p in ctx.data("enhancement_planning")["plans"] if p["feature"] == "Clear primary call to action")
    assert any("call to action" in x for x in plan["frontend_changes"])


async def test_ux_review_with_browser_checks(make_ctx):
    auditor = FakeAuditor()
    ctx = make_ctx(ux_auditor=auditor)
    await run_all(ctx)
    d = ctx.data("ux_review")
    assert d["notes"] == [] and d["client"]["browser"] and "performance" in d["client"]["score"]["categories"]
    assert "https://medibook.io" in auditor.audited
    gaps = {g["name"]: g for g in d["gaps"]}
    assert "1.4.3" in gaps["Accessibility (WCAG 2.2 AA) fixes"]["description"]
    assert "Largest contentful paint 5.2s" in gaps["Page speed (Core Web Vitals)"]["description"]


async def test_ux_review_skipped_without_external_research(make_ctx):
    ctx = make_ctx(approvals=("repository_access", "client_report"))
    await run_all(ctx)
    assert ctx.outputs.get("ux_review") is None or ctx.outputs["ux_review"].status.value == "skipped"


def test_ux_changes_between_runs():
    from cip.services.changes import diff_outputs

    def run(overall, issues, chat):
        return {"ux_review": {
            "client": {"browser": False, "score": {"overall": overall}},
            "issues": [{"key": k, "severity": sev, "title": k, "category": "accessibility", "evidence_id": f"ev_{k}"}
                       for k, sev in issues],
            "companies": [{"name": "ABC", "is_client": True},
                          {"name": "MediBook", "is_client": False, "practices": {"live_chat": chat}}]}}

    changes = {c["kind"]: c for c in diff_outputs(run(90, [("html-lang", "medium")], None),
                                                  run(72, [("image-alt", "high")], "ev_chat"))}
    assert changes["ux_score"]["severity"] == "warning" and changes["ux_score"]["after"] == 72
    assert changes["ux_issue_new"]["severity"] == "warning" and changes["ux_issue_resolved"]["title"].endswith("html-lang")
    assert changes["competitor_ux_practice"]["title"] == "MediBook added live chat to its website"


async def test_ux_report_section(make_ctx):
    ctx = make_ctx(ux_auditor=FakeAuditor())
    await run_all(ctx)
    md = ctx.data("report")["markdown"]
    section = md[md.index("### UX review"):md.index("## 4.")]
    assert "| **ABC Healthcare (client)** |" in section and "| Live chat widget |" in section
    assert "4 text element(s) below the WCAG contrast minimum" in section  # merged across the 4 pages
    assert "<meta" not in section and "&lt;meta" in section  # quoted markup is escaped
    from cip.services.report_pdf import report_html
    assert "<meta name=\"viewport\"" not in report_html(section, "t").split("</head>", 1)[1]
