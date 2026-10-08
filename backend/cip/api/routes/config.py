"""Versioned organization configuration: analysis settings, scoring profiles, taxonomy, prompts and models.

Analysts can read the configuration (it explains how analyses are produced); only admins change it. Every
change is a new immutable version, audited; runs keep the versions they started with.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.core import prompts as prompt_registry
from cip.core.source_quality import TIER_LABELS
from cip.db.models import ConfigVersion, User
from cip.db.session import get_session
from cip.services import audit, configuration

router = APIRouter(prefix="/api/config", tags=["configuration"])


class SaveIn(BaseModel):
    data: dict
    note: str = Field(default="", max_length=500)


class RevertIn(BaseModel):
    version: int = Field(ge=1)
    note: str = Field(default="", max_length=500)


class VersionOut(BaseModel):
    version: int
    note: str
    created_by_email: str | None
    created_at: datetime


def _kind(kind: str) -> str:
    if kind not in configuration.KINDS:
        raise not_found("Configuration")
    return kind


def _version(v: ConfigVersion) -> VersionOut:
    return VersionOut(version=v.version, note=v.note, created_by_email=v.created_by_email, created_at=v.created_at)


async def _view(session: AsyncSession, org_id: str, kind: str, key: str) -> dict:
    rows = await configuration.history(session, org_id, kind, key)
    current = rows[0] if rows else None
    out = {"kind": kind, "key": key or None, "version": current.version if current else 0,
           "overrides": current.data if current else {}, "effective": configuration.effective(kind, current),
           "defaults": configuration.defaults(kind), "history": [_version(v) for v in rows]}
    if kind == "llm":
        out["prompts"] = prompt_registry.registry(out["effective"].get("prompts"))
    if kind == "analysis":
        out["tiers"] = TIER_LABELS
        out["profiles"] = await configuration.profiles(session, org_id)
    return out


@router.get("")
async def overview(user: User = Depends(require_role("analyst")),
                   session: AsyncSession = Depends(get_session)) -> dict:
    """Active version of each kind and the scoring profiles."""
    out: dict = {}
    for kind in ("analysis", "taxonomy", "llm"):
        row = await configuration.latest(session, user.org_id, kind)
        out[kind] = {"version": row.version if row else 0, "updated_at": row.created_at if row else None,
                     "updated_by": row.created_by_email if row else None}
    profiles = []
    for name in await configuration.profiles(session, user.org_id):
        row = await configuration.latest(session, user.org_id, "scoring_profile", name)
        profiles.append({"name": name, "version": row.version if row else 0})
    default = configuration.effective("analysis", await configuration.latest(session, user.org_id, "analysis"))
    out["scoring_profiles"] = profiles
    out["default_scoring_profile"] = default["default_scoring_profile"]
    return out


@router.get("/{kind}")
async def get_config(kind: str, key: str | None = Query(None, max_length=80),
                     user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> dict:
    kind = _kind(kind)
    return await _view(session, user.org_id, kind, configuration._key(kind, key))


@router.get("/{kind}/versions/{version}")
async def get_config_version(kind: str, version: int, key: str | None = Query(None, max_length=80),
                             user: User = Depends(require_role("analyst")),
                             session: AsyncSession = Depends(get_session)) -> dict:
    kind = _kind(kind)
    row = await configuration.get_version(session, user.org_id, kind, configuration._key(kind, key), version)
    if row is None:
        raise not_found("Configuration version")
    return {**_version(row).model_dump(), "data": row.data, "effective": configuration.effective(kind, row)}


@router.put("/{kind}")
async def save_config(kind: str, body: SaveIn, request: Request, key: str | None = Query(None, max_length=80),
                      admin: User = Depends(require_role("admin")),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """Save a new version. ``data`` holds the overrides (analysis, scoring, llm) or the whole taxonomy."""
    kind = _kind(kind)
    row = await configuration.save(session, admin, kind, key, body.data, body.note)
    audit.record(session, request, admin, "config.saved", "config", f"{kind}:{row.key}" if row.key else kind,
                 version=row.version, note=body.note or None)
    await session.commit()
    return await _view(session, admin.org_id, kind, row.key)


@router.post("/{kind}/revert")
async def revert_config(kind: str, body: RevertIn, request: Request, key: str | None = Query(None, max_length=80),
                        admin: User = Depends(require_role("admin")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Make an earlier version current again (saved as a new version; history is never rewritten)."""
    kind = _kind(kind)
    k = configuration._key(kind, key)
    old = await configuration.get_version(session, admin.org_id, kind, k, body.version)
    if old is None:
        raise not_found("Configuration version")
    row = await configuration.save(session, admin, kind, k, old.data,
                                   body.note or f"Reverted to version {body.version}")
    audit.record(session, request, admin, "config.reverted", "config", f"{kind}:{k}" if k else kind,
                 version=row.version, reverted_to=body.version)
    await session.commit()
    return await _view(session, admin.org_id, kind, k)


@router.get("/runs/{run_id}")
async def run_config(run_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """The configuration a run used."""
    from cip.services.access import run_for

    run = await run_for(session, user, run_id)
    cfg = await configuration.for_run(session, run)
    if not run.config:
        return {"versions": None, "note": "Built-in defaults (run predates versioned configuration)."}
    return {"versions": run.config, "analysis": cfg.analysis, "scoring": cfg.scoring,
            "custom_taxonomy": cfg.taxonomy is not None, "llm": {k: v for k, v in cfg.llm.items() if k != "prompts"},
            "prompt_overrides": sorted(cfg.llm.get("prompts", {}))}
