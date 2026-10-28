"""Negated statements: "X is not available", "no X", "X is coming soon" do not evidence that X is offered.

Used by keyword matching (a keyword only counts in a sentence that does not negate its availability) and by
the feature extraction ("confirmed unavailable" needs a quote that actually says the capability is not
offered). Deliberately narrow: "SMS reminders reduce no-shows" or "no setup fees" do not negate SMS reminders.
"""

from __future__ import annotations

import re

# Phrases that negate availability, in the supported languages.
AVAILABILITY = re.compile(
    r"\b(?:not|isn'?t|aren'?t|is not|are not)\s+(?:yet\s+|currently\s+)?(?:available|supported|offered|included|"
    r"possible)\b|\b(?:does|do|did|can)\s*n[o']?t\s+(?:yet\s+)?(?:support|offer|include|provide|have|allow)\b|"
    r"\bdon'?t\s+(?:yet\s+)?(?:support|offer|include|provide)\b|\bdoesn'?t\s+(?:yet\s+)?(?:support|offer|include|"
    r"provide|have|allow)\b|\b(?:cannot|can'?t)\b|\b(?:unavailable|unsupported)\b|\bcoming soon\b|\bnot yet\b|"
    r"\bon (?:the|our) roadmap\b|\b(?:join|on) the waitlist\b|"
    r"\bno (?:está|están) disponibles?\b|\bno (?:es|son) compatibles?\b|\bpróximamente\b|"
    r"\bn'est pas (?:disponible|pris en charge)\b|\bbientôt disponible\b|"
    r"\bnicht (?:verfügbar|unterstützt|enthalten)\b|\bdemnächst\b|"
    r"\bnon (?:è|sono) (?:disponibili|disponibile|supportat[oiae])\b|\bprossimamente\b|"
    r"\bnão (?:está|estão) disponíve(?:l|is)\b|\bem breve\b|\bniet beschikbaar\b|\bbinnenkort\b",
    re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")
_NO_BEFORE = r"\b(?:no|without|sin|sans|ohne|senza|sem|geen|keine?n?)\s+(?:\w+\s+)?"


def negates(text: str) -> bool:
    """The text says something is not offered (for an explicit 'unavailable' statement)."""
    return bool(AVAILABILITY.search(text or "")) or bool(re.search(r"\b(?:no|without)\b", text or "", re.I))


def affirmed(text: str, keywords: list[str]) -> list[str]:
    """The keywords that appear at least once in a sentence that does not negate their availability."""
    sentences = _SENTENCES.split(text or "")
    out = []
    from cip.core.taxonomy import _phrase_pattern  # the same matching as the taxonomy's keywords

    for kw in keywords:
        pat = _phrase_pattern(kw)
        direct_no = re.compile(_NO_BEFORE + pat.pattern, re.IGNORECASE)
        if any(pat.search(s) and not AVAILABILITY.search(s) and not direct_no.search(s) for s in sentences):
            out.append(kw)
    return out
