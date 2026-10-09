"""Alert delivery: incoming webhooks (Slack-compatible JSON) and email (SMTP).

Webhook URLs are credentials: they are stored encrypted, never logged and never
echoed back in delivery results. Deliveries go only to public HTTPS endpoints
(same SSRF guard as the research crawler, redirects not followed). Alert text
contains names and titles scraped from third-party sites, so it is escaped for
Slack's markup (no ``<!channel>`` pings or disguised links).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from cip.core.urls import urlparse

import httpx

from cip.config import Settings, get_settings
from cip.connectors.research.web import UnsafeURL, assert_public_url

log = logging.getLogger(__name__)

# Tests inject an httpx.MockTransport here (which also bypasses DNS-based SSRF checks).
webhook_transport: httpx.AsyncBaseTransport | None = None

SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
MAX_LISTED_CHANGES = 10


def slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def validate_webhook_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Webhook URL must be an https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not embed credentials")
    return url


def mask_webhook_url(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}/…"


def run_link(settings: Settings, run_id: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/runs/{run_id}"


def alert_text(alert: dict, project_name: str, link: str, *, markup: bool) -> str:
    esc = slack_escape if markup else (lambda s: s)
    lines = [f"{SEVERITY_ICON.get(alert['severity'], '')} {esc(alert['title'])} — {esc(project_name)}".strip()]
    if alert.get("summary"):
        lines.append(esc(alert["summary"]))
    changes = alert.get("changes") or []
    for c in changes[:MAX_LISTED_CHANGES]:
        lines.append(f"• [{c['severity']}] {esc(c['title'])}")
    if len(changes) > MAX_LISTED_CHANGES:
        lines.append(f"… and {len(changes) - MAX_LISTED_CHANGES} more")
    lines.append(f"<{link}|Open the analysis>" if markup else f"Open the analysis: {link}")
    return "\n".join(lines)


async def send_webhook(url: str, payload: dict, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    try:
        validate_webhook_url(url)
        if webhook_transport is None:
            await assert_public_url(url)
        async with httpx.AsyncClient(timeout=s.notify_timeout_seconds, follow_redirects=False,
                                     transport=webhook_transport) as client:
            resp = await client.post(url, json=payload)
        ok = resp.status_code < 300
        return {"channel": "webhook", "ok": ok, "status": resp.status_code}
    except (httpx.HTTPError, UnsafeURL, ValueError) as exc:
        log.info("Webhook delivery failed: %s", type(exc).__name__)
        return {"channel": "webhook", "ok": False, "error": type(exc).__name__}


def _smtp_send(settings: Settings, msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.notify_timeout_seconds) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(msg)


async def send_email(recipients: list[str], subject: str, body: str, settings: Settings | None = None,
                     reply_to: str | None = None) -> dict:
    s = settings or get_settings()
    if not recipients:
        return {"channel": "email", "ok": True, "recipients": 0}
    if not s.smtp_host:
        return {"channel": "email", "ok": False, "error": "SMTP not configured (CIP_SMTP_HOST)"}
    msg = EmailMessage()
    msg["Subject"] = subject.replace("\n", " ")[:200]
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(recipients)
    if reply_to:
        msg["Reply-To"] = reply_to.replace("\n", " ")
    msg.set_content(body)
    try:
        await asyncio.to_thread(_smtp_send, s, msg)
        return {"channel": "email", "ok": True, "recipients": len(recipients)}
    except (OSError, smtplib.SMTPException) as exc:
        log.info("Email delivery failed: %s", exc)
        return {"channel": "email", "ok": False, "error": type(exc).__name__}


async def deliver(alert: dict, project_name: str, webhook_url: str | None, emails: list[str],
                  settings: Settings | None = None) -> list[dict]:
    """Send ``alert`` to every configured channel. Never raises; returns per-channel results."""
    s = settings or get_settings()
    link = run_link(s, alert["run_id"])
    results = []
    if webhook_url:
        payload = {"text": alert_text(alert, project_name, link, markup=True),
                   "alert": {k: alert.get(k) for k in ("id", "kind", "severity", "title", "summary", "run_id",
                                                       "project_id")} | {"project": project_name, "url": link,
                                                                         "changes": alert.get("changes", [])}}
        results.append(await send_webhook(webhook_url, payload, s))
    if emails:
        results.append(await send_email(emails, f"[{alert['severity']}] {alert['title']} — {project_name}",
                                        alert_text(alert, project_name, link, markup=False), s))
    return results
