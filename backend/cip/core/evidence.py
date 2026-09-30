"""Evidence ledger: the single source of truth for facts collected during a run.

Research agents write evidence here; analysis agents may only reference
evidence ids that exist in the ledger. ``validate_refs`` is used to strip any
reference that does not resolve (e.g. an id hallucinated by an LLM), so the
final report can only cite evidence that was really collected.
"""

from __future__ import annotations

from collections.abc import Iterable

from cip.core.schemas import Evidence, SourceType

MAX_EXTRACT_CHARS = 600


def snippet(text: str | None, needle: str | None = None, width: int = 240) -> str | None:
    """Return a short excerpt of ``text`` centred on ``needle`` when present."""
    if not text:
        return None
    text = " ".join(text.split())
    if needle:
        idx = text.lower().find(needle.lower())
        if idx >= 0:
            start = max(0, idx - width // 2)
            end = min(len(text), idx + len(needle) + width // 2)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(text) else ""
            return f"{prefix}{text[start:end]}{suffix}"
    return text[:MAX_EXTRACT_CHARS] + ("…" if len(text) > MAX_EXTRACT_CHARS else "")


class EvidenceLedger:
    def __init__(self, items: Iterable[Evidence] = ()) -> None:
        self._items: dict[str, Evidence] = {}
        self._keys: dict[tuple, str] = {}
        for item in items:
            self._store(item)

    def _key(self, ev: Evidence) -> tuple:
        return (ev.claim.strip().lower(), ev.source_url, ev.repository_path, ev.line_range)

    def _store(self, ev: Evidence) -> Evidence:
        key = self._key(ev)
        existing_id = self._keys.get(key)
        if existing_id:
            existing = self._items[existing_id]
            if ev.confidence > existing.confidence:
                existing.confidence = ev.confidence
            return existing
        self._items[ev.id] = ev
        self._keys[key] = ev.id
        return ev

    def add(
        self,
        claim: str,
        source_url: str,
        source_type: SourceType,
        confidence: float,
        extracted_text: str | None = None,
        repository_path: str | None = None,
        line_range: str | None = None,
    ) -> Evidence:
        ev = Evidence(
            claim=claim,
            source_url=source_url,
            source_type=source_type,
            confidence=max(0.0, min(1.0, confidence)),
            extracted_text=(extracted_text or None) and extracted_text[:MAX_EXTRACT_CHARS],
            repository_path=repository_path,
            line_range=line_range,
        )
        return self._store(ev)

    def extend(self, items: Iterable[Evidence]) -> list[Evidence]:
        return [self._store(i) for i in items]

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def __contains__(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def validate_refs(self, ids: Iterable[str]) -> list[str]:
        seen: list[str] = []
        for i in ids:
            if i in self._items and i not in seen:
                seen.append(i)
        return seen

    def mean_confidence(self, ids: Iterable[str]) -> float:
        vals = [self._items[i].confidence for i in ids if i in self._items]
        return round(sum(vals) / len(vals), 3) if vals else 0.0
