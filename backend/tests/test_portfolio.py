"""Portfolio & cross-client intelligence (BRS 33, 35; PRD 10.39-10.41)."""

from pathlib import Path

from cip.services.runner import runner

from test_api import client, register  # noqa: F401  (fixture)
from test_security_api import add_user, login

ALL_GATES = ["external_research", "repository_access", "client_report"]


async def _import_examples(c, h) -> list[str]:
    csv = (Path(__file__).resolve().parents[2] / "examples" / "clients.csv").read_bytes()
    up = (await c.post("/api/uploads", headers=h, files={"file": ("clients.csv", csv, "text/csv")})).json()
    await c.post(f"/api/uploads/{up['id']}/import", headers=h, json={})
    return [p["id"] for p in (await c.get("/api/projects", headers=h)).json()]


async def test_portfolio_summarises_latest_analyses_across_clients(client):  # noqa: F811
    h = await register(client)
    await client.post("/api/knowledge", headers=h, json={
        "kind": "case_study", "title": "Workflow automation for clinics", "capability_tags": ["ai.automation"],
        "automation": True, "status": "approved", "visibility": "client_facing"})
    projects = await _import_examples(client, h)
    body = (await client.post("/api/runs/bulk", headers=h, json={"project_ids": projects, "approve_gates": ALL_GATES})).json()
    for r in body["runs"]:
        await runner.wait(r["run_id"])

    pf = (await client.get("/api/portfolio", headers=h)).json()
    s = pf["summary"]
    assert s["projects"] == len(projects) and s["analysed"] == len(projects) and s["never_analysed"] == 0
    assert s["running"] == 0 and s["failed"] == 0
    abc = next(p for p in pf["projects"] if p["project"] == "ABC Patient Management")
    assert abc["industry"] and abc["top_priority"] and abc["quality"] and abc["opportunity_score"] is not None
    assert abc["analysed_run_id"] == abc["run_id"]
    assert pf["top_opportunities"] and all(o["run_id"] for o in pf["top_opportunities"])
    scores = [o["score"] for o in pf["top_opportunities"]]
    assert scores == sorted(scores, reverse=True)
    assert pf["recurring_gaps"] and pf["recurring_gaps"][0]["projects"] >= 1
    assert pf["industries"] and pf["capability_demand"][0]["title"] == "Workflow automation for clinics"

    # Search, filter and sort
    only = (await client.get("/api/portfolio?q=patient", headers=h)).json()["projects"]
    assert [p["project"] for p in only] == ["ABC Patient Management"]
    names = [p["client"] for p in (await client.get("/api/portfolio?sort=name", headers=h)).json()["projects"]]
    assert names == sorted(names, key=str.lower)
    industry = abc["industry"]
    assert all(p["industry"] == industry for p in
               (await client.get(f"/api/portfolio?industry={industry}", headers=h)).json()["projects"])
    assert (await client.get("/api/portfolio?sort=bogus", headers=h)).status_code == 422


async def test_portfolio_respects_access(client):  # noqa: F811
    h = await register(client)
    projects = await _import_examples(client, h)
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.get("/api/portfolio", headers=other)).json()["summary"]["projects"] == 0
    # A restricted project is hidden from members who are not on it.
    await add_user(client, h, "viewer@acme-consulting.com", "viewer")
    await client.put(f"/api/projects/{projects[0]}/access", headers=h, json={"restricted": True, "member_ids": []})
    viewer = {"Authorization": f"Bearer {(await login(client, 'viewer@acme-consulting.com')).json()['access_token']}"}
    seen = {p["project_id"] for p in (await client.get("/api/portfolio", headers=viewer)).json()["projects"]}
    assert projects[0] not in seen and len(seen) == len(projects) - 1
