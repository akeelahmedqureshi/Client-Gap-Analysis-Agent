"""Versioned, admin-editable configuration per organization (BRS 39, 41; PRD 10.37, 22).

Four kinds, each stored as immutable versions (``config_versions``); the newest version of a kind is the
active one, and "revert" saves an old version's content as a new version:

* ``analysis`` — research depth and analysis knobs (Top-N competitors, deep-analysis count, pages, PDFs,
  roadmap size, freshness thresholds, source-quality tier factors, the default scoring profile);
* ``scoring_profile`` (keyed by profile name) — factor weights, evidence-confidence weight, priority bands
  and phase thresholds. A run may pick a profile; otherwise the default profile (or the built-in one) applies;
* ``taxonomy`` — the capability catalogue (categories, features, keywords, code signals, default factors);
* ``llm`` — model and temperature (default and per agent) and prompt-text overrides (core/prompts.py).

When a run is created it records the versions in effect (``AnalysisRun.config``); the run, its resumes and
its partial re-runs always use exactly those versions, so editing configuration never silently changes a
historical analysis (BRS 39). Version 0 means the built-in defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.config import Settings, get_settings
from cip.core import prompts as prompt_registry
from cip.core.scoring import ALL_FACTORS, ScoringConfig
from cip.core.security.secrets import scan as scan_secrets
from cip.core.taxonomy import TAXONOMY_PATH, Taxonomy, load_taxonomy, taxonomy_from_dict
from cip.db.models import AnalysisRun, ConfigVersion, User

KINDS = ("analysis", "scoring_profile", "taxonomy", "llm")
DEFAULT_PROFILE = "Standard"
# Analysis settings an admin may change: (type, min, max).
ANALYSIS_FIELDS: dict[str, tuple[type, float, float]] = {
    "max_competitors": (int, 1, 20),
    "deep_competitors": (int, 1, 5),
    "crawler_max_pages": (int, 3, 50),
    "competitor_light_pages": (int, 1, 5),
    "competitor_deep_pages": (int, 2, 30),
    "crawler_max_pdfs": (int, 0, 10),
    "ux_max_pages": (int, 1, 10),
    "roadmap_top_n": (int, 3, 30),
    "freshness_fresh_days": (int, 1, 365),
    "freshness_stale_days": (int, 7, 3650),
}
SCORING_RANGES: dict[str, tuple[float, float]] = {
    "confidence_weight": (0, 1), "high_min_normalized": (0, 1), "medium_min_normalized": (0, 1),
    "high_min_confidence": (0, 1), "quick_win_max_complexity": (0, 5), "growth_max_complexity": (0, 5),
    "major_max_complexity": (0, 5), "strategic_min_ai_opportunity": (0, 5),
}
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,59}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9._\-/:]{1,100}$")


def bad(message: str) -> HTTPException:
    return HTTPException(422, message)


# --------------------------------------------------------------------------- defaults


def analysis_defaults(s: Settings | None = None) -> dict:
    s = s or get_settings()
    return {**{k: getattr(s, k) for k in ANALYSIS_FIELDS},
            "source_tier_factors": {str(k): v for k, v in s.source_tier_factors.items()},
            "default_scoring_profile": DEFAULT_PROFILE}


def scoring_defaults() -> dict:
    return ScoringConfig().model_dump()


def taxonomy_defaults() -> dict:
    import yaml

    return yaml.safe_load(TAXONOMY_PATH.read_text())


def llm_defaults(s: Settings | None = None) -> dict:
    s = s or get_settings()
    return {"default_model": s.openrouter_model, "temperature": s.llm_temperature, "agents": {}, "prompts": {}}


def defaults(kind: str) -> dict:
    return {"analysis": analysis_defaults, "scoring_profile": scoring_defaults, "taxonomy": taxonomy_defaults,
            "llm": llm_defaults}[kind]()


# --------------------------------------------------------------------------- validation


def _number(value, name: str, lo: float, hi: float, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not float(value).is_integer()):
        raise bad(f"{name} must be {'a whole number' if integer else 'a number'}")
    if not lo <= value <= hi:
        raise bad(f"{name} must be between {lo:g} and {hi:g}")
    return int(value) if integer else float(value)


def validate_analysis(data: dict) -> dict:
    out: dict = {}
    for key, value in data.items():
        if key in ANALYSIS_FIELDS:
            typ, lo, hi = ANALYSIS_FIELDS[key]
            out[key] = _number(value, key, lo, hi, integer=typ is int)
        elif key == "source_tier_factors":
            if not isinstance(value, dict) or {str(k) for k in value} != {"1", "2", "3", "4", "5"}:
                raise bad("source_tier_factors needs a factor for each tier 1-5")
            out[key] = {str(k): _number(v, f"tier {k} factor", 0.3, 1.0) for k, v in value.items()}
        elif key == "default_scoring_profile":
            if not isinstance(value, str) or not PROFILE_NAME.match(value):
                raise bad("default_scoring_profile must be a profile name")
            out[key] = value
        else:
            raise bad(f"Unknown analysis setting '{key}'")
    merged = {**analysis_defaults(), **out}
    if merged["deep_competitors"] > merged["max_competitors"]:
        raise bad("deep_competitors cannot exceed max_competitors")
    if merged["freshness_fresh_days"] >= merged["freshness_stale_days"]:
        raise bad("freshness_fresh_days must be below freshness_stale_days")
    return out


def validate_scoring(data: dict) -> dict:
    unknown = set(data) - {"weights", *SCORING_RANGES}
    if unknown:
        raise bad(f"Unknown scoring settings: {sorted(unknown)}")
    out: dict = {}
    if "weights" in data:
        weights = data["weights"]
        if not isinstance(weights, dict) or set(weights) - set(ALL_FACTORS):
            raise bad(f"weights may only use the factors {list(ALL_FACTORS)}")
        out["weights"] = {k: _number(v, f"weight {k}", 0, 5) for k, v in weights.items()}
    for key, (lo, hi) in SCORING_RANGES.items():
        if key in data:
            out[key] = _number(data[key], key, lo, hi)
    merged = ScoringConfig(**{**scoring_defaults(), **out,
                              "weights": {**scoring_defaults()["weights"], **out.get("weights", {})}})
    if merged.medium_min_normalized >= merged.high_min_normalized:
        raise bad("medium_min_normalized must be below high_min_normalized")
    if not merged.quick_win_max_complexity <= merged.growth_max_complexity <= merged.major_max_complexity:
        raise bad("Phase thresholds must increase: quick win ≤ growth ≤ major")
    return out


def validate_taxonomy(data: dict) -> dict:
    try:
        taxonomy_from_dict(data, strict=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise bad(f"Invalid taxonomy: {exc}") from exc
    return {"categories": data["categories"]}


def validate_llm(data: dict) -> dict:
    from cip.agents.orchestrator import default_agents

    unknown = set(data) - {"default_model", "temperature", "agents", "prompts"}
    if unknown:
        raise bad(f"Unknown LLM settings: {sorted(unknown)}")
    out: dict = {}
    if data.get("default_model") is not None:
        if not isinstance(data["default_model"], str) or not MODEL_NAME.match(data["default_model"]):
            raise bad("default_model must be a model id such as provider/model-name")
        out["default_model"] = data["default_model"]
    if data.get("temperature") is not None:
        out["temperature"] = _number(data["temperature"], "temperature", 0, 1.5)
    agents = {a.name for a in default_agents()}
    out["agents"] = {}
    for agent, cfg in (data.get("agents") or {}).items():
        if agent not in agents or not isinstance(cfg, dict) or set(cfg) - {"model", "temperature"}:
            raise bad(f"Unknown agent or setting in agents.{agent}")
        entry = {}
        if cfg.get("model"):
            if not MODEL_NAME.match(str(cfg["model"])):
                raise bad(f"agents.{agent}.model must be a model id")
            entry["model"] = cfg["model"]
        if cfg.get("temperature") is not None:
            entry["temperature"] = _number(cfg["temperature"], f"agents.{agent}.temperature", 0, 1.5)
        if entry:
            out["agents"][agent] = entry
    out["prompts"] = {}
    for pid, text in (data.get("prompts") or {}).items():
        if pid not in prompt_registry.PROMPTS:
            raise bad(f"Unknown prompt '{pid}'")
        if not isinstance(text, str) or not text.strip() or len(text) > prompt_registry.MAX_PROMPT_CHARS:
            raise bad(f"Prompt '{pid}' must be 1-{prompt_registry.MAX_PROMPT_CHARS} characters")
        if scan_secrets(text):
            raise bad(f"Prompt '{pid}' looks like it contains a secret; prompts must never carry credentials")
        if text.strip() != prompt_registry.default_text(pid).strip():
            out["prompts"][pid] = text
    return out


VALIDATORS = {"analysis": validate_analysis, "scoring_profile": validate_scoring, "taxonomy": validate_taxonomy,
              "llm": validate_llm}


# --------------------------------------------------------------------------- storage


def _key(kind: str, key: str | None) -> str:
    if kind != "scoring_profile":
        return ""
    key = (key or DEFAULT_PROFILE).strip()
    if not PROFILE_NAME.match(key):
        raise bad("Profile names are 1-60 letters, digits, spaces, '-' or '_'")
    return key


async def latest(session: AsyncSession, org_id: str, kind: str, key: str = "") -> ConfigVersion | None:
    return (await session.execute(select(ConfigVersion).where(
        ConfigVersion.org_id == org_id, ConfigVersion.kind == kind, ConfigVersion.key == key)
        .order_by(ConfigVersion.version.desc()).limit(1))).scalar_one_or_none()


async def get_version(session: AsyncSession, org_id: str, kind: str, key: str, version: int) -> ConfigVersion | None:
    return (await session.execute(select(ConfigVersion).where(
        ConfigVersion.org_id == org_id, ConfigVersion.kind == kind, ConfigVersion.key == key,
        ConfigVersion.version == version))).scalar_one_or_none()


async def history(session: AsyncSession, org_id: str, kind: str, key: str = "") -> list[ConfigVersion]:
    return list((await session.execute(select(ConfigVersion).where(
        ConfigVersion.org_id == org_id, ConfigVersion.kind == kind, ConfigVersion.key == key)
        .order_by(ConfigVersion.version.desc()))).scalars())


async def profiles(session: AsyncSession, org_id: str) -> list[str]:
    keys = (await session.execute(select(ConfigVersion.key).where(
        ConfigVersion.org_id == org_id, ConfigVersion.kind == "scoring_profile").distinct())).scalars().all()
    return sorted({DEFAULT_PROFILE, *keys})


async def save(session: AsyncSession, user: User, kind: str, key: str | None, data: dict, note: str = "") \
        -> ConfigVersion:
    if kind not in KINDS:
        raise bad(f"Kind must be one of {KINDS}")
    key = _key(kind, key)
    clean = VALIDATORS[kind](data)
    if kind == "analysis" and "default_scoring_profile" in clean and \
            clean["default_scoring_profile"] not in await profiles(session, user.org_id):
        raise bad(f"Scoring profile '{clean['default_scoring_profile']}' does not exist")
    current = (await session.execute(select(func.max(ConfigVersion.version)).where(
        ConfigVersion.org_id == user.org_id, ConfigVersion.kind == kind, ConfigVersion.key == key))).scalar()
    row = ConfigVersion(org_id=user.org_id, kind=kind, key=key, version=(current or 0) + 1, data=clean,
                        note=note[:500], created_by=user.id, created_by_email=user.email)
    session.add(row)
    await session.flush()
    return row


def effective(kind: str, row: ConfigVersion | None) -> dict:
    """Stored overrides merged over the built-in defaults."""
    base = defaults(kind)
    if row is None:
        return base
    if kind == "taxonomy":
        return row.data
    if kind == "scoring_profile":
        return {**base, **row.data, "weights": {**base["weights"], **row.data.get("weights", {})}}
    if kind == "llm":
        return {**base, **{k: v for k, v in row.data.items() if v not in (None, {})},
                "agents": row.data.get("agents", {}), "prompts": row.data.get("prompts", {})}
    return {**base, **row.data}


# --------------------------------------------------------------------------- runs


async def snapshot(session: AsyncSession, org_id: str, profile: str | None = None) -> dict:
    """The configuration versions a new run uses (0 = built-in defaults)."""
    analysis = await latest(session, org_id, "analysis")
    profile = profile or effective("analysis", analysis)["default_scoring_profile"]
    scoring = await latest(session, org_id, "scoring_profile", _key("scoring_profile", profile))
    if profile != DEFAULT_PROFILE and scoring is None:
        raise bad(f"Scoring profile '{profile}' does not exist")
    taxonomy = await latest(session, org_id, "taxonomy")
    llm = await latest(session, org_id, "llm")
    return {"analysis": analysis.version if analysis else 0,
            "scoring_profile": {"name": profile, "version": scoring.version if scoring else 0},
            "taxonomy": taxonomy.version if taxonomy else 0, "llm": llm.version if llm else 0}


@dataclass
class RunConfig:
    versions: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    scoring: dict = field(default_factory=dict)
    taxonomy: Taxonomy | None = None
    llm: dict = field(default_factory=dict)

    def settings(self, base: Settings | None = None) -> Settings:
        base = base or get_settings()
        update = {k: v for k, v in self.analysis.items() if k in ANALYSIS_FIELDS}
        if "source_tier_factors" in self.analysis:
            update["source_tier_factors"] = {int(k): float(v) for k, v in self.analysis["source_tier_factors"].items()}
        return base.model_copy(update=update)


async def _version_data(session: AsyncSession, org_id: str, kind: str, key: str, version: int) -> ConfigVersion | None:
    return await get_version(session, org_id, kind, key, version) if version else None


async def for_run(session: AsyncSession, run: AnalysisRun) -> RunConfig:
    """Resolve exactly the configuration versions recorded on ``run`` (built-ins for older runs)."""
    snap = run.config or {}
    if not snap:
        return RunConfig(versions={}, analysis=analysis_defaults(), scoring=scoring_defaults(), llm={})
    prof = snap.get("scoring_profile") or {}
    analysis = await _version_data(session, run.org_id, "analysis", "", snap.get("analysis", 0))
    scoring = await _version_data(session, run.org_id, "scoring_profile", prof.get("name", DEFAULT_PROFILE),
                                  prof.get("version", 0))
    taxonomy = await _version_data(session, run.org_id, "taxonomy", "", snap.get("taxonomy", 0))
    llm = await _version_data(session, run.org_id, "llm", "", snap.get("llm", 0))
    return RunConfig(versions=snap, analysis=effective("analysis", analysis),
                     scoring=effective("scoring_profile", scoring),
                     taxonomy=taxonomy_from_dict(taxonomy.data) if taxonomy else None,
                     llm=llm.data if llm else {})


async def run_taxonomy(session: AsyncSession, run: AnalysisRun) -> Taxonomy:
    return (await for_run(session, run)).taxonomy or load_taxonomy()


def scoring_config(data: dict) -> ScoringConfig:
    try:
        return ScoringConfig(**data)
    except ValidationError as exc:  # stored versions were validated; this guards hand-edited rows
        raise bad(f"Invalid scoring profile: {exc}") from exc
