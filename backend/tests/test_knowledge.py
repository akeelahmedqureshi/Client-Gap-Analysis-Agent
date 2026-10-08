"""Knowledge base: governance, visibility, versioning, and matching to client needs (BRS 7.18, 27)."""

from cip.core.matching import Need, match_need
from cip.core.taxonomy import load_taxonomy
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_pipeline import run_all
from test_security_api import add_user, login

PW = "s3cure-password!"


def record(**kw) -> dict:
    base = {"id": "kb_x", "kind": "capability", "title": "", "summary": "", "capability_tags": [], "industries": [],
            "technologies": [], "status": "approved", "visibility": "client_facing", "version": 1}
    return {**base, **kw}


# --- matching (pure) ----------------------------------------------------------------------------

def test_matching_requires_capability_relevance_and_explains_itself():
    tax = load_taxonomy()
    need = Need(id="rec_1", name="AI assistant / chatbot", feature_id="ai.assistant", category_id="ai", ai=True)
    tagged = record(id="kb_tag", title="Patient support chatbot", capability_tags=["ai.assistant"], ai=True,
                    industries=["Healthcare"], technologies=["React"])
    industry_only = record(id="kb_ind", title="Hospital intranet", industries=["Healthcare"], technologies=["React"])
    draft = record(id="kb_draft", title="Chatbot accelerator", capability_tags=["ai.assistant"], status="draft")
    restricted = record(id="kb_r", title="Chatbot for a bank", capability_tags=["ai.assistant"], status="restricted")
    internal = record(id="kb_int", title="Support chatbot", capability_tags=["ai.assistant"], visibility="internal")

    found = match_need(need, [tagged, industry_only, draft, restricted, internal], tax,
                       industry="Healthcare technology", stack={"react"})
    ids = [m.record_id for m in found]
    assert ids[0] == "kb_tag" and "kb_int" in ids
    assert "kb_ind" not in ids, "industry/technology overlap alone is never a match"
    assert "kb_draft" not in ids and "kb_r" not in ids, "only approved records are matched"
    best = found[0]
    assert best.client_facing and best.confidence > [m for m in found if m.record_id == "kb_int"][0].confidence
    assert any("required capability" in r for r in best.reasons)
    assert any("Same industry" in r for r in best.reasons) and any("technology" in r for r in best.reasons)
    assert not [m for m in found if m.record_id == "kb_int"][0].client_facing


def test_case_study_customer_named_only_when_reference_allowed():
    tax = load_taxonomy()
    need = Need(id="n", name="Live chat support", feature_id=None)
    allowed = record(id="a", kind="case_study", title="Live chat support rollout", customer_name="SmileCo",
                     reference_allowed=True)
    not_allowed = record(id="b", kind="case_study", title="Live chat support desk", customer_name="BankCo")
    internal = record(id="c", kind="case_study", title="Live chat support", customer_name="SecretCo",
                      reference_allowed=True, visibility="internal")
    by_id = {m.record_id: m for m in match_need(need, [allowed, not_allowed, internal], tax)}
    assert by_id["a"].reference_allowed and by_id["a"].customer_name == "SmileCo"
    assert not by_id["b"].reference_allowed and by_id["b"].customer_name is None
    assert not by_id["c"].reference_allowed, "internal records are never referenced to clients"


# --- agent ----------------------------------------------------------------------------------------

async def test_capability_matching_agent_maps_recommendations_to_approved_records(make_ctx):
    kb = [
        record(id="kb_auto", kind="solution", title="Workflow automation platform", automation=True, ai=True,
               capability_tags=["ai.automation"], technologies=["React"], version=3),
        record(id="kb_chat", kind="case_study", title="Live chat for a clinic network", capability_tags=["support.live_chat"],
               industries=["Healthcare"], outcomes="Response time halved", reference_allowed=True,
               customer_name="CareCo"),
        record(id="kb_draft", title="AI assistant accelerator", capability_tags=["ai.assistant"], status="draft"),
        record(id="kb_secret", title="Chatbot for a bank", capability_tags=["ai.assistant"], status="restricted"),
    ]
    ctx = make_ctx(knowledge=kb)
    status, statuses, _ = await run_all(ctx)
    assert status == "completed"
    out = ctx.data("capability_matching")
    assert out["knowledge_base"]["records_considered"] == 2
    assert {r["id"] for r in out["knowledge_base"]["records"]} == {"kb_auto", "kb_chat"}
    assert out["knowledge_base"]["records"][0]["version"] in (1, 3)
    matched = {m["need"]: m["matches"] for m in out["matches"]}
    assert matched["AI workflow automation / agents"][0]["record_id"] == "kb_auto"
    used = {mm["record_id"] for ms in matched.values() for mm in ms}
    assert not used & {"kb_draft", "kb_secret"}
    assert "AI assistant / chatbot" in out["unmatched"]
    assert any(e["record_id"] == "kb_auto" for e in out["by_record"])


async def test_capability_matching_with_empty_knowledge_base(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    out = ctx.data("capability_matching")
    assert out["matches"] == [] and out["knowledge_base"]["records_considered"] == 0
    assert ctx.outputs["capability_matching"].findings[0].title == "Knowledge base has no approved records"


# --- API ------------------------------------------------------------------------------------------

KB_BODY = {"kind": "case_study", "title": "Automated SMS reminders for a clinic chain",
           "summary": "Two-way SMS reminders and rescheduling", "outcomes": "No-shows down 30%",
           "customer_name": "SmileCo", "industries": ["Healthcare", "healthcare"], "technologies": ["Twilio", "React"],
           "capability_tags": ["comm.sms", "ai.automation"], "automation": True,
           "visibility": "client_facing", "reference_allowed": True}


async def test_knowledge_governance_visibility_and_versions(client):  # noqa: F811
    admin = await register(client)
    await add_user(client, admin, "analyst@acme-consulting.com", "analyst")
    await add_user(client, admin, "viewer@acme-consulting.com", "viewer")
    analyst = {"Authorization": f"Bearer {(await login(client, 'analyst@acme-consulting.com')).json()['access_token']}"}
    viewer = {"Authorization": f"Bearer {(await login(client, 'viewer@acme-consulting.com')).json()['access_token']}"}

    # Analysts create drafts and submit them; they cannot approve.
    r = await client.post("/api/knowledge", headers=analyst, json={**KB_BODY, "status": "approved"})
    assert r.status_code == 403
    r = await client.post("/api/knowledge", headers=analyst, json=KB_BODY)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "draft" and rec["version"] == 1 and not rec["client_facing"]
    assert rec["industries"] == ["Healthcare"], "tags are de-duplicated case-insensitively"
    rid = rec["id"]
    assert (await client.post("/api/knowledge", headers=viewer, json=KB_BODY)).status_code == 403

    # Viewers only see approved records.
    assert (await client.get("/api/knowledge", headers=viewer)).json() == []
    assert (await client.get(f"/api/knowledge/{rid}", headers=viewer)).status_code == 404

    r = await client.post(f"/api/knowledge/{rid}/status", headers=analyst, json={"status": "in_review"})
    assert r.json()["status"] == "in_review" and r.json()["version"] == 2
    assert (await client.post(f"/api/knowledge/{rid}/status", headers=analyst,
                              json={"status": "approved"})).status_code == 403
    r = await client.post(f"/api/knowledge/{rid}/status", headers=admin, json={"status": "approved", "note": "ok"})
    assert r.json()["status"] == "approved" and r.json()["client_facing"] and r.json()["approved_by"]
    assert [x["id"] for x in (await client.get("/api/knowledge", headers=viewer)).json()] == [rid]

    # An analyst's edit to an approved record sends it back for review.
    r = await client.patch(f"/api/knowledge/{rid}", headers=analyst, json={"outcomes": "No-shows down 35%"})
    assert r.json()["status"] == "in_review" and r.json()["version"] == 4
    # An admin's edit keeps the approval.
    await client.post(f"/api/knowledge/{rid}/status", headers=admin, json={"status": "approved"})
    r = await client.patch(f"/api/knowledge/{rid}", headers=admin, json={"title": "SMS reminders (clinics)"})
    assert r.json()["status"] == "approved" and r.json()["version"] == 6

    versions = (await client.get(f"/api/knowledge/{rid}/versions", headers=analyst)).json()
    assert [v["version"] for v in versions] == [6, 5, 4, 3, 2, 1]
    assert versions[-1]["change"] == "created" and versions[-1]["changed_by_email"] == "analyst@acme-consulting.com"
    assert versions[2]["snapshot"]["outcomes"] == "No-shows down 35%" and versions[2]["snapshot"]["status"] == "in_review"
    assert (await client.get(f"/api/knowledge/{rid}/versions", headers=viewer)).status_code == 403

    # Restricted records are admin-only; analysts cannot edit them.
    await client.post(f"/api/knowledge/{rid}/status", headers=admin, json={"status": "restricted"})
    assert (await client.get(f"/api/knowledge/{rid}", headers=analyst)).status_code == 404
    assert (await client.get(f"/api/knowledge/{rid}", headers=viewer)).status_code == 404
    assert (await client.get(f"/api/knowledge/{rid}", headers=admin)).status_code == 200

    # Search and filters.
    r2 = (await client.post("/api/knowledge", headers=admin, json={
        "kind": "capability", "title": "React Native mobile apps", "technologies": ["React Native"],
        "capability_tags": ["ux.mobile_app"], "status": "approved"})).json()
    assert [x["id"] for x in (await client.get("/api/knowledge?q=mobile apps", headers=admin)).json()] == [r2["id"]]
    assert [x["id"] for x in (await client.get("/api/knowledge?technology=twilio", headers=admin)).json()] == [rid]
    assert [x["id"] for x in (await client.get("/api/knowledge?tag=comm.sms", headers=admin)).json()] == [rid]
    assert (await client.get("/api/knowledge?status=restricted", headers=admin)).json()[0]["id"] == rid

    # Archived records are hidden by default.
    await client.post(f"/api/knowledge/{r2['id']}/status", headers=admin, json={"status": "archived"})
    assert r2["id"] not in [x["id"] for x in (await client.get("/api/knowledge", headers=admin)).json()]
    assert r2["id"] in [x["id"] for x in (await client.get("/api/knowledge?include_archived=true",
                                                             headers=admin)).json()]

    audit = (await client.get("/api/audit", headers=admin)).json()
    assert {"knowledge.created", "knowledge.updated", "knowledge.status"} <= {a["action"] for a in audit}


async def test_knowledge_is_isolated_between_organizations(client):  # noqa: F811
    h1 = await register(client)
    h2 = await register(client, org="Other Co", email="boss@other.example")
    rid = (await client.post("/api/knowledge", headers=h1, json={**KB_BODY, "status": "approved"})).json()["id"]
    assert (await client.get(f"/api/knowledge/{rid}", headers=h2)).status_code == 404
    assert (await client.patch(f"/api/knowledge/{rid}", headers=h2, json={"title": "Hijacked"})).status_code == 404
    assert (await client.post(f"/api/knowledge/{rid}/status", headers=h2, json={"status": "archived"})).status_code == 404
    assert (await client.get("/api/knowledge", headers=h2)).json() == []
    # Links can only point at the organization's own records.
    r = await client.post("/api/knowledge", headers=h2, json={**KB_BODY, "linked_ids": [rid]})
    assert r.status_code == 422


async def test_runs_match_only_the_organizations_approved_records(client):  # noqa: F811
    h = await register(client)
    h2 = await register(client, org="Other Co", email="boss@other.example")
    await client.post("/api/knowledge", headers=h, json={
        "kind": "solution", "title": "Workflow automation platform", "capability_tags": ["ai.automation"],
        "automation": True, "ai": True, "status": "approved", "visibility": "client_facing"})
    await client.post("/api/knowledge", headers=h, json={
        "kind": "solution", "title": "AI workflow automation (draft)", "capability_tags": ["ai.automation"]})
    await client.post("/api/knowledge", headers=h2, json={
        "kind": "solution", "title": "Other org automation", "capability_tags": ["ai.automation"], "status": "approved"})
    project_id = await upload_and_import(client, h)
    r = await client.post("/api/runs", headers=h, json={
        "project_id": project_id, "approve_gates": ["external_research", "repository_access", "client_report"]})
    run_id = r.json()["run_id"]
    await runner.wait(run_id)
    out = (await client.get(f"/api/runs/{run_id}/agents/capability_matching", headers=h)).json()["result"]["data"]
    assert out["knowledge_base"]["records_considered"] == 1
    titles = {m["title"] for ms in out["matches"] for m in ms["matches"]}
    assert titles == {"Workflow automation platform"}
