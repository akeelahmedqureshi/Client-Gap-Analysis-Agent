"""CSV upload → validate → review (corrections, duplicates, existing clients) → import. Rules: services/intake.py."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.csv_intake import parse_csv
from cip.connectors.research.domain import check_many, is_problem
from cip.connectors.research.web import WebFetcher
from cip.api.deps import not_found, require_role
from cip.core.schemas import NormalizedRecord
from cip.db.models import CsvUpload, User
from cip.db.session import get_session
from cip.services import audit, intake
from cip.services.storage import LocalObjectStore

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOMAIN_CHECKS = 200


def domain_fetcher() -> WebFetcher:
    """Tests replace this with a fetcher on a mocked transport."""
    return WebFetcher(rendering="never")


class UploadOut(BaseModel):
    id: str
    filename: str
    valid: bool
    column_mapping: dict[str, str]
    unmapped_columns: list[str]
    errors: list[str]
    warnings: list[str]
    records: list[NormalizedRecord]
    domain_checks: dict[int, dict] = {}
    headers: list[str] = []
    plan: dict = {}
    results: dict = {}


@router.post("", response_model=UploadOut, status_code=201)
async def upload_csv(request: Request, file: UploadFile = File(...), user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> UploadOut:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "CSV exceeds 10 MB")
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload.csv")[:200]
    parsed = parse_csv(data)
    upload = CsvUpload(org_id=user.org_id, filename=filename, storage_key="", column_mapping=parsed.column_mapping,
                       row_count=len(parsed.records), errors=parsed.errors, warnings=parsed.warnings,
                       uploaded_by=user.id)
    session.add(upload)
    await session.flush()
    upload.storage_key = LocalObjectStore().put(f"{user.org_id}/csv/{upload.id}.csv", data)
    audit.record(session, request, user, "upload.created", "upload", upload.id, filename=filename,
                 rows=len(parsed.records))
    await session.commit()
    return _out(upload, parsed)


@router.get("/{upload_id}", response_model=UploadOut)
async def get_upload(upload_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> UploadOut:
    upload = await _upload(session, user, upload_id)
    return _out(upload, _parsed(upload))


def _checks(upload: CsvUpload) -> dict[int, dict]:
    return {int(k): v for k, v in (upload.domain_checks or {}).items()}


def _out(upload: CsvUpload, parsed) -> UploadOut:
    checks = _checks(upload)
    for rec in parsed.records:
        rec.domain_check = checks.get(rec.row_number)
    return UploadOut(id=upload.id, filename=upload.filename, valid=parsed.valid,
                     column_mapping=parsed.column_mapping, unmapped_columns=parsed.unmapped_columns,
                     errors=parsed.errors, warnings=parsed.warnings, records=parsed.records, domain_checks=checks,
                     headers=parsed.headers, plan=upload.plan or {}, results=upload.results or {})


def _parsed(upload: CsvUpload, plan: intake.Plan | None = None):
    mapping = plan.mapping if plan and plan.mapping is not None else (upload.plan or {}).get("mapping")
    return parse_csv(LocalObjectStore().get(upload.storage_key), mapping)


async def _upload(session: AsyncSession, user: User, upload_id: str) -> CsvUpload:
    upload = await session.get(CsvUpload, upload_id)
    if not upload or upload.org_id != user.org_id:
        raise not_found("Upload")
    return upload


def _rows(upload: CsvUpload, parsed, plan: intake.Plan) -> list[intake.Incoming]:
    return intake.incoming([(r.row_number, r.raw) for r in parsed.rows], plan, _checks(upload))


def _done(upload: CsvUpload) -> dict[int, dict]:
    return {int(k): v for k, v in (upload.results or {}).items() if v.get("ok")}


@router.post("/{upload_id}/review")
async def review_upload(upload_id: str, plan: intake.Plan, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Validate every row with the person's corrections and decisions, find duplicates within the file and
    matches with existing clients, and report what an import would do. The plan is saved with the upload so the
    review can be resumed; nothing is imported."""
    upload = await _upload(session, user, upload_id)
    parsed = _parsed(upload, plan)
    if not parsed.valid:
        raise HTTPException(422, {"errors": parsed.errors})
    result = await intake.review(session, user, _rows(upload, parsed, plan), plan, _done(upload))
    upload.plan = plan.model_dump(mode="json")
    await session.commit()
    return {**result, "column_mapping": parsed.column_mapping, "unmapped_columns": parsed.unmapped_columns,
            "headers": parsed.headers, "warnings": parsed.warnings}


def _record_url(rec: NormalizedRecord) -> str | None:
    return rec.client.domain or rec.project.url


@router.post("/{upload_id}/check-domains", response_model=UploadOut)
async def check_domains(upload_id: str, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> UploadOut:
    """Request each row's homepage once and classify it: ok, redirected, parked, unreachable or blocked.
    Problem rows are flagged in the preview (not blocked); the person decides whether to import them."""
    upload = await _upload(session, user, upload_id)
    parsed = _parsed(upload)
    urls = {r.row_number: u for r in parsed.records[:MAX_DOMAIN_CHECKS] if (u := _record_url(r))}
    results = await check_many(domain_fetcher(), urls)
    upload.domain_checks = {str(k): v for k, v in results.items()}
    problems = sum(1 for v in results.values() if is_problem(v))
    audit.record(session, request, user, "upload.domains_checked", "upload", upload.id, rows=len(results),
                 problems=problems)
    await session.commit()
    return _out(upload, parsed)


@router.post("/{upload_id}/import")
async def import_upload(upload_id: str, plan: intake.Plan, request: Request,
                        user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Apply the reviewed decisions. Refused (409, with the review) while records still need a decision, unless
    ``allow_partial`` confirms that only the ready records should be imported."""
    upload = await _upload(session, user, upload_id)
    parsed = _parsed(upload, plan)
    if not parsed.valid:
        raise HTTPException(422, {"errors": parsed.errors})
    try:
        out = await intake.apply(session, request, user, _rows(upload, parsed, plan), plan, upload)
    except intake.IntakeError as exc:
        await session.rollback()
        raise HTTPException(exc.status, {"message": exc.message, "review": exc.review}) from exc
    await session.commit()
    return out
