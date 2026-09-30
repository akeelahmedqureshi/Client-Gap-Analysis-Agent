"""Grounding of LLM extractions against collected source material.

LLM extraction prompts must return, for every fact, the source URL it came
from and a verbatim supporting quote. ``ground`` checks both against the pages
actually fetched: facts citing an unknown URL are dropped, and facts whose
quote cannot be found are kept only with reduced confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from cip.core.evidence import EvidenceLedger, snippet
from cip.core.schemas import Evidence, SourceType

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


class SourcedValue(BaseModel):
    value: str
    source_url: str
    quote: str = ""


def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = _norm(quote)
    if len(q) < 8:
        return False
    t = _norm(text)
    if q in t:
        return True
    # Tolerate small LLM paraphrasing: most 5-word shingles must appear.
    words = q.split()
    if len(words) < 6:
        return False
    shingles = [" ".join(words[i : i + 5]) for i in range(len(words) - 4)]
    hits = sum(1 for s in shingles if s in t)
    return hits / len(shingles) >= 0.7


@dataclass
class SourceDoc:
    url: str
    text: str
    source_type: SourceType
    repository_path: str | None = None


class Grounder:
    def __init__(self, ledger: EvidenceLedger, docs: list[SourceDoc]) -> None:
        self.ledger = ledger
        self.docs = {self._key(d.url): d for d in docs}

    @staticmethod
    def _key(url: str) -> str:
        return url.strip().rstrip("/").lower()

    def ground(self, claim: str, source_url: str, quote: str, base_confidence: float = 0.85) -> Evidence | None:
        doc = self.docs.get(self._key(source_url))
        if doc is None:
            return None
        verified = bool(quote) and quote_in_text(quote, doc.text)
        confidence = base_confidence if verified else min(base_confidence, 0.45)
        extracted = snippet(doc.text, quote if verified else None) if doc.text else None
        if verified:
            extracted = quote[:600]
        return self.ledger.add(claim, doc.url, doc.source_type, confidence,
                               extracted_text=extracted, repository_path=doc.repository_path)

    def ground_value(self, label: str, sv: SourcedValue | None, base_confidence: float = 0.85) -> Evidence | None:
        if sv is None or not sv.value:
            return None
        return self.ground(f"{label}: {sv.value}", sv.source_url, sv.quote, base_confidence)


def pages_to_prompt(docs: list[SourceDoc], budget_chars: int = 24_000) -> str:
    """Render source documents for a prompt, sharing a character budget fairly."""
    if not docs:
        return "(no sources)"
    per = max(1_500, budget_chars // len(docs))
    parts = []
    for d in docs:
        parts.append(f"### SOURCE {d.url}\n{d.text[:per]}")
    return "\n\n".join(parts)[:budget_chars]
