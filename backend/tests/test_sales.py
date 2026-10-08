"""Sales intelligence summary and outreach email: claim safety, LLM fallback, review workflow (BRS 7.19-7.20)."""

from cip.core.outreach import Fact, OutreachInput, check_claims
from cip.services.runner import runner

from fakes import ScriptedLLM, sites_with_client_app, web_transport
from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_pipeline import run_all
from test_security_api import add_user, login

KB = [
    {"id": "kb_case", "kind": "case_study", "title": "Workflow automation for a clinic network",
     "capability_tags": ["ai.automation"], "industries": ["Healthcare"], "status": "approved",
     "visibility": "client_facing", "reference_allowed": True, "automation": True, "customer_name": "CareCo",
     "outcomes": "Admin time down 40%", "version": 1},
    {"id": "kb_secret", "kind": "capability", "title": "Confidential chatbot accelerator",
     "capability_tags": ["ai.assistant"], "status": "approved", "visibility": "internal", "version": 1,
     "customer_name": "BankCo"},
]


def _ctx(make_ctx, **kw):
    from cip.connectors.research.web import WebFetcher
    return make_ctx(knowledge=KB, fetcher=WebFetcher(transport=web_transport(sites_with_client_app())), **kw)


async def test_sales_summary_is_claim_safe_and_consistent_with_the_analysis(make_ctx):
    ctx = _ctx(make_ctx)
    status, statuses, _ = await run_all(ctx)
    assert status == "completed"
    s = ctx.data("sales_intelligence")
    gaps = {g["id"]: g for g in ctx.data("gap_analysis")["gaps"]}

    assert 1 <= len(s["top_gaps"]) <= 3
    for g in s["top_gaps"]:
        assert g["confidence"] >= 0.5 and g["competitors"], "only evidenced, confident gaps lead"
        assert gaps[g["gap_id"]]["gap_type"] not in ("security", "technology")
        assert "not publicly identified" in g["statement"] and "lack" not in g["statement"]
        assert g["evidence_ids"]
    recs = {r["id"] for r in ctx.data("opportunity_prioritization")["recommendations"]}
    assert {i["recommendation_id"] for i in s["top_improvements"]} <= recs
    assert len(s["top_improvements"]) == 3

    # Customer pain from public reviews is usable; security findings are internal only.
    pains = {p["source"]: p for p in s["pain_points"]}
    assert not pains["app_reviews"]["internal_only"] and "reviews" in pains["app_reviews"]["text"]
    assert pains["security"]["internal_only"]
    assert s["ai_opportunity"] and s["automation_opportunity"] and s["revenue_opportunity"]

    # Knowledge base: client-facing and internal matches are kept apart.
    assert [c["title"] for c in s["relevant_capabilities"]] == ["Workflow automation for a clinic network"]
    assert [c["title"] for c in s["internal_capabilities"]] == ["Confidential chatbot accelerator"]
    assert s["case_studies"][0]["customer"] == "CareCo"
    assert "CareCo" not in str(s["internal_capabilities"])
    assert s["contact"]["email"]
    assert "30-minute" in s["next_step"]

    # Outreach facts never carry internal knowledge or security topics.
    inp = s["outreach_input"]
    assert "Confidential chatbot accelerator" in inp["forbidden_terms"] and "BankCo" in inp["forbidden_terms"]
    assert "Confidential" not in str(inp["facts"]) and "security" not in str(inp["facts"]).lower()


async def test_outreach_template_without_llm_passes_the_claim_check(make_ctx):
    ctx = _ctx(make_ctx)
    await run_all(ctx)
    o = ctx.data("outreach")
    assert o["generated_by"] == "template" and o["problems"] == []
    assert o["to"] and o["subject"]
    body = o["body"]
    assert body.startswith("Hi ") and "[Your name]" in body and "30-minute call" in body
    assert "could not find publicly" in body and "Confidential" not in body and "BankCo" not in body
    assert "CareCo" in body, "approved, referenceable case study is used"


async def test_outreach_llm_draft_is_retried_then_replaced_when_it_makes_unsupported_claims(make_ctx):
    calls = []

    def bad_then_good(user):
        calls.append(user)
        if len(calls) == 1:
            return {"subject": "Grow 300% with us", "body": "Hi team, we can triple revenue. [Your name]"}
        return {"subject": "An idea for your scheduling product",
                "body": "Hi ABC Healthcare team,\n\nMediBook and ClinicFlow offer a free trial that we could not find "
                        "for ABC Patient Management. Would a 30-minute call help?\n\n[Your name]\n[Your organization]"}

    ctx = _ctx(make_ctx, llm=ScriptedLLM({"EmailDraft": bad_then_good}))
    await run_all(ctx)
    o = ctx.data("outreach")
    assert o["generated_by"] == "llm" and o["problems"] == []
    assert "numbers that are not in the facts: 300" in o["notes"][0]
    assert "rejected" in calls[1], "the retry tells the model what was wrong"

    always_bad = ScriptedLLM({"EmailDraft": {"subject": "Hi", "body": "Our Confidential chatbot accelerator…"}})
    ctx = _ctx(make_ctx, llm=always_bad)
    await run_all(ctx)
    o = ctx.data("outreach")
    assert o["generated_by"] == "template" and len(o["notes"]) == 2 and "Confidential" not in o["body"]
    assert "Confidential chatbot accelerator" not in always_bad.calls[0][1], "internal knowledge never reaches the LLM"


def test_claim_check_flags_unsupported_content():
    inp = OutreachInput(client="ABC", product="Scheduler", industry="Healthcare",
                        facts=[Fact("gap", "MediBook offers SMS reminders (1 of 2 competitors analysed).")],
                        competitors_allowed=["MediBook"], competitors_all=["MediBook", "ClinicFlow"],
                        forbidden_terms=["Secret Accelerator", "BankCo"], top_gaps=["SMS reminders"],
                        opportunity=None, capability=None)
    assert check_claims("SMS reminders", "MediBook offers SMS reminders. 1 of 2 competitors. 30-minute call?", inp) == []
    problems = check_claims("Hello", "ClinicFlow and our Secret Accelerator for BankCo cut costs 45%. "
                                     "Your site has a security vulnerability.", inp)
    text = " | ".join(problems)
    assert "Secret Accelerator" in text and "BankCo" in text and "ClinicFlow" in text
    assert "security" in text and "45" in text


async def test_sales_review_workflow_api(client):  # noqa: F811
    h = await register(client)
    await add_user(client, h, "viewer@acme-consulting.com", "viewer")
    viewer = {"Authorization": f"Bearer {(await login(client, 'viewer@acme-consulting.com')).json()['access_token']}"}
    await client.post("/api/knowledge", headers=h, json={
        "kind": "capability", "title": "Internal bot toolkit", "capability_tags": ["ai.assistant"],
        "status": "approved"})
    project_id = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={
        "project_id": project_id, "approve_gates": ["external_research", "repository_access", "client_report"]})
    ).json()["run_id"]
    await runner.wait(run_id)

    sales = (await client.get(f"/api/runs/{run_id}/sales", headers=viewer)).json()
    assert sales["summary"]["status"] == "draft" and sales["summary"]["content"]["top_gaps"]
    assert "outreach_input" not in sales["summary"]["content"]
    o = sales["outreach"]
    assert o["status"] == "draft" and o["version"] == 1 and o["content"]["problems"] == []
    assert (await client.patch(f"/api/runs/{run_id}/outreach", headers=viewer, json={"subject": "x"})).status_code == 403

    # An edit that leaks internal knowledge cannot be approved.
    body = o["content"]["body"] + "\nWe also have our Internal bot toolkit."
    r = (await client.patch(f"/api/runs/{run_id}/outreach", headers=h, json={"body": body, "note": "add toolkit"})).json()
    assert r["version"] == 2 and r["edited"] and any("Internal bot toolkit" in p for p in r["content"]["problems"])
    r = await client.post(f"/api/runs/{run_id}/outreach/approve", headers=h, json={"acknowledge_warnings": True})
    assert r.status_code == 409 and "Internal bot toolkit" in r.json()["detail"]

    # A number that is not in the facts is a warning that must be acknowledged.
    body = o["content"]["body"] + "\nWe typically cut no-shows by 25%."
    r = (await client.patch(f"/api/runs/{run_id}/outreach", headers=h, json={"body": body})).json()
    assert any("25" in p for p in r["content"]["problems"])
    assert (await client.post(f"/api/runs/{run_id}/outreach/approve", headers=h, json={})).status_code == 409
    r = await client.post(f"/api/runs/{run_id}/outreach/approve", headers=h,
                          json={"acknowledge_warnings": True, "note": "figure confirmed by delivery lead"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    # Regenerating starts a new draft; history keeps every step.
    r = (await client.post(f"/api/runs/{run_id}/outreach/regenerate", headers=h, json={"instructions": "shorter"})).json()
    assert r["status"] == "draft" and r["version"] == 4 and r["content"]["generated_by"] == "template"
    assert [e["action"] for e in r["history"]] == ["regenerated", "approved", "edited", "edited", "generated"]

    assert (await client.post(f"/api/runs/{run_id}/outreach/approve", headers=h, json={})).json()["status"] == "approved"
    eml = await client.get(f"/api/runs/{run_id}/outreach.eml", headers=viewer)
    assert eml.status_code == 200 and eml.headers["content-type"].startswith("message/rfc822")
    assert "DRAFT" not in eml.headers["content-disposition"] and b"Subject: " in eml.content
    assert b"X-Unsent: 1" in eml.content

    # Sales summary: edit, approve, export.
    r = (await client.patch(f"/api/runs/{run_id}/sales/summary", headers=h,
                            json={"next_step": "Book a demo with their CTO", "reviewer_notes": "Warm lead"})).json()
    assert r["content"]["next_step"] == "Book a demo with their CTO" and r["version"] == 2
    assert (await client.post(f"/api/runs/{run_id}/sales/summary/approve", headers=h, json={})).json()["status"] == "approved"
    md = await client.get(f"/api/runs/{run_id}/sales-summary.md", headers=viewer)
    assert md.status_code == 200 and "## Top competitive gaps" in md.text and "Book a demo with their CTO" in md.text
    assert "Internal bot toolkit" in md.text and "_Internal only (do not share):_" in md.text

    audit = {a["action"] for a in (await client.get("/api/audit", headers=h)).json()}
    assert {"outreach.edited", "outreach.approved", "outreach.regenerated", "outreach.exported",
            "sales_summary.edited", "sales_summary.approved"} <= audit

    # Other organizations cannot see it.
    h2 = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.get(f"/api/runs/{run_id}/sales", headers=h2)).status_code == 404
    assert (await client.get(f"/api/runs/{run_id}/outreach.eml", headers=h2)).status_code == 404
