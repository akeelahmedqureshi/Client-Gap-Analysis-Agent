"""CRM, marketing and email integrations: only approved, client-facing content leaves the platform."""

import hashlib
import hmac
import json

import httpx
import pytest

from cip.services import integrations, notify
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_security_api import add_user, bearer, login

GATES = ["external_research", "repository_access", "client_report"]
TOKEN = "pat-na1-" + "0123456789abcdef0123456789abcdef"
# Fake key, assembled at runtime so no key-shaped literal lives in the source tree (secret scanning).
MC_KEY = "0123456789abcdef" * 2 + "-" + "us21"


@pytest.fixture
def external(monkeypatch):
    """Fake HubSpot, Mailchimp, webhook receiver and SMTP; records every request."""
    seen: dict = {"requests": [], "emails": [], "fail": set()}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen["requests"].append({"method": request.method, "url": str(request.url), "body": body,
                                 "headers": dict(request.headers), "raw": request.content})
        host, path = request.url.host, request.url.path
        if host in seen["fail"]:
            return httpx.Response(401, json={"message": "invalid token"})
        if host == "api.hubapi.com":
            if path.endswith("/search"):
                return httpx.Response(200, json={"results": []})
            kind = path.rstrip("/").split("/")[-1]
            return httpx.Response(201, json={"id": f"{kind}-1"}) if request.method == "POST" else httpx.Response(200, json={"results": []})
        if host == "us21.api.mailchimp.com":
            return httpx.Response(200, json={"id": "member-1", "status": "pending"} if request.method == "PUT" else {})
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(integrations, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(notify, "_smtp_send", lambda settings, msg: seen["emails"].append(msg))
    monkeypatch.setenv("CIP_SMTP_HOST", "smtp.test")
    from cip.config import get_settings
    get_settings.cache_clear()
    yield seen
    get_settings.cache_clear()


async def _analysed(c, h) -> tuple[str, str]:
    project_id = await upload_and_import(c, h)
    r = (await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": GATES})).json()
    await runner.wait(r["run_id"])
    await c.get(f"/api/runs/{r['run_id']}/sales", headers=h)  # creates the sales documents
    return project_id, r["run_id"]


async def test_crm_hubspot_opportunity_from_the_approved_summary(client, external):  # noqa: F811
    h = await register(client)
    _, run_id = await _analysed(client, h)
    await add_user(client, h, "analyst@acme-consulting.com", "analyst")
    analyst = bearer(await login(client, "analyst@acme-consulting.com"))

    assert (await client.put("/api/integrations/crm", headers=analyst, json={"provider": "hubspot", "secret": TOKEN})).status_code == 403
    assert (await client.put("/api/integrations/crm", headers=h, json={"provider": "hubspot"})).status_code == 422
    saved = (await client.put("/api/integrations/crm", headers=h, json={"provider": "hubspot", "secret": TOKEN})).json()
    assert saved["has_secret"] and TOKEN not in json.dumps(saved)
    listed = (await client.get("/api/integrations", headers=analyst)).json()
    assert listed["crm"]["provider"] == "hubspot" and TOKEN not in json.dumps(listed) and listed["email_sending"]
    assert (await client.post("/api/integrations/crm/test", headers=h)).json()["ok"]

    # Only an approved summary can be sent, and reviewer notes never leave.
    assert (await client.post(f"/api/runs/{run_id}/crm", headers=analyst)).status_code == 409
    await client.patch(f"/api/runs/{run_id}/sales/summary", headers=analyst, json={"reviewer_notes": "INTERNAL-NOTE-42"})
    await client.post(f"/api/runs/{run_id}/sales/summary/approve", headers=analyst, json={})
    external["requests"].clear()
    sync = (await client.post(f"/api/runs/{run_id}/crm", headers=analyst)).json()
    assert sync["status"] == "ok" and sync["external_ids"]["deal_id"] == "deals-1" and not sync["automatic"]
    reqs = external["requests"]
    paths = [(r["method"], r["url"].split("hubapi.com")[1]) for r in reqs]
    assert ("POST", "/crm/v3/objects/companies") in paths and ("POST", "/crm/v3/objects/deals") in paths
    assert ("POST", "/crm/v3/objects/notes") in paths and ("POST", "/crm/v3/objects/contacts") in paths
    assert all(r["headers"]["authorization"] == f"Bearer {TOKEN}" for r in reqs)
    deal = next(r for r in reqs if r["url"].endswith("/deals"))
    assert deal["body"]["associations"][0]["to"]["id"] == "companies-1"
    company = next(r for r in reqs if r["url"].endswith("/companies"))
    assert company["body"]["properties"]["domain"] == "abc-healthcare.com"
    sent = json.dumps([r["body"] for r in reqs])
    assert "INTERNAL-NOTE-42" not in sent and "internal_capabilities" not in sent

    # A provider failure is recorded, not raised.
    external["fail"].add("api.hubapi.com")
    failed = (await client.post(f"/api/runs/{run_id}/crm", headers=analyst)).json()
    assert failed["status"] == "failed" and "401" in failed["error"]
    history = (await client.get(f"/api/runs/{run_id}/integrations", headers=analyst)).json()
    assert [s["status"] for s in history] == ["failed", "ok"]
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.post(f"/api/runs/{run_id}/crm", headers=other)).status_code == 404


async def test_crm_webhook_is_signed_and_opportunities_can_be_automatic(client, external):  # noqa: F811
    h = await register(client)
    _, run_id = await _analysed(client, h)
    assert (await client.put("/api/integrations/crm", headers=h, json={
        "provider": "webhook", "config": {"url": "http://crm.example/hook"}})).status_code == 422
    secret = "whsec-test-signing"
    saved = (await client.put("/api/integrations/crm", headers=h, json={
        "provider": "webhook", "secret": secret, "config": {"url": "https://hooks.example.com/cip/abc123"},
        "auto_create": True})).json()
    assert saved["auto_create"] and saved["config"]["url"] == "https://hooks.example.com/…"
    external["requests"].clear()
    await client.post(f"/api/runs/{run_id}/sales/summary/approve", headers=h, json={})  # creates it automatically
    hook = external["requests"][-1]
    assert hook["body"]["event"] == "opportunity.created"
    data = hook["body"]["data"]
    assert data["client"] == "ABC Healthcare" and data["top_improvements"] and data["analysis_url"].endswith(run_id)
    ts = hook["headers"]["x-cip-timestamp"]
    expected = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + hook["raw"], hashlib.sha256).hexdigest()
    assert hook["headers"]["x-cip-signature"] == expected
    syncs = (await client.get(f"/api/runs/{run_id}/integrations", headers=h)).json()
    assert syncs[0]["automatic"] and syncs[0]["status"] == "ok"


async def test_marketing_contact_and_outreach_sending(client, external):  # noqa: F811
    h = await register(client)
    project_id, run_id = await _analysed(client, h)
    assert (await client.put("/api/integrations/marketing", headers=h, json={
        "provider": "mailchimp", "secret": "abc-notadc", "config": {"list_id": "a1b2c3d4"}})).status_code == 422
    await client.put("/api/integrations/marketing", headers=h, json={
        "provider": "mailchimp", "secret": MC_KEY, "config": {"list_id": "a1b2c3d4"}})
    await client.post(f"/api/runs/{run_id}/sales/summary/approve", headers=h, json={})
    external["requests"].clear()
    sync = (await client.post(f"/api/runs/{run_id}/marketing", headers=h)).json()
    assert sync["status"] == "ok"
    put = next(r for r in external["requests"] if r["method"] == "PUT")
    member = hashlib.md5(b"ops@abc-healthcare.com").hexdigest()
    assert put["url"] == f"https://us21.api.mailchimp.com/3.0/lists/a1b2c3d4/members/{member}"
    assert put["body"]["status_if_new"] == "pending"  # double opt-in: no one is subscribed without consent
    tags = next(r for r in external["requests"] if r["url"].endswith("/tags"))["body"]["tags"]
    assert {"name": "cip-analysed", "status": "active"} in tags

    # Outreach: approved, confirmed, once per version; replies go to the sender.
    assert (await client.post(f"/api/runs/{run_id}/outreach/send", headers=h, json={"confirm": True})).status_code == 409
    await client.post(f"/api/runs/{run_id}/outreach/approve", headers=h, json={"acknowledge_warnings": True})
    assert (await client.post(f"/api/runs/{run_id}/outreach/send", headers=h, json={})).status_code == 422
    sent = (await client.post(f"/api/runs/{run_id}/outreach/send", headers=h, json={"confirm": True})).json()
    assert sent["status"] == "ok" and sent["action"] == "email.sent"
    msg = external["emails"][-1]
    assert msg["To"] == "ops@abc-healthcare.com" and "lead@acme-consulting.com" in msg["Reply-To"]
    assert (await client.post(f"/api/runs/{run_id}/outreach/send", headers=h, json={"confirm": True})).status_code == 409
    again = await client.post(f"/api/runs/{run_id}/outreach/send", headers=h, json={"confirm": True, "resend": True})
    assert again.status_code == 200 and len(external["emails"]) == 2

    # Deleting the project removes its sync records with it.
    assert (await client.delete(f"/api/projects/{project_id}", headers=h)).status_code == 204
    assert (await client.delete("/api/integrations/marketing", headers=h)).status_code == 204
    assert (await client.get("/api/integrations", headers=h)).json()["marketing"] is None
