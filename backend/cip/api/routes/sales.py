"""Sales summary and outreach email of a run: review, edit, regenerate, approve, export (PRD 10.20-10.21).

The agents (``agents/sales.py``) produce the first versions; this API keeps the human-reviewed copy in
``sales_documents``. Every save re-runs the claim check. Approval is refused while a draft mentions
internal-only knowledge or security findings; other warnings must be acknowledged explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import require_role
from cip.config import get_settings
from cip.core.llm import get_llm
from cip.core.outreach import OutreachInput, check_claims, generate_email
from cip.db.models import AgentExecution, AnalysisRun, SalesDocument, User
from cip.db.session import get_session
from cip.services import audit, governance
from cip.services.access import run_for

router = APIRouter(prefix="/api/runs", tags=["sales & outreach"])

BLOCKING = ("internal-only", "security")


class SummaryPatch(BaseModel):
    conversation_angle: str | None = Field(None, max_length=4000)
    next_step: str | None = Field(None, max_length=2000)
    reviewer_notes: str | None = Field(None, max_length=4000)
    note: str = Field("", max_length=500)


class OutreachPatch(BaseModel):
    to: str | None = Field(None, max_length=320)
    subject: str | None = Field(None, min_length=1, max_length=200)
    body: str | None = Field(None, min_length=1, max_length=8000)
    note: str = Field("", max_length=500)


class RegenerateIn(BaseModel):
    instructions: str = Field("", max_length=500)


class ApproveIn(BaseModel):
    acknowledge_warnings: bool = False
    note: str = Field("", max_length=500)


async def _agent_data(session: AsyncSession, run_id: str, agent: str) -> dict | None:
    ex = (await session.execute(select(AgentExecution).where(
        AgentExecution.run_id == run_id, AgentExecution.agent == agent))).scalar_one_or_none()
    if ex is None or ex.status != "completed" or not ex.result:
        return None
    return ex.result.get("data") or None


def _entry(user: User, action: str, version: int, note: str = "", **extra) -> dict:
    return {"version": version, "action": action, "by": user.email, "at": datetime.now(timezone.utc).isoformat(),
            "note": note, **extra}


async def _docs(session: AsyncSession, run: AnalysisRun, user: User) -> dict[str, SalesDocument]:
    """The run's sales documents, created from the agent results on first access."""
    rows = {d.kind: d for d in (await session.execute(select(SalesDocument).where(
        SalesDocument.run_id == run.id, SalesDocument.org_id == run.org_id))).scalars()}
    created = False
    for kind, agent in (("sales_summary", "sales_intelligence"), ("outreach", "outreach")):
        if kind in rows:
            continue
        data = await _agent_data(session, run.id, agent)
        if data is None:
            continue
        if kind == "sales_summary":
            content = {k: v for k, v in data.items() if k != "outreach_input"}
            content["reviewer_notes"] = ""
        else:
            content = {k: data.get(k) for k in ("to", "subject", "body", "generated_by", "problems", "notes", "facts")}
        doc = SalesDocument(org_id=run.org_id, run_id=run.id, project_id=run.project_id, kind=kind, content=content,
                            generated=content, status="draft", version=1,
                            history=[{"version": 1, "action": "generated", "by": None,
                                      "at": datetime.now(timezone.utc).isoformat(), "note": ""}])
        session.add(doc)
        rows[kind] = doc
        created = True
    if created:
        await session.commit()
    return rows


async def _outreach_input(session: AsyncSession, run: AnalysisRun) -> OutreachInput:
    data = await _agent_data(session, run.id, "sales_intelligence")
    if not data or not data.get("outreach_input"):
        raise HTTPException(409, "The sales summary for this run is not available")
    return OutreachInput.from_dict(data["outreach_input"])


def _doc_out(d: SalesDocument | None) -> dict | None:
    if d is None:
        return None
    return {"content": d.content, "status": d.status, "version": d.version, "history": d.history[::-1],
            "approved_at": d.approved_at, "updated_at": d.updated_at, "edited": d.content != d.generated}


@router.get("/{run_id}/sales")
async def get_sales(run_id: str, user: User = Depends(require_role("viewer")),
                    session: AsyncSession = Depends(get_session)) -> dict:
    run = await run_for(session, user, run_id)
    docs = await _docs(session, run, user)
    return {"run_id": run.id, "summary": _doc_out(docs.get("sales_summary")), "outreach": _doc_out(docs.get("outreach"))}


async def _doc(session: AsyncSession, user: User, run_id: str, kind: str) -> tuple[AnalysisRun, SalesDocument]:
    run = await run_for(session, user, run_id)
    doc = (await _docs(session, run, user)).get(kind)
    if doc is None:
        raise HTTPException(409, "Not generated yet for this run")
    return run, doc


@router.patch("/{run_id}/sales/summary")
async def edit_summary(run_id: str, body: SummaryPatch, request: Request, user: User = Depends(require_role("analyst")),
                       session: AsyncSession = Depends(get_session)) -> dict:
    run, doc = await _doc(session, user, run_id, "sales_summary")
    changes = body.model_dump(exclude_unset=True, exclude={"note"}, exclude_none=True)
    content = dict(doc.content)
    changed = [k for k, v in changes.items() if content.get(k) != v]
    if not changed:
        return _doc_out(doc)
    content.update(changes)
    doc.content, doc.status, doc.version = content, "draft", doc.version + 1
    doc.updated_by = user.id
    doc.history = [*doc.history, _entry(user, "edited", doc.version, body.note, fields=changed)]
    audit.record(session, request, user, "sales_summary.edited", "run", run.id, fields=changed)
    await session.commit()
    return _doc_out(doc)


@router.post("/{run_id}/sales/summary/approve")
async def approve_summary(run_id: str, body: ApproveIn, request: Request, user: User = Depends(require_role("analyst")),
                          session: AsyncSession = Depends(get_session)) -> dict:
    run, doc = await _doc(session, user, run_id, "sales_summary")
    if doc.status != "approved":
        doc.status, doc.approved_by, doc.approved_at = "approved", user.id, datetime.now(timezone.utc)
        doc.history = [*doc.history, _entry(user, "approved", doc.version, body.note)]
        audit.record(session, request, user, "sales_summary.approved", "run", run.id, version=doc.version)
        await session.commit()
    return _doc_out(doc)


@router.patch("/{run_id}/outreach")
async def edit_outreach(run_id: str, body: OutreachPatch, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    run, doc = await _doc(session, user, run_id, "outreach")
    changes = body.model_dump(exclude_unset=True, exclude={"note"}, exclude_none=True)
    content = dict(doc.content)
    changed = [k for k, v in changes.items() if content.get(k) != v]
    if not changed:
        return _doc_out(doc)
    content.update(changes)
    inp = await _outreach_input(session, run)
    content["problems"] = check_claims(content["subject"], content["body"], inp)
    content["generated_by"] = "edited"
    doc.content, doc.status, doc.version = content, "draft", doc.version + 1
    doc.updated_by = user.id
    doc.history = [*doc.history, _entry(user, "edited", doc.version, body.note, fields=changed)]
    audit.record(session, request, user, "outreach.edited", "run", run.id, fields=changed)
    await session.commit()
    return _doc_out(doc)


@router.post("/{run_id}/outreach/regenerate")
async def regenerate_outreach(run_id: str, body: RegenerateIn, request: Request,
                              user: User = Depends(require_role("analyst")),
                              session: AsyncSession = Depends(get_session)) -> dict:
    run, doc = await _doc(session, user, run_id, "outreach")
    inp = await _outreach_input(session, run)
    email = await generate_email(get_llm(get_settings()), inp, body.instructions)
    content = {**doc.content, "subject": email.subject, "body": email.body, "generated_by": email.generated_by,
               "problems": email.problems, "notes": email.notes}
    doc.content, doc.status, doc.version = content, "draft", doc.version + 1
    doc.updated_by = user.id
    doc.history = [*doc.history, _entry(user, "regenerated", doc.version, body.instructions,
                                        generated_by=email.generated_by)]
    audit.record(session, request, user, "outreach.regenerated", "run", run.id, generated_by=email.generated_by)
    await session.commit()
    return _doc_out(doc)


@router.post("/{run_id}/outreach/approve")
async def approve_outreach(run_id: str, body: ApproveIn, request: Request, user: User = Depends(require_role("analyst")),
                           session: AsyncSession = Depends(get_session)) -> dict:
    run, doc = await _doc(session, user, run_id, "outreach")
    if doc.status == "approved":
        return _doc_out(doc)
    inp = await _outreach_input(session, run)
    problems = check_claims(doc.content.get("subject", ""), doc.content.get("body", ""), inp)
    blocking = [p for p in problems if any(b in p.lower() for b in BLOCKING)]
    if blocking:
        raise HTTPException(409, "Cannot approve: " + "; ".join(blocking))
    if problems and not body.acknowledge_warnings:
        raise HTTPException(409, "Review the warnings first: " + "; ".join(problems))
    doc.content = {**doc.content, "problems": problems}
    doc.status, doc.approved_by, doc.approved_at = "approved", user.id, datetime.now(timezone.utc)
    doc.history = [*doc.history, _entry(user, "approved", doc.version, body.note,
                                        acknowledged_warnings=problems or None)]
    audit.record(session, request, user, "outreach.approved", "run", run.id, version=doc.version,
                 warnings=len(problems) or None)
    await session.commit()
    return _doc_out(doc)


@router.get("/{run_id}/outreach.eml")
async def export_outreach(run_id: str, request: Request, user: User = Depends(require_role("viewer")),
                          session: AsyncSession = Depends(get_session)) -> Response:
    run, doc = await _doc(session, user, run_id, "outreach")
    await governance.require_export(session, user)
    msg = EmailMessage()
    if doc.content.get("to"):
        msg["To"] = doc.content["to"]
    msg["Subject"] = doc.content.get("subject", "")
    msg["Date"] = formatdate(localtime=False)
    msg["X-Unsent"] = "1"  # opens as a draft in Outlook / Apple Mail
    msg.set_content(doc.content.get("body", ""))
    audit.record(session, request, user, "outreach.exported", "run", run.id, status=doc.status)
    await session.commit()
    suffix = "" if doc.status == "approved" else "-DRAFT"
    return Response(msg.as_bytes(), media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="outreach-{run_id}{suffix}.eml"'})


def summary_markdown(c: dict, status: str) -> str:
    def opp(label: str, o: dict | None) -> list[str]:
        if not o:
            return [f"- **{label}:** none identified"]
        return [f"- **{label}:** {o['name']}" + (f" — {o['impact']}" if o.get("impact") else "") + " _(estimate)_"]

    out = [f"# Sales intelligence: {c.get('client')} — {c.get('product')}",
           f"_Status: {status}. Industry: {c.get('industry') or 'unknown'}._", "", "## Key pain points"]
    out += [f"- {p['text']}" + (" _(internal only)_" if p.get("internal_only") else "") for p in c.get("pain_points", [])] \
        or ["- None evidenced"]
    out += ["", "## Top competitive gaps"] + [f"{i}. {g['statement']}" for i, g in enumerate(c.get("top_gaps", []), 1)]
    out += ["", "## Top recommended improvements"]
    out += [f"{i}. **{r['feature']}** ({r['phase']}): {r['business_impact']}"
            for i, r in enumerate(c.get("top_improvements", []), 1)]
    out += ["", "## Opportunities"]
    out += opp("AI", c.get("ai_opportunity")) + opp("Automation", c.get("automation_opportunity"))
    out += opp("Cost saving", c.get("cost_saving_opportunity")) + opp("Revenue", c.get("revenue_opportunity"))
    out += ["", "## Conversation angle", c.get("conversation_angle", ""), "", "## Our relevant capabilities"]
    out += [f"- {x['title']} — for {x['need']}" for x in c.get("relevant_capabilities", [])] or ["- None approved for client use"]
    if c.get("internal_capabilities"):
        out += ["", "_Internal only (do not share):_"] + [f"- {x['title']}" for x in c["internal_capabilities"]]
    if c.get("case_studies"):
        out += ["", "## Case studies"] + [f"- {x['title']} ({x['customer']})" for x in c["case_studies"]]
    out += ["", "## Suggested next step", c.get("next_step", "")]
    if c.get("contact"):
        out += ["", f"Contact: {c['contact']['email']}"]
    if c.get("reviewer_notes"):
        out += ["", "## Reviewer notes", c["reviewer_notes"]]
    return "\n".join(out) + "\n"


@router.get("/{run_id}/sales-summary.md", response_class=PlainTextResponse)
async def export_summary(run_id: str, request: Request, user: User = Depends(require_role("viewer")),
                         session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    run, doc = await _doc(session, user, run_id, "sales_summary")
    await governance.require_export(session, user)
    audit.record(session, request, user, "sales_summary.exported", "run", run.id, status=doc.status)
    await session.commit()
    return PlainTextResponse(summary_markdown(doc.content, doc.status), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="sales-summary-{run_id}.md"'})
