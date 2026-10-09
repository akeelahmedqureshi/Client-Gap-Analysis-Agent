"""CRM and email / marketing integrations: settings (admin), pushes from a run (analyst), outreach sending."""

from __future__ import annotations

from datetime import datetime
from email.utils import formataddr

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.config import get_settings
from cip.db.models import AnalysisRun, Client, Integration, IntegrationSync, Project, SalesDocument, User
from cip.db.session import get_session
from cip.services import audit, governance, integrations, notify
from cip.services.access import run_for

router = APIRouter(prefix="/api", tags=["integrations"])


class IntegrationIn(BaseModel):
    provider: str
    secret: str | None = Field(None, max_length=500)  # API token, or the webhook signing secret
    config: dict = Field(default_factory=dict)
    enabled: bool = True
    auto_create: bool = False


class SendIn(BaseModel):
    confirm: bool = False
    resend: bool = False


class SyncOut(BaseModel):
    id: str
    action: str
    provider: str
    status: str
    external_ids: dict
    error: str | None
    document_version: int | None
    automatic: bool
    created_by_email: str | None
    created_at: datetime


async def _integration(session: AsyncSession, org_id: str, kind: str) -> Integration | None:
    return (await session.execute(select(Integration).where(Integration.org_id == org_id,
                                                            Integration.kind == kind))).scalar_one_or_none()


def _kind(kind: str) -> str:
    if kind not in integrations.PROVIDERS:
        raise not_found("Integration")
    return kind


# --------------------------------------------------------------------------- settings


@router.get("/integrations")
async def list_integrations(user: User = Depends(require_role("viewer")),
                            session: AsyncSession = Depends(get_session)) -> dict:
    out = {k: integrations.public_view(await _integration(session, user.org_id, k)) for k in integrations.PROVIDERS}
    out["email_sending"] = bool(get_settings().smtp_host)
    return out


@router.put("/integrations/{kind}")
async def put_integration(kind: str, body: IntegrationIn, request: Request, admin: User = Depends(require_role("admin")),
                          session: AsyncSession = Depends(get_session)) -> dict:
    kind = _kind(kind)
    existing = await _integration(session, admin.org_id, kind)
    try:
        config = integrations.validate(kind, body.provider, body.secret, body.config, existing)
    except (integrations.IntegrationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if existing is not None and existing.provider != body.provider:
        await session.delete(existing)
        await session.flush()
        existing = None
    i = existing or Integration(org_id=admin.org_id, kind=kind, provider=body.provider, created_by=admin.id)
    integrations.store(i, body.secret, config)
    i.enabled, i.auto_create = body.enabled, body.auto_create and kind == "crm"
    session.add(i)
    audit.record(session, request, admin, "integration.saved", "integration", kind, provider=body.provider,
                 secret_changed=bool(body.secret) or None, auto_create=i.auto_create or None)
    await session.commit()
    return integrations.public_view(i)


@router.delete("/integrations/{kind}", status_code=204)
async def delete_integration(kind: str, request: Request, admin: User = Depends(require_role("admin")),
                             session: AsyncSession = Depends(get_session)) -> None:
    i = await _integration(session, admin.org_id, _kind(kind))
    if i is None:
        raise not_found("Integration")
    await session.delete(i)
    audit.record(session, request, admin, "integration.removed", "integration", kind, provider=i.provider)
    await session.commit()


@router.post("/integrations/{kind}/test")
async def test_integration(kind: str, request: Request, admin: User = Depends(require_role("admin")),
                           session: AsyncSession = Depends(get_session)) -> dict:
    i = await _integration(session, admin.org_id, _kind(kind))
    if i is None:
        raise not_found("Integration")
    try:
        result = await integrations.test(i)
    except (integrations.IntegrationError, Exception) as exc:  # noqa: BLE001 - report, never 500
        audit.record(session, request, admin, "integration.tested", "integration", kind, ok=False)
        await session.commit()
        return {"ok": False, "error": str(exc)[:300]}
    audit.record(session, request, admin, "integration.tested", "integration", kind, ok=True)
    await session.commit()
    return {"ok": True, **result}


# --------------------------------------------------------------------------- pushes from a run


async def _approved_summary(session: AsyncSession, run: AnalysisRun) -> SalesDocument:
    doc = (await session.execute(select(SalesDocument).where(
        SalesDocument.run_id == run.id, SalesDocument.kind == "sales_summary"))).scalar_one_or_none()
    if doc is None or doc.status != "approved":
        raise HTTPException(409, "Approve the sales summary first; only approved content is sent to other systems")
    return doc


async def _opportunity(session: AsyncSession, run: AnalysisRun, doc: SalesDocument) -> dict:
    project = await session.get(Project, run.project_id)
    client = await session.get(Client, project.client_id)
    return integrations.opportunity(doc.content, project.name, run.id, client.industry, client.domain)


async def _record(session: AsyncSession, run: AnalysisRun, user: User | None, action: str, provider: str,
                  version: int | None, result: dict | None, error: str | None, automatic: bool = False) -> IntegrationSync:
    sync = IntegrationSync(org_id=run.org_id, run_id=run.id, project_id=run.project_id, action=action, provider=provider,
                           status="failed" if error else "ok", external_ids=result or {}, error=(error or None) and error[:500],
                           document_version=version, automatic=automatic, created_by=user.id if user else None,
                           created_by_email=user.email if user else None)
    session.add(sync)
    return sync


async def create_opportunity(session: AsyncSession, run: AnalysisRun, user: User | None, request: Request | None,
                             automatic: bool = False) -> IntegrationSync:
    i = await _integration(session, run.org_id, "crm")
    if i is None or not i.enabled:
        raise HTTPException(409, "No CRM integration is configured")
    doc = await _approved_summary(session, run)
    payload = await _opportunity(session, run, doc)
    try:
        result, error = await integrations.push_opportunity(i, payload), None
    except Exception as exc:  # noqa: BLE001 - provider and network errors are recorded
        result, error = None, str(exc)
    sync = await _record(session, run, user, "crm.opportunity", i.provider, doc.version, result, error, automatic)
    audit.record(session, request, user, "crm.opportunity", "run", run.id, org_id=run.org_id,
                 email=None if user else "automation", provider=i.provider, ok=not error, automatic=automatic or None)
    return sync


@router.post("/runs/{run_id}/crm", response_model=SyncOut)
async def push_crm(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                   session: AsyncSession = Depends(get_session)) -> SyncOut:
    """Create the opportunity (company, deal and note) in the CRM from the approved sales summary."""
    run = await run_for(session, user, run_id)
    await governance.require_export(session, user)
    sync = await create_opportunity(session, run, user, request)
    await session.commit()
    return SyncOut.model_validate(sync, from_attributes=True)


@router.post("/runs/{run_id}/marketing", response_model=SyncOut)
async def push_marketing(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                         session: AsyncSession = Depends(get_session)) -> SyncOut:
    """Add the client's business contact to the marketing system, tagged with industry and top opportunity."""
    run = await run_for(session, user, run_id)
    await governance.require_export(session, user)
    i = await _integration(session, run.org_id, "marketing")
    if i is None or not i.enabled:
        raise HTTPException(409, "No marketing integration is configured")
    doc = await _approved_summary(session, run)
    payload = await _opportunity(session, run, doc)
    email = payload.get("contact_email")
    if not email:
        raise HTTPException(409, "No business contact email was found for this client")
    try:
        result, error = await integrations.push_contact(i, email, payload), None
    except Exception as exc:  # noqa: BLE001
        result, error = None, str(exc)
    sync = await _record(session, run, user, "marketing.contact", i.provider, doc.version, result, error)
    audit.record(session, request, user, "marketing.contact", "run", run.id, provider=i.provider, ok=not error)
    await session.commit()
    return SyncOut.model_validate(sync, from_attributes=True)


@router.post("/runs/{run_id}/outreach/send", response_model=SyncOut)
async def send_outreach(run_id: str, body: SendIn, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> SyncOut:
    """Send the approved outreach email through the organization's SMTP server (explicit confirmation, once per
    approved version unless ``resend``). Replies go to the sender."""
    run = await run_for(session, user, run_id)
    await governance.require_export(session, user)
    if not body.confirm:
        raise HTTPException(422, "Confirm sending: this emails a person outside your organization")
    s = get_settings()
    if not s.smtp_host:
        raise HTTPException(409, "Email sending is not configured (CIP_SMTP_HOST)")
    doc = (await session.execute(select(SalesDocument).where(
        SalesDocument.run_id == run.id, SalesDocument.kind == "outreach"))).scalar_one_or_none()
    if doc is None or doc.status != "approved":
        raise HTTPException(409, "Approve the outreach email first")
    to = (doc.content.get("to") or "").strip()
    if not integrations.EMAIL.match(to):
        raise HTTPException(409, "Set a valid recipient address before sending")
    sent = (await session.execute(select(IntegrationSync).where(
        IntegrationSync.run_id == run.id, IntegrationSync.action == "email.sent", IntegrationSync.status == "ok",
        IntegrationSync.document_version == doc.version))).first()
    if sent and not body.resend:
        raise HTTPException(409, "This version was already sent; confirm a resend to send it again")
    result = await notify.send_email([to], doc.content.get("subject", ""), doc.content.get("body", ""), s,
                                     reply_to=formataddr((user.name or "", user.email)))
    error = None if result.get("ok") else result.get("error", "send failed")
    sync = await _record(session, run, user, "email.sent", "smtp", doc.version, {"recipients": 1} if not error else None,
                         error)
    audit.record(session, request, user, "outreach.sent", "run", run.id, version=doc.version, ok=not error)
    await session.commit()
    return SyncOut.model_validate(sync, from_attributes=True)


@router.get("/runs/{run_id}/integrations", response_model=list[SyncOut])
async def run_syncs(run_id: str, user: User = Depends(require_role("viewer")),
                    session: AsyncSession = Depends(get_session)) -> list[SyncOut]:
    run = await run_for(session, user, run_id)
    rows = (await session.execute(select(IntegrationSync).where(IntegrationSync.run_id == run.id)
                                  .order_by(IntegrationSync.created_at.desc()))).scalars().all()
    return [SyncOut.model_validate(r, from_attributes=True) for r in rows]
