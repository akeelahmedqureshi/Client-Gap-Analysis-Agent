"""Monitoring: change detection, scheduled runs with standing approvals, alerts and notifications."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from test_api import client, register, upload_and_import  # noqa: F401  (fixture re-export)
from test_security_api import add_user, bearer, login

from cip.connectors.research.web import WebFetcher
from cip.db import session as db
from cip.db.models import AuditLog, Monitor
from cip.services import monitoring, notify
from cip.services.changes import diff_outputs
from cip.services.runner import runner

from fakes import SITES, FakeSearch, FakeSourceControl, html, web_transport

ALL_GATES = ["client_report", "external_research", "large_repository_scan", "repository_access"]
LATER = datetime.now(timezone.utc) + timedelta(days=8)


# --------------------------------------------------------------------------- pure diff


def _cmp(cid, name, url, features=(), price=None):
    return {"id": cid, "name": name, "url": url, "classification": "direct", "description": "",
            "features": [{"feature_id": f, "status": "available", "evidence_ids": [f"ev_{f}"]} for f in features],
            "evidence_ids": [f"ev_{cid}"]}


def _outputs(cmp_ids, comps, rows, gaps, price, issues, site_checked=True, announcements=()):
    return {
        "competitor_research": {"competitors": comps},
        "feature_comparison": {"rows": [{"feature_id": f, "feature_name": f.title(), "client": client_status,
                                         "competitors": dict(zip(cmp_ids, sts))} for f, client_status, sts in rows]},
        "pricing_analysis": {"client": {"entry_price_monthly": 79.0, "currency": "USD"}, "position": "above",
                             "market": {}, "competitors": [
                                 {"competitor_id": cmp_ids[0], "name": comps[0]["name"], "currency": "USD",
                                  "entry_price_monthly": price, "evidence_ids": ["ev_price"]}]},
        "gap_analysis": {"gaps": [{"feature_id": g, "name": g.title(), "gap_type": "missing", "category": "X",
                                   "evidence_ids": [f"ev_gap_{g}"]} for g in gaps],
                         "existing": [{"feature_id": "sms", "status": "available", "evidence_ids": ["ev_sms"]}]},
        "security_review": {"enabled": True, "score": 70, "grade": "C",
                            "site": {"checked": site_checked}, "scope": ["1 declared dependencies of a/b checked "
                                                                         "against OSV.dev"],
                            "issues": [{"key": k, "title": k, "severity": sev, "category": cat,
                                        "evidence_id": f"ev_{k}"} for k, sev, cat in issues]},
        "client_research": {"announcements": [{"title": t, "url": f"https://x.test/{t}", "is_product": True,
                                               "evidence_id": f"ev_{t}"} for t in announcements],
                            "hiring": {"job_count": 4, "signals": []}},
    }


def test_diff_detects_changes_and_matches_competitors_by_domain():
    prev = _outputs(["c1", "c2"], [_cmp("c1", "ClinicFlow", "https://clinicflow.com"),
                                   _cmp("c2", "OldCo", "https://oldco.io")],
                    [("ai", "missing", ["unknown", "unknown"])], ["sms", "ai"], 49.0,
                    [("hsts", "medium", "web"), ("dep-1", "high", "dependency")])
    # Same competitors get fresh ids in every run: matching must use the domain.
    cur = _outputs(["n1", "n3"], [_cmp("n1", "ClinicFlow", "https://www.clinicflow.com/", features=["ai"]),
                                  _cmp("n3", "NewCo", "https://newco.ai")],
                   [("ai", "missing", ["available", "unknown"])], ["ai", "telehealth"], 39.0,
                   [("dep-1", "high", "dependency"), ("dep-2", "critical", "dependency")],
                   announcements=["launch"])
    changes = diff_outputs(prev, cur)
    by_kind = {}
    for c in changes:
        by_kind.setdefault(c["kind"], []).append(c)
    assert [c["subject"] for c in by_kind["competitor_new"]] == ["NewCo"]
    assert [c["subject"] for c in by_kind["competitor_dropped"]] == ["OldCo"]
    feat = by_kind["competitor_feature"][0]
    assert feat["severity"] == "warning" and feat["evidence_ids"] == ["ev_ai"] and "ClinicFlow" in feat["title"]
    price = by_kind["competitor_price"][0]
    assert price["before"] == 49.0 and price["after"] == 39.0 and "cut" in price["title"]
    assert [c["title"] for c in by_kind["gap_new"]] == ["New gap: Telehealth"]
    closed = by_kind["gap_closed"][0]
    assert closed["title"] == "Gap closed: Sms" and closed["evidence_ids"] == ["ev_sms"]
    assert [c["severity"] for c in by_kind["security_new"]] == ["critical"]
    assert by_kind["client_announcement"][0]["evidence_ids"] == ["ev_launch"]
    # Removed web issue is NOT reported as resolved only if the site was checked both times (it was here).
    assert [c["title"] for c in by_kind["security_resolved"]] == ["Security issue resolved: hsts"]
    assert changes[0]["severity"] == "critical"  # sorted most severe first


def test_diff_ignores_categories_not_inspected_in_both_runs():
    prev = _outputs(["c1"], [_cmp("c1", "ClinicFlow", "https://clinicflow.com")], [], ["ai"], 49.0,
                    [("hsts", "medium", "web")])
    cur = _outputs(["c1"], [_cmp("c1", "ClinicFlow", "https://clinicflow.com")], [], ["ai"], 49.0, [],
                   site_checked=False)
    del cur["competitor_research"]  # e.g. the research step was skipped this time
    changes = diff_outputs(prev, cur)
    assert not any(c["kind"].startswith("competitor") for c in changes)
    assert not any(c["kind"].startswith("security") for c in changes)  # site not checked this time


def test_slack_text_is_escaped():
    alert = {"severity": "warning", "title": "<!channel> New competitor: <https://evil.test|Click>",
             "run_id": "run_1", "changes": [{"severity": "info", "title": "a & b"}]}
    text = notify.alert_text(alert, "Proj", "https://app.test/runs/run_1", markup=True)
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "a &amp; b" in text
    assert text.endswith("<https://app.test/runs/run_1|Open the analysis>")


# --------------------------------------------------------------------------- end to end


@pytest.fixture
def channels(monkeypatch):
    """Capture webhook posts and emails."""
    sent = {"webhook": [], "email": []}

    def hook(request: httpx.Request) -> httpx.Response:
        sent["webhook"].append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(notify, "webhook_transport", httpx.MockTransport(hook))
    monkeypatch.setattr(notify, "_smtp_send", lambda settings, msg: sent["email"].append(msg))
    monkeypatch.setenv("CIP_SMTP_HOST", "smtp.test")
    from cip.config import get_settings
    get_settings.cache_clear()
    yield sent
    get_settings.cache_clear()


def _use_sites(sites):
    def hook(ctx):
        ctx.fetcher = WebFetcher(transport=web_transport(sites))
        ctx.search = FakeSearch()
        ctx.source_control_factory = lambda ref, token: FakeSourceControl(ref, token)
    runner.context_hook = hook


async def _baseline_run(c, h, project_id):
    r = await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": [
        "external_research", "repository_access", "client_report"]})
    run_id = r.json()["run_id"]
    await runner.wait(run_id)
    assert (await c.get(f"/api/runs/{run_id}", headers=h)).json()["status"] == "completed"
    return run_id


async def test_scheduled_run_detects_changes_and_notifies(client, channels):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    sites = dict(SITES)
    _use_sites(sites)
    baseline = await _baseline_run(client, h, project_id)

    r = await client.put(f"/api/projects/{project_id}/monitor", headers=h, json={
        "frequency": "weekly", "standing_approvals": ALL_GATES, "min_severity": "info",
        "notify_emails": ["pm@acme-consulting.com"],
        "webhook_url": "https://hooks.slack.test/services/T000/B000/secretpart"})
    assert r.status_code == 200, r.text
    mon = r.json()
    assert mon["webhook"] == "https://hooks.slack.test/…" and "secretpart" not in r.text
    assert mon["approvals_valid"] and mon["next_run_at"]

    # The world changes: a competitor cuts its price, the client publishes a new announcement.
    sites["https://clinicflow.com/pricing"] = SITES["https://clinicflow.com/pricing"].replace("$49", "$39")
    sites["https://abc-healthcare.com/blog"] = SITES["https://abc-healthcare.com/blog"].replace(
        "<a href='/blog/page/2'>", "<a href='/blog/introducing-video-visits'>Introducing video visits</a>"
                                   "<a href='/blog/page/2'>")
    sites["https://abc-healthcare.com/blog/introducing-video-visits"] = html("Video visits", "<p>Video.</p>")

    assert await monitoring.tick(now=datetime.now(timezone.utc)) == []  # not due yet
    started = await monitoring.tick(now=LATER)
    assert len(started) == 1
    run_id = started[0]
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["status"] == "completed"  # standing approvals: no human gate on the scheduled run
    assert all(a["status"] == "approved" for a in run["approvals"])

    changes = (await client.get(f"/api/runs/{run_id}/changes", headers=h)).json()
    assert changes["baseline_run_id"] == baseline
    kinds = {c["kind"]: c for c in changes["changes"]}
    assert kinds["competitor_price"]["before"] == 49.0 and kinds["competitor_price"]["after"] == 39.0
    assert "video visits" in kinds["client_announcement"]["title"].lower()
    # Every cited evidence id exists in the new run's ledger.
    ev = {e["id"] for e in (await client.get(f"/api/runs/{run_id}/evidence", headers=h)).json()}
    assert all(set(c["evidence_ids"]) <= ev for c in changes["changes"])

    alerts = (await client.get("/api/alerts", headers=h)).json()
    assert len(alerts) == 1 and alerts[0]["kind"] == "changes" and alerts[0]["run_id"] == run_id
    assert {n["channel"]: n["ok"] for n in alerts[0]["notifications"]} == {"webhook": True, "email": True}
    url, payload = channels["webhook"][0]
    assert url.endswith("/secretpart") and "cut its entry price" in payload["text"]
    assert payload["alert"]["url"].endswith(f"/runs/{run_id}")
    assert channels["email"][0]["To"] == "pm@acme-consulting.com"

    assert (await client.get("/api/alerts/unread-count", headers=h)).json()["count"] == 1
    assert (await client.post(f"/api/alerts/{alerts[0]['id']}/read", headers=h)).status_code == 204
    assert (await client.get("/api/alerts/unread-count", headers=h)).json()["count"] == 0

    # The schedule moved on: the same moment is no longer due.
    assert await monitoring.tick(now=LATER) == []
    async with db.sessionmaker()() as s:
        actions = (await s.execute(select(AuditLog.action))).scalars().all()
        assert "monitor.created" in actions and "monitor.run_started" in actions
        assert "secretpart" not in json.dumps([a.details for a in (await s.execute(select(AuditLog))).scalars()])
        stored = (await s.execute(select(Monitor))).scalar_one()
        assert "secretpart" not in stored.encrypted_webhook_url


async def test_unapproved_gate_pauses_and_alerts_and_no_pile_up(client, channels):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    _use_sites(SITES)
    await client.put(f"/api/projects/{project_id}/monitor", headers=h, json={
        "standing_approvals": ["external_research", "repository_access"], "min_severity": "warning",
        "webhook_url": "https://hooks.slack.test/x"})
    run_id = (await monitoring.tick(now=LATER))[0]
    await runner.wait(run_id)
    assert (await client.get(f"/api/runs/{run_id}", headers=h)).json()["status"] == "awaiting_approval"
    alerts = (await client.get("/api/alerts", headers=h)).json()
    assert [a["kind"] for a in alerts] == ["approval_needed"]
    assert "client-facing report" in alerts[0]["summary"]
    assert len(channels["webhook"]) == 1
    # Still waiting: the next cycle does not start another run.
    assert await monitoring.tick(now=LATER + timedelta(days=8)) == []
    assert (await client.post(f"/api/projects/{project_id}/monitor/run-now", headers=h)).status_code == 409


async def test_standing_approvals_lapse_when_approver_is_deactivated(client):  # noqa: F811
    admin = await register(client)
    project_id = await upload_and_import(client, admin)
    _use_sites(SITES)
    await add_user(client, admin, "ana@acme-consulting.com", role="analyst")
    ana = bearer(await login(client, "ana@acme-consulting.com"))
    r = await client.put(f"/api/projects/{project_id}/monitor", headers=ana,
                         json={"standing_approvals": ALL_GATES})
    assert r.json()["approvals_valid"]
    users = (await client.get("/api/users", headers=admin)).json()
    ana_id = next(u["id"] for u in users if u["email"] == "ana@acme-consulting.com")
    await client.patch(f"/api/users/{ana_id}", headers=admin, json={"is_active": False})
    assert (await client.get(f"/api/projects/{project_id}/monitor", headers=admin)).json()["approvals_valid"] is False
    run_id = (await monitoring.tick(now=LATER))[0]
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=admin)).json()
    assert run["status"] == "awaiting_approval"
    assert {a["gate"] for a in run["approvals"] if a["status"] == "pending"} == {"external_research",
                                                                                  "repository_access"}


async def test_concurrent_ticks_start_one_run(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    await client.put(f"/api/projects/{project_id}/monitor", headers=h, json={"standing_approvals": ALL_GATES})
    started: list[str] = []
    results = await asyncio.gather(*(monitoring.tick(now=LATER, start=started.append) for _ in range(4)))
    assert sum(len(r) for r in results) == 1 and len(started) == 1


async def test_monitor_access_control_and_validation(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    other = await register(client, org="Other Org", email="boss@other-org.com")
    assert (await client.put(f"/api/projects/{project_id}/monitor", headers=other, json={})).status_code == 404
    await add_user(client, h, "vi@acme-consulting.com", role="viewer")
    viewer = bearer(await login(client, "vi@acme-consulting.com"))
    assert (await client.put(f"/api/projects/{project_id}/monitor", headers=viewer, json={})).status_code == 403
    bad = [{"webhook_url": "http://hooks.slack.test/x"}, {"webhook_url": "https://user:pw@hooks.test/x"},
           {"standing_approvals": ["everything"]}, {"frequency": "hourly"}, {"notify_emails": ["not-an-email"]},
           {"scoring_weights": {"nope": 1}}]
    for body in bad:
        assert (await client.put(f"/api/projects/{project_id}/monitor", headers=h, json=body)).status_code == 422, body
    assert (await client.put(f"/api/projects/{project_id}/monitor", headers=h, json={})).status_code == 200
    assert (await client.get(f"/api/projects/{project_id}/monitor", headers=viewer)).status_code == 200
    assert (await client.get(f"/api/projects/{project_id}/monitor", headers=other)).status_code == 404
    assert (await client.get("/api/monitors", headers=other)).json() == []
    assert (await client.delete(f"/api/projects/{project_id}/monitor", headers=other)).status_code == 404
    assert (await client.delete(f"/api/projects/{project_id}/monitor", headers=h)).status_code == 204
    assert (await client.get(f"/api/projects/{project_id}/monitor", headers=h)).status_code == 404
