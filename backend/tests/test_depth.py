"""Analysis depth and navigation: positioning, workflows, cell evidence, competitor history, report links."""

import re

from cip.agents.reporting import anchor
from cip.core import positioning
from cip.core.evidence import EvidenceLedger
from cip.core.taxonomy import load_taxonomy
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_pipeline import run_all

PAGES = [
    {"url": "https://clinicly.example/", "title": "Clinicly", "description": "Scheduling software for physiotherapy clinics.",
     "headings": ["Clinicly", "Fill every appointment slot, automatically", "Trusted by 400 clinics"],
     "text": "Clinicly helps clinics run smoothly. Tired of chasing patients by phone? Automated reminders cut "
             "no-shows. Sign up in minutes. © 2026 Clinicly"},
    {"url": "https://clinicly.example/solutions", "title": "Solutions", "description": "",
     "headings": ["Solutions", "Physiotherapy practices", "Multi-site clinic groups"], "text": "Built for teams."},
]


def test_value_proposition_skips_the_brand_heading():
    ledger = EvidenceLedger()
    vp = positioning.value_proposition(PAGES, ledger, "Clinicly")
    assert vp["statement"] == "Fill every appointment slot, automatically"
    assert vp["supporting"] == "Scheduling software for physiotherapy clinics."
    assert ledger.get(vp["evidence_ids"][0]).extracted_text.startswith("Fill every appointment slot")


def test_use_cases_and_problems_are_verbatim():
    ledger = EvidenceLedger()
    cases = positioning.use_cases(PAGES, ledger, "Clinicly", positioning.audiences(
        [positioning._page_text(p) for p in PAGES]))
    names = [c["name"] for c in cases]
    assert "Physiotherapy practices" in names and "Multi-site clinic groups" in names
    problems = positioning.customer_problems(PAGES, ledger, "Clinicly")
    statements = [p["statement"] for p in problems]
    assert "Tired of chasing patients by phone?" in statements
    assert all("©" not in s for s in statements)
    for p in problems:  # every statement is a quote from the page it cites
        page = next(pg for pg in PAGES if pg["url"] == p["source_url"])
        assert p["statement"] in positioning._page_text(page)


def test_workflows_map_capabilities_and_mentions():
    ledger = EvidenceLedger()
    ev = ledger.add("Booking", "https://clinicly.example/", "website", 0.8)
    obs = {"workflow.scheduling": {"status": "available", "evidence_ids": [ev.id]},
           "comm.sms": {"status": "partial", "evidence_ids": []}}
    flows = {w["id"]: w for w in positioning.workflows(PAGES, obs, load_taxonomy(), ledger, "Clinicly")}
    book = flows["book_and_attend"]
    steps = {s["name"]: s["status"] for s in book["steps"]}
    assert steps["Book online"] == "supported" and steps["Receive reminders"] == "supported"
    assert steps["Give feedback"] == "not_identified" and "Give feedback" in book["next_steps"]
    signup = {s["name"]: s for s in flows["sign_up_and_start"]["steps"]}
    assert signup["Create an account"]["status"] == "mentioned" and signup["Create an account"]["mention"] == "sign up"
    assert "govern_and_comply" not in flows  # nothing visible: the workflow is not reported


async def test_pipeline_reports_positioning_workflows_and_cell_evidence(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    features = ctx.data("product_features")
    assert features["positioning"]["value_proposition"]["statement"]
    assert any(w["id"] == "book_and_attend" for w in features["workflows"])
    rows = ctx.data("feature_comparison")["rows"]
    with_client = [r for r in rows if r["client"] in ("available", "partial")]
    assert with_client and all(r["evidence"].get("client") for r in with_client)
    for r in rows:
        for ids in r["evidence"].values():
            assert all(i in ctx.ledger for i in ids)
    md = ctx.data("report")["markdown"]
    targets = set(re.findall(r"\]\(#([a-z0-9-]+)\)", md.split("**Contents:**")[1].split("\n")[0]))
    headings = {anchor(h) for h in re.findall(r"^## (.+)$", md, re.M)}
    assert len(targets) == 18 and targets <= headings


async def test_competitor_history_across_runs(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    gates = ["external_research", "repository_access", "client_report"]
    for _ in range(2):
        r = (await client.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": gates})).json()
        await runner.wait(r["run_id"])
    hist = (await client.get(f"/api/projects/{project_id}/competitor-history", headers=h)).json()
    assert len(hist["runs"]) == 2 and hist["runs"][0]["date"] <= hist["runs"][1]["date"]
    assert hist["runs"][0]["client_capabilities"] > 0
    assert hist["competitors"] and all(len(c["points"]) == 2 for c in hist["competitors"])
    top = hist["competitors"][0]
    assert top["current"] and top["appearances"] == 2 and top["feature_change"] == 0
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.get(f"/api/projects/{project_id}/competitor-history", headers=other)).status_code == 404
