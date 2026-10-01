"""CSV upload → validate → preview → import (creates clients & projects)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.csv_intake import parse_csv
from cip.api.deps import not_found, require_role
from cip.core.schemas import NormalizedRecord
from cip.db.models import Client, CsvUpload, Project, User
from cip.db.session import get_session
from cip.services import audit
from cip.services.storage import LocalObjectStore

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UploadOut(BaseModel):
    id: str
    filename: str
    valid: bool
    column_mapping: dict[str, str]
    unmapped_columns: list[str]
    errors: list[str]
    warnings: list[str]
    records: list[NormalizedRecord]


class ImportIn(BaseModel):
    rows: list[int] | None = None  # row numbers to import; default: all non-duplicate rows
    include_duplicates: bool = False


class ImportOut(BaseModel):
    created_projects: list[str]
    reused_clients: int
    created_clients: int
    skipped_rows: list[int]


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
    return UploadOut(id=upload.id, filename=filename, valid=parsed.valid, column_mapping=parsed.column_mapping,
                     unmapped_columns=parsed.unmapped_columns, errors=parsed.errors, warnings=parsed.warnings,
                     records=parsed.records)


@router.get("/{upload_id}", response_model=UploadOut)
async def get_upload(upload_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> UploadOut:
    upload = await session.get(CsvUpload, upload_id)
    if not upload or upload.org_id != user.org_id:
        raise not_found("Upload")
    parsed = parse_csv(LocalObjectStore().get(upload.storage_key))
    return UploadOut(id=upload.id, filename=upload.filename, valid=parsed.valid,
                     column_mapping=parsed.column_mapping, unmapped_columns=parsed.unmapped_columns,
                     errors=parsed.errors, warnings=parsed.warnings, records=parsed.records)


def _client_key(rec: NormalizedRecord) -> str:
    return re.sub(r"[^a-z0-9.]", "", (rec.client.domain or rec.client.name).lower())


@router.post("/{upload_id}/import", response_model=ImportOut)
async def import_upload(upload_id: str, body: ImportIn, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> ImportOut:
    upload = await session.get(CsvUpload, upload_id)
    if not upload or upload.org_id != user.org_id:
        raise not_found("Upload")
    parsed = parse_csv(LocalObjectStore().get(upload.storage_key))
    if not parsed.valid:
        raise HTTPException(422, {"errors": parsed.errors})
    created: list[str] = []
    skipped: list[int] = []
    new_clients = reused = 0
    for rec in parsed.records:
        if body.rows is not None and rec.row_number not in body.rows:
            continue
        if rec.duplicate_of_row and not body.include_duplicates:
            skipped.append(rec.row_number)
            continue
        key = _client_key(rec)
        client = (await session.execute(select(Client).where(Client.org_id == user.org_id,
                                                             Client.key == key))).scalar_one_or_none()
        if client is None:
            client = Client(org_id=user.org_id, key=key, name=rec.client.name, domain=rec.client.domain,
                            industry=rec.client.industry)
            session.add(client)
            await session.flush()
            new_clients += 1
        else:
            reused += 1
        project = Project(org_id=user.org_id, client_id=client.id, upload_id=upload.id, name=rec.project.name,
                          created_by=user.id, restricted=False,
                          url=rec.project.url, description=rec.project.description, record=rec.model_dump())
        session.add(project)
        await session.flush()
        created.append(project.id)
    audit.record(session, request, user, "upload.imported", "upload", upload.id, projects=len(created),
                 skipped_rows=skipped or None)
    await session.commit()
    return ImportOut(created_projects=created, created_clients=new_clients, reused_clients=reused,
                     skipped_rows=skipped)
