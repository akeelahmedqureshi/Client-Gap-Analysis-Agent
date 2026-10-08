"""Run lifecycle: duplicate prevention, bulk start, cancel, pause, partial re-run (BRS 26.2-26.3, PRD 10.31-10.32)."""

from cip.agents.orchestrator import Orchestrator
from cip.core.schemas import AgentStatus
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_pipeline import MemStore

ALL_GATES = ["external_research", "repository_access", "client_report"]


async def _start(c, h, project_id, gates=ALL_GATES):
    return await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": gates})


async def test_duplicate_runs_are_prevented_and_cancel_stops_a_run(client):  # noqa: F811
    h = await register(client)
    pid = await upload_and_import(client, h)
    r = await _start(client, h, pid, gates=[])
    run_id = r.json()["run_id"]
    await runner.wait(run_id)
    assert (await client.get(f"/api/runs/{run_id}", headers=h)).json()["status"] == "awaiting_approval"

    dup = await _start(client, h, pid)
    assert dup.status_code == 409 and run_id in dup.json()["detail"]

    r = await client.post(f"/api/runs/{run_id}/cancel", headers=h)
    run = r.json()
    assert r.status_code == 200 and run["status"] == "cancelled"
    assert run["agents"]["client_research"] == "skipped" and run["agents"]["report"] == "skipped"
    assert next(d for d in run["agent_details"] if d["agent"] == "client_research")["error"] == "run cancelled"
    approval = next(a for a in run["approvals"] if a["status"] == "pending")
    assert (await client.post(f"/api/runs/{run_id}/approvals/{approval['id']}", headers=h,
                              json={"approve": True})).status_code == 409
    assert (await client.post(f"/api/runs/{run_id}/cancel", headers=h)).status_code == 409

    # Once cancelled, a new analysis may start.
    r2 = await _start(client, h, pid)
    assert r2.status_code == 201
    await runner.wait(r2.json()["run_id"])
    audit = {a["action"] for a in (await client.get("/api/audit", headers=h)).json()}
    assert "run.cancelled" in audit


async def test_bulk_start_creates_one_independent_run_per_project(client):  # noqa: F811
    from pathlib import Path

    h = await register(client)
    csv = (Path(__file__).resolve().parents[2] / "examples" / "clients.csv").read_bytes()
    up = (await client.post("/api/uploads", headers=h, files={"file": ("clients.csv", csv, "text/csv")})).json()
    await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})
    projects = [p["id"] for p in (await client.get("/api/projects", headers=h)).json()]
    assert len(projects) >= 2
    r = await client.post("/api/runs/bulk", headers=h, json={"project_ids": projects + ["prj_nope"],
                                                             "approve_gates": ALL_GATES})
    body = r.json()
    assert r.status_code == 201 and len(body["runs"]) == len(projects)
    assert {x["project_id"] for x in body["runs"]} == set(projects)
    assert body["skipped"] == [{"project_id": "prj_nope", "reason": "not found"}]
    again = (await client.post("/api/runs/bulk", headers=h, json={"project_ids": projects})).json()
    assert again["runs"] == [] and {s["reason"] for s in again["skipped"]} == {"already in progress"}
    for x in body["runs"]:
        await runner.wait(x["run_id"])
    statuses = {(await client.get(f"/api/runs/{x['run_id']}", headers=h)).json()["status"] for x in body["runs"]}
    assert statuses <= {"completed", "completed_with_errors"}


async def test_partial_rerun_creates_a_new_version_and_keeps_upstream_stages(client):  # noqa: F811
    h = await register(client)
    pid = await upload_and_import(client, h)
    first = (await _start(client, h, pid)).json()["run_id"]
    await runner.wait(first)
    before = (await client.get(f"/api/runs/{first}", headers=h)).json()
    assert before["status"] == "completed"

    assert (await client.post(f"/api/runs/{first}/rerun", headers=h, json={"stages": ["nope"]})).status_code == 422
    stages = (await client.get("/api/runs/rerun-stages", headers=h)).json()
    assert stages["competitors"] == ["competitor_research"]
    r = await client.post(f"/api/runs/{first}/rerun", headers=h, json={"stages": ["competitors"]})
    assert r.status_code == 201
    new = r.json()
    assert new["parent_run_id"] == first and new["rerun_stages"] == ["competitor_research"]
    assert (await client.post(f"/api/runs/{first}/rerun", headers=h, json={"stages": ["report"]})).status_code == 409
    await runner.wait(new["run_id"])
    after = (await client.get(f"/api/runs/{new['run_id']}", headers=h)).json()
    assert after["status"] == "completed" and after["has_report"]

    old_d = {d["agent"]: d for d in before["agent_details"]}
    new_d = {d["agent"]: d for d in after["agent_details"]}
    for kept in ("csv_intake", "client_research", "product_features", "industry_market", "code_analysis"):
        assert new_d[kept]["started_at"] == old_d[kept]["started_at"], f"{kept} must be reused, not re-run"
    for rerun in ("competitor_research", "feature_comparison", "gap_analysis", "outreach", "report"):
        assert new_d[rerun]["started_at"] != old_d[rerun]["started_at"], f"{rerun} must be re-run"
    old_ev = (await client.get(f"/api/runs/{first}/evidence", headers=h)).json()
    new_ev = {e["id"] for e in (await client.get(f"/api/runs/{new['run_id']}/evidence", headers=h)).json()}
    assert {e["id"] for e in old_ev} <= new_ev
    # The earlier version is untouched.
    assert (await client.get(f"/api/runs/{first}", headers=h)).json()["agent_details"] == before["agent_details"]


class _ControlStore(MemStore):
    def __init__(self, after_waves: int, state: str) -> None:
        super().__init__()
        self.waves, self.after, self.state = 0, after_waves, state

    async def control(self, run_id):
        self.waves += 1
        return self.state if self.waves > self.after else None


async def test_pause_and_cancel_are_honoured_between_waves(make_ctx):
    statuses: dict = {}
    store = _ControlStore(after_waves=3, state="paused")
    assert await Orchestrator(store, retry_delay=0).run(make_ctx(), statuses) == "paused"
    done = [n for n, s in statuses.items() if s == AgentStatus.COMPLETED]
    pending = [n for n, s in statuses.items() if s == AgentStatus.PENDING]
    assert done and pending and "report" in pending, "paused runs keep their remaining stages pending"

    statuses = {}
    store = _ControlStore(after_waves=3, state="cancelled")
    assert await Orchestrator(store, retry_delay=0).run(make_ctx(), statuses) == "cancelled"
    assert statuses["report"] == AgentStatus.SKIPPED
    assert not [n for n, s in statuses.items() if s == AgentStatus.PENDING]
