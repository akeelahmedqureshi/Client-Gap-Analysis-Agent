"""CRM and email / marketing integrations (BRS Phase 3: CRM integration, automatic opportunity creation,
email / marketing-system integration).

One integration per organization and kind:

* ``crm`` — **HubSpot** (private-app token: company by domain, deal associated to it, note with the
  summary, the business contact) or a **webhook** (signed JSON ``opportunity.created`` event for Salesforce,
  Pipedrive, Zapier, Make… through their own inbound hooks). With ``auto_create`` the opportunity is created
  as soon as a person approves the run's sales summary.
* ``marketing`` — **Mailchimp** (audience member upserted with tags; new contacts get status ``pending`` by
  default, i.e. Mailchimp's double opt-in, so nobody is subscribed without consent) or a **webhook**
  (``contact.upserted`` event).

What leaves the platform is only the *approved* sales summary's client-facing content: no internal-only
knowledge-base items, no reviewer notes, no evidence quotes and never a credential. Credentials and webhook URLs
are Fernet-encrypted at rest and never returned. Webhooks go to public HTTPS endpoints only (SSRF guard, no
redirects) and carry ``X-CIP-Signature: sha256=<HMAC of timestamp.body>`` so the receiver can verify them.
Every push is recorded in ``integration_syncs`` and the audit log; failures are recorded, never raised into
the analysis.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from cip.config import get_settings
from cip.connectors.research.web import UnsafeURL, assert_public_url
from cip.core.security.crypto import TokenCipher
from cip.db.models import Integration
from cip.services.notify import mask_webhook_url, validate_webhook_url

log = logging.getLogger(__name__)

# Tests inject an httpx.MockTransport here (which also bypasses DNS-based SSRF checks).
transport: httpx.AsyncBaseTransport | None = None

PROVIDERS = {"crm": ("hubspot", "webhook"), "marketing": ("mailchimp", "webhook")}
HUBSPOT_API = "https://api.hubapi.com"
MAILCHIMP_DC = re.compile(r"^[a-z]{2}\d{1,3}$")
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
# HubSpot default association types (HUBSPOT_DEFINED).
DEAL_TO_COMPANY, NOTE_TO_DEAL, CONTACT_TO_COMPANY = 5, 214, 1


class IntegrationError(Exception):
    pass


# --------------------------------------------------------------------------- configuration


def validate(kind: str, provider: str, secret: str | None, config: dict, existing: Integration | None) -> dict:
    if kind not in PROVIDERS or provider not in PROVIDERS[kind]:
        raise IntegrationError(f"Provider must be one of {PROVIDERS.get(kind, ())}")
    clean: dict = {}
    if provider == "webhook":
        url = config.get("url")
        if url:
            clean["url"] = validate_webhook_url(url)
        elif not (existing and existing.provider == "webhook" and existing.encrypted_config):
            raise IntegrationError("A webhook needs an https:// URL")
    if provider == "hubspot":
        clean["pipeline"] = str(config.get("pipeline") or "default")[:100]
        clean["dealstage"] = str(config.get("dealstage") or "appointmentscheduled")[:100]
    if provider == "mailchimp":
        list_id = str(config.get("list_id") or "")
        if not re.fullmatch(r"[a-z0-9]{4,20}", list_id):
            raise IntegrationError("Mailchimp needs the audience (list) id")
        clean["list_id"] = list_id
        status = config.get("status_if_new") or "pending"
        if status not in ("pending", "subscribed", "transactional"):
            raise IntegrationError("status_if_new must be pending (double opt-in), subscribed or transactional")
        clean["status_if_new"] = status
        key = secret or ""
        if key and not MAILCHIMP_DC.match(key.rsplit("-", 1)[-1]):
            raise IntegrationError("A Mailchimp API key ends with its data centre, e.g. …-us21")
    if provider in ("hubspot", "mailchimp") and not secret and not (existing and existing.provider == provider
                                                                       and existing.encrypted_secret):
        raise IntegrationError("An API token is required")
    return clean


def public_view(i: Integration | None) -> dict | None:
    if i is None:
        return None
    cfg = _config(i)
    shown = {k: v for k, v in cfg.items() if k != "url"}
    if cfg.get("url"):
        shown["url"] = mask_webhook_url(cfg["url"])
    return {"kind": i.kind, "provider": i.provider, "enabled": i.enabled, "auto_create": i.auto_create,
            "config": shown, "has_secret": bool(i.encrypted_secret), "updated_at": i.updated_at}


def _config(i: Integration) -> dict:
    return json.loads(TokenCipher().decrypt(i.encrypted_config)) if i.encrypted_config else {}


def _secret(i: Integration) -> str | None:
    return TokenCipher().decrypt(i.encrypted_secret) if i.encrypted_secret else None


def store(i: Integration, secret: str | None, config: dict) -> None:
    merged = {**_config(i), **config} if i.encrypted_config else config
    i.encrypted_config = TokenCipher().encrypt(json.dumps(merged))
    if secret:
        i.encrypted_secret = TokenCipher().encrypt(secret)


# --------------------------------------------------------------------------- payloads


def opportunity(summary: dict, project_name: str, run_id: str, industry: str | None, domain: str | None) -> dict:
    """The client-facing part of an approved sales summary."""
    s = get_settings()
    imp = summary.get("top_improvements", [])
    opp = lambda o: {"name": o["name"], "impact": o.get("impact")} if o else None  # noqa: E731
    return {
        "client": summary.get("client"), "product": summary.get("product") or project_name,
        "domain": domain, "industry": summary.get("industry") or industry,
        "title": f"{summary.get('client')}: {imp[0]['feature']}" if imp else f"{summary.get('client')}: improvement plan",
        "pain_points": [p["text"] for p in summary.get("pain_points", []) if not p.get("internal_only")],
        "top_gaps": [g["statement"] for g in summary.get("top_gaps", [])],
        "top_improvements": [{"feature": r["feature"], "phase": r.get("phase"), "business_impact": r.get("business_impact")}
                             for r in imp],
        "opportunities": {k: opp(summary.get(f"{k}_opportunity")) for k in ("ai", "automation", "cost_saving", "revenue")},
        "conversation_angle": summary.get("conversation_angle"), "next_step": summary.get("next_step"),
        "our_capabilities": [x["title"] for x in summary.get("relevant_capabilities", [])],
        "contact_email": (summary.get("contact") or {}).get("email"),
        "analysis_url": f"{s.app_base_url.rstrip('/')}/runs/{run_id}",
        "basis": "Estimates and recommendations from a public-information analysis; review before relying on them.",
    }


def note_text(o: dict) -> str:
    lines = [o["title"], "", "Top gaps:"] + [f"- {g}" for g in o["top_gaps"]]
    lines += ["", "Recommended improvements:"] + [f"- {r['feature']}: {r['business_impact']}" for r in o["top_improvements"]]
    if o.get("conversation_angle"):
        lines += ["", "Conversation angle:", o["conversation_angle"]]
    if o.get("next_step"):
        lines += ["", "Next step:", o["next_step"]]
    lines += ["", f"Analysis: {o['analysis_url']}", o["basis"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- transport


def _client(**kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=get_settings().notify_timeout_seconds, transport=transport,
                             follow_redirects=False, **kw)


async def _call(client: httpx.AsyncClient, method: str, url: str, **kw) -> dict:
    resp = await client.request(method, url, **kw)
    if resp.status_code >= 400:
        # Provider error bodies can echo request data, never credentials; keep them short.
        raise IntegrationError(f"{method} {url.split('?')[0]} failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json() if resp.content else {}


def sign(secret: str, body: bytes, timestamp: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


async def post_webhook(i: Integration, event: str, data: dict) -> dict:
    url = _config(i).get("url")
    if not url:
        raise IntegrationError("Webhook URL not configured")
    try:
        validate_webhook_url(url)
        if transport is None:
            await assert_public_url(url)
    except (UnsafeURL, ValueError) as exc:
        raise IntegrationError(f"Webhook URL refused: {exc}") from exc
    body = json.dumps({"event": event, "sent_at": datetime.now(timezone.utc).isoformat(), "data": data}).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-CIP-Event": event, "X-CIP-Timestamp": ts}
    if i.encrypted_secret:
        headers["X-CIP-Signature"] = sign(_secret(i), body, ts)
    async with _client() as c:
        resp = await c.post(url, content=body, headers=headers)
    if resp.status_code >= 300:
        raise IntegrationError(f"Webhook answered HTTP {resp.status_code}")
    return {"status": resp.status_code}


# --------------------------------------------------------------------------- HubSpot


async def hubspot_opportunity(i: Integration, o: dict) -> dict:
    cfg = _config(i)
    auth = {"Authorization": f"Bearer {_secret(i)}"}
    async with _client(base_url=HUBSPOT_API, headers=auth) as c:
        company_id = None
        if o.get("domain"):
            found = await _call(c, "POST", "/crm/v3/objects/companies/search", json={
                "filterGroups": [{"filters": [{"propertyName": "domain", "operator": "EQ", "value": o["domain"]}]}],
                "limit": 1})
            company_id = (found.get("results") or [{}])[0].get("id")
        if not company_id:
            props = {"name": o["client"], **({"domain": o["domain"]} if o.get("domain") else {})}
            company_id = (await _call(c, "POST", "/crm/v3/objects/companies", json={"properties": props}))["id"]
        deal = await _call(c, "POST", "/crm/v3/objects/deals", json={
            "properties": {"dealname": o["title"][:250], "pipeline": cfg.get("pipeline", "default"),
                           "dealstage": cfg.get("dealstage", "appointmentscheduled"),
                           "description": note_text(o)[:5000]},
            "associations": [{"to": {"id": company_id}, "types": [
                {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": DEAL_TO_COMPANY}]}]})
        note = await _call(c, "POST", "/crm/v3/objects/notes", json={
            "properties": {"hs_timestamp": datetime.now(timezone.utc).isoformat(), "hs_note_body": note_text(o)[:60000]},
            "associations": [{"to": {"id": deal["id"]}, "types": [
                {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_DEAL}]}]})
        contact_id = None
        if o.get("contact_email") and EMAIL.match(o["contact_email"]):
            found = await _call(c, "POST", "/crm/v3/objects/contacts/search", json={
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": o["contact_email"]}]}],
                "limit": 1})
            contact_id = (found.get("results") or [{}])[0].get("id")
            if not contact_id:
                contact_id = (await _call(c, "POST", "/crm/v3/objects/contacts", json={
                    "properties": {"email": o["contact_email"]},
                    "associations": [{"to": {"id": company_id}, "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": CONTACT_TO_COMPANY}]}]}))["id"]
    return {"company_id": company_id, "deal_id": deal["id"], "note_id": note.get("id"), "contact_id": contact_id}


# --------------------------------------------------------------------------- Mailchimp


async def mailchimp_contact(i: Integration, email: str, company: str, tags: list[str]) -> dict:
    cfg = _config(i)
    key = _secret(i) or ""
    dc = key.rsplit("-", 1)[-1]
    if not MAILCHIMP_DC.match(dc):
        raise IntegrationError("Invalid Mailchimp API key")
    member = hashlib.md5(email.lower().encode()).hexdigest()  # noqa: S324 - Mailchimp's member id scheme
    base = f"https://{dc}.api.mailchimp.com/3.0/lists/{cfg['list_id']}/members/{member}"
    async with _client(auth=("cip", key)) as c:
        out = await _call(c, "PUT", base, json={"email_address": email, "status_if_new": cfg.get("status_if_new", "pending"),
                                                "merge_fields": {"COMPANY": company[:100]}})
        if tags:
            await _call(c, "POST", f"{base}/tags", json={"tags": [{"name": t[:100], "status": "active"} for t in tags]})
    return {"member_id": out.get("id", member), "status": out.get("status")}


# --------------------------------------------------------------------------- actions


async def push_opportunity(i: Integration, o: dict) -> dict:
    if i.provider == "hubspot":
        return await hubspot_opportunity(i, o)
    return await post_webhook(i, "opportunity.created", o)


async def push_contact(i: Integration, email: str, o: dict) -> dict:
    if not EMAIL.match(email or ""):
        raise IntegrationError("No valid business contact email for this client")
    tags = [t for t in (o.get("industry"), "cip-analysed",
                        o["top_improvements"][0]["feature"] if o.get("top_improvements") else None) if t]
    if i.provider == "mailchimp":
        return await mailchimp_contact(i, email, o.get("client") or "", tags)
    return await post_webhook(i, "contact.upserted", {"email": email, "company": o.get("client"),
                                                      "domain": o.get("domain"), "industry": o.get("industry"),
                                                      "tags": tags, "analysis_url": o["analysis_url"]})


async def test(i: Integration) -> dict:
    if i.provider == "hubspot":
        async with _client(base_url=HUBSPOT_API, headers={"Authorization": f"Bearer {_secret(i)}"}) as c:
            await _call(c, "GET", "/crm/v3/objects/companies?limit=1")
        return {"ok": True}
    if i.provider == "mailchimp":
        key = _secret(i) or ""
        async with _client(auth=("cip", key)) as c:
            await _call(c, "GET", f"https://{key.rsplit('-', 1)[-1]}.api.mailchimp.com/3.0/lists/{_config(i)['list_id']}")
        return {"ok": True}
    return await post_webhook(i, "test", {"message": "Test event from the Client Intelligence Platform"})
