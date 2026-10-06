"""API tests: auth, RBAC, tenant isolation, CSV upload → import → run → approvals → report."""

import httpx
import pytest

from cip.api.main import app
from cip.connectors.research.web import WebFetcher
from cip.db import session as db
from cip.services.runner import runner

from conftest import SAMPLE_CSV
from fakes import FakeSearch, FakeSourceControl, web_transport


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIP_STORAGE_DIR", str(tmp_path / "storage"))
    from cip.config import get_settings
    get_settings.cache_clear()
    db.configure(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.create_all()

    def hook(ctx):
        ctx.fetcher = WebFetcher(transport=web_transport())
        ctx.search = FakeSearch()
        ctx.source_control_factory = lambda ref, token: FakeSourceControl(ref, token)

    runner.context_hook = hook
    from cip.services.ratelimit import limiter
    limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    runner.context_hook = None
    for run_id in list(runner._tasks):
        await runner.wait(run_id)
    await db.dispose()
    get_settings.cache_clear()


async def register(c, org="Acme Consulting", email="lead@acme-consulting.com"):
    r = await c.post("/api/auth/register", json={"organization": org, "email": email, "password": "s3cure-password!"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def upload_and_import(c, h):
    r = await c.post("/api/uploads", headers=h, files={"file": ("clients.csv", SAMPLE_CSV, "text/csv")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["valid"] and body["records"][0]["client"]["domain"] == "abc-healthcare.com"
    r = await c.post(f"/api/uploads/{body['id']}/import", headers=h, json={})
    assert r.status_code == 200, r.text
    return r.json()["created_projects"][0]


async def test_auth_required(client):
    assert (await client.get("/api/projects")).status_code == 401
    assert (await client.get("/api/projects", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_full_flow_with_approvals(client):
    h = await register(client)
    project_id = await upload_and_import(client, h)

    preview = (await client.get(f"/api/runs/approval-preview?project_id={project_id}", headers=h)).json()
    assert {p["gate"] for p in preview} == {"external_research", "repository_access", "client_report"}

    r = await client.post("/api/runs", headers=h, json={
        "project_id": project_id, "approve_gates": ["external_research", "repository_access"],
        "scoring_weights": {"ai_opportunity": 2.0}})
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    await runner.wait(run_id)

    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["status"] == "awaiting_approval"
    assert run["agents"]["enhancement_planning"] == "completed"
    assert run["agents"]["report"] == "awaiting_approval"
    pending = [a for a in run["approvals"] if a["status"] == "pending"]
    assert [a["gate"] for a in pending] == ["client_report"]
    assert (await client.get(f"/api/runs/{run_id}/report", headers=h)).status_code == 404

    r = await client.post(f"/api/runs/{run_id}/approvals/{pending[0]['id']}", headers=h, json={"approve": True})
    assert r.status_code == 200
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["status"] == "completed" and run["has_report"]

    report = (await client.get(f"/api/runs/{run_id}/report", headers=h)).json()
    assert report["content"]["sections"]["scoring_weights"]["ai_opportunity"] == 2.0
    md = await client.get(f"/api/runs/{run_id}/report.md", headers=h)
    assert md.headers["content-type"].startswith("text/markdown") and "Evidence Appendix" in md.text

    evidence = (await client.get(f"/api/runs/{run_id}/evidence?source_type=github", headers=h)).json()
    assert evidence and all(e["source_type"] == "github" for e in evidence)
    agent = (await client.get(f"/api/runs/{run_id}/agents/gap_analysis", headers=h)).json()
    assert agent["result"]["data"]["gaps"]

    projects = (await client.get("/api/projects", headers=h)).json()
    assert projects[0]["latest_run"]["status"] == "completed"
    assert (await client.get("/api/clients", headers=h)).json()[0]["project_count"] == 1


async def test_rejecting_a_gate_skips_the_agent(client):
    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={
        "project_id": project_id, "approve_gates": ["external_research", "client_report"]})).json()["run_id"]
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    repo_gate = next(a for a in run["approvals"] if a["gate"] == "repository_access")
    await client.post(f"/api/runs/{run_id}/approvals/{repo_gate['id']}", headers=h, json={"approve": False})
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["status"] == "completed"
    assert run["agents"]["repository"] == "skipped" and run["agents"]["code_analysis"] == "skipped"
    assert run["agents"]["report"] == "completed"


async def test_tenant_isolation_and_rbac(client):
    h1 = await register(client)
    project_id = await upload_and_import(client, h1)
    h2 = await register(client, org="Other Co", email="boss@other-co.com")
    assert (await client.get(f"/api/projects/{project_id}", headers=h2)).status_code == 404
    assert (await client.get("/api/projects", headers=h2)).json() == []
    r = await client.post("/api/runs", headers=h2, json={"project_id": project_id})
    assert r.status_code == 404

    r = await client.post("/api/auth/users", headers=h1, json={"email": "viewer@acme-consulting.com",
                                                               "password": "viewer-password-1", "role": "viewer"})
    assert r.status_code == 201
    tok = (await client.post("/api/auth/login", json={"email": "viewer@acme-consulting.com",
                                                      "password": "viewer-password-1"})).json()["access_token"]
    hv = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/projects", headers=hv)).status_code == 200
    r = await client.post("/api/uploads", headers=hv, files={"file": ("x.csv", SAMPLE_CSV, "text/csv")})
    assert r.status_code == 403
    assert (await client.post("/api/connections/token", headers=hv,
                              json={"provider": "github", "token": "ghp_" + "x" * 36})).status_code == 403


async def test_connection_tokens_are_encrypted_and_never_returned(client):
    h = await register(client)
    token = "ghp_" + "z" * 36
    r = await client.post("/api/connections/token", headers=h, json={"provider": "github", "token": token})
    assert r.status_code == 201 and token not in r.text
    listed = await client.get("/api/connections", headers=h)
    assert token not in listed.text and listed.json()[0]["host"] == "github.com"
    from sqlalchemy import select
    from cip.db.models import SourceConnection
    async with db.sessionmaker()() as s:
        con = (await s.execute(select(SourceConnection))).scalar_one()
        assert token not in con.encrypted_token


async def test_invalid_csv_upload_reports_errors(client):
    h = await register(client)
    r = await client.post("/api/uploads", headers=h, files={"file": ("bad.csv", "foo,bar\n1,2\n", "text/csv")})
    body = r.json()
    assert r.status_code == 201 and not body["valid"] and body["errors"]
    r = await client.post(f"/api/uploads/{body['id']}/import", headers=h, json={})
    assert r.status_code == 422


async def test_concurrent_evidence_saves_do_not_duplicate(client):
    import asyncio

    from cip.core.schemas import Evidence
    from cip.services.runner import DbRunStore

    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={"project_id": project_id})).json()["run_id"]
    await runner.wait(run_id)
    store = DbRunStore((await client.get("/api/auth/me", headers=h)).json()["org_id"])
    evs = [Evidence(claim=f"c{i}", source_url="https://x.com", source_type="website", confidence=0.5) for i in range(20)]
    await asyncio.gather(store.save_evidence(run_id, evs), store.save_evidence(run_id, evs),
                         store.save_evidence(run_id, evs[:5]))
    ids = [e["id"] for e in (await client.get(f"/api/runs/{run_id}/evidence", headers=h)).json()]
    assert len(ids) == len(set(ids))
    assert {e.id for e in evs} <= set(ids)


async def test_interrupted_runs_resume_on_startup(client):
    """Simulate a crash mid-run: the run is 'running' with one agent caught 'running'."""
    from sqlalchemy import select

    from cip.db.models import AgentExecution, AnalysisRun

    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={
        "project_id": project_id,
        "approve_gates": ["external_research", "repository_access", "client_report"]})).json()["run_id"]
    await runner.wait(run_id)
    async with db.sessionmaker()() as s:
        run = await s.get(AnalysisRun, run_id)
        run.status = "running"
        for ex in (await s.execute(select(AgentExecution).where(AgentExecution.run_id == run_id))).scalars():
            if ex.agent in ("gap_analysis", "opportunity_prioritization", "enhancement_planning", "report"):
                ex.status = "running" if ex.agent == "gap_analysis" else "pending"
                ex.result = None
        await s.commit()

    assert await runner.resume_interrupted() == [run_id]
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["status"] == "completed" and run["has_report"]
    assert all(s == "completed" for a, s in run["agents"].items())


async def test_upload_with_malformed_url_cell_reports_issue_not_500(client):
    """Regression: a cell like '[2013-04-24]' in the Project URL column crashed POST /api/uploads with a 500."""
    h = await register(client)
    csv_text = ("Client Name,Project Name,Project URL,Start Date\n"
                "Acme,Portal,[2013-04-24],2013-04-24\n"
                "Beta,Shop,https://shop.beta-corp.com,2020-01-01\n")
    r = await client.post("/api/uploads", headers=h, files={"file": ("clients.csv", csv_text, "text/csv")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["records"]) == 2
    assert any("Invalid project URL" in i for i in body["records"][0]["issues"])
    assert body["records"][1]["project"]["url"] == "https://shop.beta-corp.com"
