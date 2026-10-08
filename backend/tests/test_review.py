"""Human review: overrides and decisions applied as a new run version (BRS 17, PRD 10.34)."""

from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_security_api import add_user, login

ALL_GATES = ["external_research", "repository_access", "client_report"]


async def _finished_run(c, h):
    pid = await upload_and_import(c, h)
    run_id = (await c.post("/api/runs", headers=h, json={"project_id": pid, "approve_gates": ALL_GATES})).json()["run_id"]
    await runner.wait(run_id)
    return run_id


async def _agent(c, h, run_id, agent):
    return (await c.get(f"/api/runs/{run_id}/agents/{agent}", headers=h)).json()["result"]["data"]


async def test_review_overrides_flow_through_a_new_run_version(client):  # noqa: F811
    h = await register(client)
    run_id = await _finished_run(client, h)
    comps = (await _agent(client, h, run_id, "competitor_research"))["competitors"]
    gaps = (await _agent(client, h, run_id, "gap_analysis"))["gaps"]
    recs = (await _agent(client, h, run_id, "opportunity_prioritization"))["recommendations"]
    medibook = next(c for c in comps if c["name"] == "MediBook")
    reject = next(g for g in gaps if g["gap_type"] == "missing")
    rework = next(g for g in gaps if g["id"] != reject["id"] and g["gap_type"] != "missing")
    # A recommendation that does not depend on competitors, so it stays in the roadmap after MediBook goes.
    kinds = {g["id"]: g["gap_type"] for g in gaps}
    rec = next(r for r in recs if kinds.get(r["gap_id"]) in ("process", "technology", "security"))

    put = lambda body: client.put(f"/api/runs/{run_id}/review", headers=h, json=body)  # noqa: E731
    # Validation
    assert (await put({"kind": "capability_status", "target_id": "nope", "value": "missing"})).status_code == 422
    assert (await put({"kind": "capability_status", "target_id": "ai.voice", "value": "gone"})).status_code == 422
    assert (await put({"kind": "competitor", "target_id": "cmp_x", "value": "exclude"})).status_code == 404
    assert (await put({"kind": "recommendation", "target_id": rec["id"], "field": "priority", "value": "urgent"})).status_code == 422

    assert (await put({"kind": "capability_status", "target_id": "ai.voice", "value": "missing",
                       "note": "Client confirmed on the call: no voice features"})).status_code == 200
    assert (await put({"kind": "competitor", "target_id": medibook["id"], "value": "exclude",
                       "note": "Different segment"})).status_code == 200
    assert (await put({"kind": "gap", "target_id": reject["id"], "value": "reject", "note": "Not relevant"})).status_code == 200
    assert (await put({"kind": "gap", "target_id": rework["id"], "value": "rework", "note": "Check pricing page"})).status_code == 200
    r = await put({"kind": "recommendation", "target_id": rec["id"], "field": "priority", "value": "low", "note": "Budget"})
    assert r.status_code == 200
    # Upsert: the same target and field replaces the earlier value.
    r = await put({"kind": "recommendation", "target_id": rec["id"], "field": "priority", "value": "high", "note": "CEO priority"})
    overrides = (await client.get(f"/api/runs/{run_id}/review", headers=h)).json()
    assert len(overrides) == 5 and all(o["status"] == "pending" for o in overrides)

    applied = (await client.post(f"/api/runs/{run_id}/review/apply", headers=h)).json()
    new_id = applied["run_id"]
    assert applied["applied"] == 5
    assert {"feature_comparison", "pricing_analysis", "opportunity_prioritization", "enhancement_planning"} <= set(applied["stages"])
    await runner.wait(new_id)
    run = (await client.get(f"/api/runs/{new_id}", headers=h)).json()
    assert run["status"] == "completed" and run["parent_run_id"] == run_id

    feats = (await _agent(client, h, new_id, "product_features"))["observations"]
    assert feats["ai.voice"]["status"] == "missing" and feats["ai.voice"]["reviewed"]
    rows = {r["feature_id"]: r for r in (await _agent(client, h, new_id, "feature_comparison"))["rows"]}
    assert all(medibook["id"] not in r["competitors"] for r in rows.values())
    new_comps = await _agent(client, h, new_id, "competitor_research")
    assert medibook["name"] not in {c["name"] for c in new_comps["competitors"]}
    assert any("Excluded by reviewer" in c["rationale"] for c in new_comps["rejected"])
    new_gaps = (await _agent(client, h, new_id, "gap_analysis"))["gaps"]
    assert reject["name"] not in {g["name"] for g in new_gaps}
    assert next(g for g in new_gaps if g["name"] == rework["name"])["review"]["decision"] == "rework"
    new_rec = next(r for r in (await _agent(client, h, new_id, "opportunity_prioritization"))["recommendations"]
                   if r["feature"] == rec["feature"])
    assert new_rec["priority"] == "high" and new_rec["review"][0]["note"] == "CEO priority"
    q = await _agent(client, h, new_id, "quality_assurance")
    assert q["metrics"]["manual_overrides"] == 5
    assert any("rework" in i["message"] for i in q["issues"])
    assert not any("High priority on low-confidence" in i["message"] for i in q["issues"])
    md = (await client.get(f"/api/runs/{new_id}/report.md", headers=h)).text
    assert "Excluded by reviewer" in md and "| AI voice" not in md

    # The reviewed run is unchanged; its overrides are marked applied.
    assert (await _agent(client, h, run_id, "product_features"))["observations"]["ai.voice"]["status"] == "unknown"
    assert {o["status"] for o in (await client.get(f"/api/runs/{run_id}/review", headers=h)).json()} == {"applied"}
    assert (await client.post(f"/api/runs/{run_id}/review/apply", headers=h)).status_code == 409
    audit = {a["action"] for a in (await client.get("/api/audit", headers=h)).json()}
    assert {"review.override", "review.applied"} <= audit

    # A later refresh re-runs competitor research from scratch; the reviewers' decisions still apply.
    refreshed = (await client.post(f"/api/runs/{new_id}/rerun", headers=h, json={"stages": ["competitors"]})).json()
    await runner.wait(refreshed["run_id"])
    again = await _agent(client, h, refreshed["run_id"], "competitor_research")
    assert medibook["name"] not in {c["name"] for c in again["competitors"] + again["landscape"]}
    assert (await _agent(client, h, refreshed["run_id"], "product_features"))["observations"]["ai.voice"]["status"] == "missing"
    assert len((await client.get(f"/api/runs/{refreshed['run_id']}/review", headers=h)).json()) == 5


async def test_review_permissions_and_isolation(client):  # noqa: F811
    h = await register(client)
    run_id = await _finished_run(client, h)
    await add_user(client, h, "viewer@acme-consulting.com", "viewer")
    viewer = {"Authorization": f"Bearer {(await login(client, 'viewer@acme-consulting.com')).json()['access_token']}"}
    body = {"kind": "capability_status", "target_id": "ai.voice", "value": "missing"}
    assert (await client.put(f"/api/runs/{run_id}/review", headers=viewer, json=body)).status_code == 403
    assert (await client.get(f"/api/runs/{run_id}/review", headers=viewer)).status_code == 200
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.put(f"/api/runs/{run_id}/review", headers=other, json=body)).status_code == 404
    o = (await client.put(f"/api/runs/{run_id}/review", headers=h, json=body)).json()
    assert (await client.delete(f"/api/runs/{run_id}/review/{o['id']}", headers=other)).status_code == 404
    assert (await client.delete(f"/api/runs/{run_id}/review/{o['id']}", headers=h)).status_code == 204
    assert (await client.post(f"/api/runs/{run_id}/review/apply", headers=h)).status_code == 409
