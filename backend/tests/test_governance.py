"""Governance: export permissions, structured exports, deletion, retention, job functions, notifications."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from cip.db import session as db
from cip.db.models import AgentExecution, AnalysisRun, AuditLog, EvidenceRecord, Notification
from cip.services import governance
from cip.services.exports import safe_cell
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_monitoring import channels  # noqa: F401  (fixture)
from test_security_api import add_user, bearer, login

ALL_GATES = ["external_research", "repository_access", "client_report"]


async def _analysed(c, h, gates=ALL_GATES) -> tuple[str, str]:
    project_id = await upload_and_import(c, h)
    run_id = (await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": gates})).json()["run_id"]
    await runner.wait(run_id)
    return project_id, run_id


async def _as(c, h, email, role, job_function=None):
    uid = await add_user(c, h, email, role)
    if job_function:
        assert (await c.patch(f"/api/users/{uid}", headers=h, json={"job_function": job_function})).status_code == 200
    return uid, bearer(await login(c, email))


async def _count(model, *where) -> int:
    async with db.sessionmaker()() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def test_csv_cells_cannot_become_formulas():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("+1") == "'+1" and safe_cell("@cmd") == "'@cmd" and safe_cell("-2") == "'-2"
    assert safe_cell(["a", "b"]) == "a; b" and safe_cell(None) == "" and safe_cell(True) == "yes"
    assert safe_cell(0.5) == "0.5" and safe_cell(3.0) == "3"


async def test_structured_exports_and_export_permissions(client):  # noqa: F811
    h = await register(client)
    _, run_id = await _analysed(client, h)

    matrix = await client.get(f"/api/runs/{run_id}/export/matrix.csv", headers=h)
    assert matrix.status_code == 200 and matrix.headers["content-type"].startswith("text/csv")
    lines = matrix.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("Capability,Category,Client,") and "Market class" in lines[0] and len(lines) > 5
    assert "not publicly identified" in matrix.text or "available" in matrix.text
    for name in ("gaps.csv", "opportunities.csv", "recommendations.csv", "evidence.csv"):
        r = await client.get(f"/api/runs/{run_id}/export/{name}", headers=h)
        assert r.status_code == 200 and len(r.text.splitlines()) > 1, name
    analysis = (await client.get(f"/api/runs/{run_id}/export/analysis.json", headers=h)).json()
    assert analysis["run"]["id"] == run_id and analysis["evidence"]
    assert analysis["agents"]["opportunity_prioritization"]["result"]["data"]["recommendations"]
    assert (await client.get(f"/api/runs/{run_id}/export/secrets.csv", headers=h)).status_code == 404
    assert (await client.get(f"/api/runs/{run_id}/export/matrix.json", headers=h)).status_code == 404
    assert await _count(AuditLog, AuditLog.action == "run.exported") == 6

    # Other organizations can't export (404, not 403).
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.get(f"/api/runs/{run_id}/export/matrix.csv", headers=other)).status_code == 404

    # By default every role may export; an admin can raise the bar.
    _, viewer = await _as(client, h, "viewer@acme-consulting.com", "viewer")
    assert (await client.get(f"/api/runs/{run_id}/export/gaps.csv", headers=viewer)).status_code == 200
    s = (await client.put("/api/org/settings", headers=h, json={"export_min_role": "analyst"})).json()
    assert s["settings"]["export_min_role"] == "analyst"
    for url in ("export/gaps.csv", "report.md", "sales-summary.md", "outreach.eml"):
        assert (await client.get(f"/api/runs/{run_id}/{url}", headers=viewer)).status_code == 403, url
    assert (await client.get(f"/api/runs/{run_id}/report", headers=viewer)).status_code == 200  # viewing is fine
    assert (await client.get("/api/org/settings", headers=viewer)).json()["can_export"] is False
    assert (await client.put("/api/org/settings", headers=viewer, json={"export_min_role": "viewer"})).status_code == 403

    # Restrict exports to the sales and BD functions: analysts need one of them; admins always can.
    await client.put("/api/org/settings", headers=h, json={"export_job_functions": ["sales", "business_development"]})
    _, product = await _as(client, h, "pm@acme-consulting.com", "analyst", "product")
    _, sales = await _as(client, h, "ae@acme-consulting.com", "analyst", "sales")
    assert (await client.get(f"/api/runs/{run_id}/report.md", headers=product)).status_code == 403
    assert (await client.get(f"/api/runs/{run_id}/report.md", headers=sales)).status_code == 200
    assert (await client.get(f"/api/runs/{run_id}/export/matrix.csv", headers=h)).status_code == 200

    for bad in ({"export_min_role": "owner"}, {"export_job_functions": ["chef"]}, {"retention_days": 2},
                {"retention_days": "30"}, {"bogus": 1}):
        assert (await client.put("/api/org/settings", headers=h, json=bad)).status_code == 422, bad


async def test_job_functions_on_users(client):  # noqa: F811
    h = await register(client)
    uid = await add_user(client, h, "bd@acme-consulting.com", "viewer")
    r = await client.patch(f"/api/users/{uid}", headers=h, json={"job_function": "business_development"})
    assert r.json()["job_function"] == "business_development"
    assert (await client.patch(f"/api/users/{uid}", headers=h, json={"job_function": "wizard"})).status_code == 422
    assert (await client.patch(f"/api/users/{uid}", headers=h, json={"job_function": ""})).json()["job_function"] is None
    r = await client.post("/api/auth/users", headers=h, json={"email": "tech@acme-consulting.com", "password": "s3cure-password!",
                                                               "role": "analyst", "job_function": "technical"})
    assert r.status_code == 201 and r.json()["job_function"] == "technical"


async def test_deleting_runs_projects_and_clients(client):  # noqa: F811
    h = await register(client)
    project_id, run_id = await _analysed(client, h)
    assert await _count(EvidenceRecord, EvidenceRecord.run_id == run_id) > 0
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.delete(f"/api/runs/{run_id}", headers=other)).status_code == 404
    _, analyst = await _as(client, h, "analyst@acme-consulting.com", "analyst")
    assert (await client.delete(f"/api/runs/{run_id}", headers=analyst)).status_code == 403  # not their run
    _, viewer = await _as(client, h, "viewer@acme-consulting.com", "viewer")
    assert (await client.delete(f"/api/projects/{project_id}", headers=viewer)).status_code == 403

    # An analyst can delete a run they started; everything derived from it goes too.
    mine = (await client.post(f"/api/runs/{run_id}/rerun", headers=analyst, json={"stages": ["report"]})).json()["run_id"]
    await runner.wait(mine)
    assert (await client.get(f"/api/runs/{mine}", headers=h)).json()["parent_run_id"] == run_id
    assert (await client.delete(f"/api/runs/{mine}", headers=analyst)).status_code == 204
    assert (await client.get(f"/api/runs/{mine}", headers=h)).status_code == 404
    assert await _count(EvidenceRecord, EvidenceRecord.run_id == mine) == 0
    assert await _count(AgentExecution, AgentExecution.run_id == mine) == 0
    assert await _count(EvidenceRecord, EvidenceRecord.run_id == run_id) > 0  # the original is untouched

    # A run in progress can't be deleted.
    async with db.sessionmaker()() as s:
        run = await s.get(AnalysisRun, run_id)
        run.status = "running"
        await s.commit()
    assert (await client.delete(f"/api/runs/{run_id}", headers=h)).status_code == 409
    assert (await client.delete(f"/api/projects/{project_id}", headers=h)).status_code == 409
    async with db.sessionmaker()() as s:
        (await s.get(AnalysisRun, run_id)).status = "completed"
        await s.commit()

    client_id = (await client.get(f"/api/projects/{project_id}", headers=h)).json()["client_id"]
    assert (await client.delete(f"/api/projects/{project_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/projects/{project_id}", headers=h)).status_code == 404
    assert (await client.get(f"/api/runs/{run_id}", headers=h)).status_code == 404
    assert await _count(EvidenceRecord, EvidenceRecord.run_id == run_id) == 0
    assert (await client.delete(f"/api/clients/{client_id}", headers=other)).status_code == 404
    assert (await client.delete(f"/api/clients/{client_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/clients/{client_id}", headers=h)).status_code == 404
    actions = {a for a, in (await _rows(select(AuditLog.action)))}
    assert {"run.deleted", "project.deleted", "client.deleted"} <= actions


async def _rows(stmt):
    async with db.sessionmaker()() as s:
        return (await s.execute(stmt)).all()


async def test_retention_purges_old_runs_but_keeps_the_latest_analysis(client):  # noqa: F811
    h = await register(client)
    project_id, first = await _analysed(client, h)
    second = (await client.post(f"/api/runs/{first}/rerun", headers=h, json={"stages": ["report"]})).json()["run_id"]
    await runner.wait(second)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    async with db.sessionmaker()() as s:
        for rid in (first, second):
            (await s.get(AnalysisRun, rid)).updated_at = old
        await s.commit()

    assert (await client.post("/api/org/retention/purge", headers=h)).status_code == 409  # no policy yet
    await client.put("/api/org/settings", headers=h, json={"retention_days": 30})
    preview = (await client.post("/api/org/retention/purge?dry_run=true", headers=h)).json()
    assert preview["run_ids"] == [first]  # the latest completed run of the project is kept
    assert (await client.get(f"/api/runs/{first}", headers=h)).status_code == 200
    done = (await client.post("/api/org/retention/purge", headers=h)).json()
    assert done["runs"] == 1
    assert (await client.get(f"/api/runs/{first}", headers=h)).status_code == 404
    assert (await client.get(f"/api/runs/{second}", headers=h)).json()["parent_run_id"] is None

    # The scheduler applies the policy too; without keep-latest the remaining old run goes.
    await client.put("/api/org/settings", headers=h, json={"retention_keep_latest": False})
    purged = await governance.purge_all(min_interval=timedelta(0))
    assert list(purged.values()) == [1]
    assert (await client.get(f"/api/runs/{second}", headers=h)).status_code == 404
    assert (await client.get(f"/api/projects/{project_id}", headers=h)).status_code == 200


async def test_run_notifications_in_app_and_email(client, channels):  # noqa: F811
    h = await register(client)
    _, viewer = await _as(client, h, "viewer@acme-consulting.com", "viewer")
    # The viewer follows every run in the organization.
    prefs = (await client.put("/api/notifications/preferences", headers=viewer, json={"scope": "all"})).json()
    assert prefs["scope"] == "all" and prefs["events"]["run_failed"]["email"] is True
    assert (await client.put("/api/notifications/preferences", headers=viewer,
                             json={"events": {"nope": {"email": True}}})).status_code == 422

    project_id, run_id = await _analysed(client, h, gates=["external_research", "repository_access"])
    mine = (await client.get("/api/notifications", headers=h)).json()
    assert [n["event"] for n in mine] == ["approval_needed"] and "Pending:" in mine[0]["body"]
    assert (await client.get("/api/notifications/unread-count", headers=h)).json()["count"] == 1
    assert [n["event"] for n in (await client.get("/api/notifications", headers=viewer)).json()] == ["approval_needed"]
    assert len(channels["email"]) == 2  # approval_needed is emailed by default
    assert channels["email"][0]["Subject"].startswith("Analysis waiting for your approval")

    # Completion: in-app by default; the owner also asked for email. Outreach drafts are announced too.
    await client.put("/api/notifications/preferences", headers=h, json={"events": {"run_completed": {"email": True}}})
    pending = [a for a in (await client.get(f"/api/runs/{run_id}", headers=h)).json()["approvals"] if a["status"] == "pending"]
    for a in pending:
        await client.post(f"/api/runs/{run_id}/approvals/{a['id']}", headers=h, json={"approve": True})
    await runner.wait(run_id)
    events = {n["event"] for n in (await client.get("/api/notifications", headers=h)).json()}
    assert {"approval_needed", "run_completed"} <= events
    assert any(m["To"] == "lead@acme-consulting.com" and m["Subject"].startswith("Analysis completed")
               for m in channels["email"])
    assert not any(m["To"] == "viewer@acme-consulting.com" and m["Subject"].startswith("Analysis completed")
                   for m in channels["email"])

    # Read state is per user.
    first = (await client.get("/api/notifications", headers=h)).json()[0]
    assert (await client.post(f"/api/notifications/{first['id']}/read", headers=viewer)).status_code == 404
    await client.post(f"/api/notifications/{first['id']}/read", headers=h)
    assert (await client.get("/api/notifications?unread_only=true", headers=h)).json()[0]["id"] != first["id"]
    await client.post("/api/notifications/read-all", headers=h)
    assert (await client.get("/api/notifications/unread-count", headers=h)).json()["count"] == 0

    # Restricting the project hides its notifications from people outside it, and stops new ones.
    await client.put(f"/api/projects/{project_id}/access", headers=h, json={"restricted": True, "member_ids": []})
    assert (await client.get("/api/notifications", headers=viewer)).json() == []
    rerun = (await client.post(f"/api/runs/{run_id}/rerun", headers=h, json={"stages": ["report"]})).json()["run_id"]
    await runner.wait(rerun)
    assert await _count(Notification, Notification.run_id == rerun, Notification.event == "run_completed") == 1
