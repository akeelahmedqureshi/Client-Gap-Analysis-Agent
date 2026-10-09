"""Loader and matching helpers for the normalized feature taxonomy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

TAXONOMY_PATH = Path(__file__).with_name("taxonomy.yaml")
I18N_PATH = Path(__file__).with_name("taxonomy_i18n.yaml")


@lru_cache
def i18n_keywords() -> dict[str, tuple[str, ...]]:
    """Multilingual keywords per feature id (core/taxonomy_i18n.yaml), merged into matching for every taxonomy,
    including organization-edited ones (by feature id)."""
    raw = yaml.safe_load(I18N_PATH.read_text()) or {}
    return {fid: tuple(dict.fromkeys(k.lower() for words in langs.values() for k in words)) for fid, langs in raw.items()}


@dataclass(frozen=True)
class TaxonomyFeature:
    id: str
    name: str
    category_id: str
    category_name: str
    keywords: tuple[str, ...]
    code_signals: tuple[str, ...]
    defaults: dict[str, float] = field(hash=False)
    ai: bool = False


class Taxonomy:
    def __init__(self, features: list[TaxonomyFeature]) -> None:
        self.features = features
        self._by_id = {f.id: f for f in features}
        self._kw_patterns = {
            f.id: [_phrase_pattern(k) for k in f.keywords] for f in features
        }

    def get(self, feature_id: str) -> TaxonomyFeature | None:
        return self._by_id.get(feature_id)

    def __contains__(self, feature_id: str) -> bool:
        return feature_id in self._by_id

    def ids(self) -> list[str]:
        return list(self._by_id)

    def match_text(self, text: str) -> dict[str, list[str]]:
        """Return ``{feature_id: [matched keywords]}`` for phrases present in ``text``."""
        out: dict[str, list[str]] = {}
        if not text:
            return out
        for fid, patterns in self._kw_patterns.items():
            feature = self._by_id[fid]
            hits = [kw for kw, pat in zip(feature.keywords, patterns) if pat.search(text)]
            if hits:
                out[fid] = hits
        return out

    def match_code_signal(self, token: str) -> list[str]:
        """Feature ids whose code signals match a dependency name or file path."""
        token_l = token.lower()
        hits = []
        for f in self.features:
            for sig in f.code_signals:
                if sig.startswith("/"):
                    if sig in token_l:
                        hits.append(f.id)
                        break
                elif token_l == sig or token_l.startswith(sig + "-") or token_l.startswith(sig + "/") \
                        or token_l.endswith("/" + sig) or token_l.startswith("@" + sig):
                    hits.append(f.id)
                    break
        return hits

    def describe_for_prompt(self) -> str:
        lines = []
        for f in self.features:
            lines.append(f"- {f.id}: {f.name} ({f.category_name})")
        return "\n".join(lines)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.lower()).replace(r"\ ", r"[\s\-]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


@lru_cache
def load_taxonomy(path: str | None = None) -> Taxonomy:
    return taxonomy_from_dict(yaml.safe_load(Path(path or TAXONOMY_PATH).read_text()))


CATEGORY_ID = re.compile(r"^[a-z0-9_]{1,40}$")
FEATURE_ID = re.compile(r"^[a-z0-9_]{1,40}\.[a-z0-9_]{1,60}$")
MAX_FEATURES = 400


def _check(raw: dict) -> None:
    """Validation for an organization-edited taxonomy (services/configuration.py)."""
    from cip.core.scoring import ALL_FACTORS

    cats = raw.get("categories")
    if not isinstance(cats, list) or not cats:
        raise ValueError("categories must be a non-empty list")
    seen_cats, seen = set(), set()
    for cat in cats:
        if not isinstance(cat, dict) or not CATEGORY_ID.match(str(cat.get("id", ""))):
            raise ValueError(f"category id {cat.get('id')!r} must be lowercase letters, digits or _")
        if cat["id"] in seen_cats:
            raise ValueError(f"duplicate category id {cat['id']!r}")
        seen_cats.add(cat["id"])
        if not str(cat.get("name", "")).strip():
            raise ValueError(f"category {cat['id']!r} needs a name")
        for f in cat.get("features") or []:
            fid = str(f.get("id", ""))
            if not FEATURE_ID.match(fid):
                raise ValueError(f"feature id {fid!r} must look like category.feature (lowercase)")
            if fid in seen:
                raise ValueError(f"duplicate feature id {fid!r}")
            seen.add(fid)
            if not str(f.get("name", "")).strip() or len(str(f["name"])) > 120:
                raise ValueError(f"feature {fid!r} needs a name (max 120 characters)")
            for key in ("keywords", "code_signals"):
                values = f.get(key, [])
                if not isinstance(values, list) or len(values) > 60 or \
                        any(not isinstance(v, str) or not v.strip() or len(v) > 80 for v in values):
                    raise ValueError(f"{fid}.{key} must be a list of up to 60 phrases (80 characters each)")
            defaults = f.get("defaults", {})
            if not isinstance(defaults, dict) or set(defaults) - set(ALL_FACTORS) or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 5 for v in defaults.values()):
                raise ValueError(f"{fid}.defaults must map scoring factors to 0-5")
            if "ai" in f and not isinstance(f["ai"], bool):
                raise ValueError(f"{fid}.ai must be true or false")
    if len(seen) > MAX_FEATURES:
        raise ValueError(f"at most {MAX_FEATURES} features")
    if not seen:
        raise ValueError("the taxonomy needs at least one feature")


def taxonomy_from_dict(raw: dict, strict: bool = False) -> Taxonomy:
    if strict:
        _check(raw)
    features: list[TaxonomyFeature] = []
    for cat in raw["categories"]:
        for f in cat["features"]:
            features.append(
                TaxonomyFeature(
                    id=f["id"],
                    name=f["name"],
                    category_id=cat["id"],
                    category_name=cat["name"],
                    keywords=tuple(dict.fromkeys([*(k.lower() for k in f.get("keywords", [])),
                                                  *i18n_keywords().get(f["id"], ())])),
                    code_signals=tuple(s.lower() for s in f.get("code_signals", [])),
                    defaults=dict(f.get("defaults", {})),
                    ai=bool(f.get("ai", cat["id"] == "ai")),
                )
            )
    return Taxonomy(features)
