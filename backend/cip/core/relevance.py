"""Competitor relevance scoring for Top-10 ranking (BRS 7.5-7.6, PRD 10.7 ``CompetitorScore``).

Every verified candidate gets nine factor scores (0-1) computed deterministically from its own website
and the client's profile, and a weighted overall relevance. Market presence has a small weight on
purpose: the ranking optimises for *relevance to this client*, not company size (BRS 7.5).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

FACTORS = ("feature_overlap", "product_similarity", "industry_similarity", "customer_similarity",
           "geographic_relevance", "business_model_similarity", "market_presence", "product_maturity",
           "evidence_confidence")
DEFAULT_WEIGHTS = {
    "feature_overlap": 0.25, "product_similarity": 0.18, "industry_similarity": 0.12, "customer_similarity": 0.13,
    "geographic_relevance": 0.05, "business_model_similarity": 0.07, "market_presence": 0.06,
    "product_maturity": 0.06, "evidence_confidence": 0.08,
}
LABELS = {
    "feature_overlap": "overlapping capabilities", "product_similarity": "a similar product",
    "industry_similarity": "the same industry", "customer_similarity": "the same customers",
    "geographic_relevance": "the same markets", "business_model_similarity": "a similar business model",
    "market_presence": "market presence", "product_maturity": "a mature product",
    "evidence_confidence": "strong evidence",
}
_WORD = re.compile(r"[a-z][a-z0-9-]{2,}")
_STOP = {"the", "and", "for", "with", "software", "platform", "solution", "solutions", "system", "app", "apps",
         "online", "management", "services", "service", "tool", "tools", "your", "our", "their", "from", "that",
         "this", "all", "any", "more", "best", "free", "new", "get", "use", "one"}
MODEL_CUES = {
    "subscription": ("/month", "per month", "monthly", "subscription", "per user", "per seat", "/mo", "billed annually"),
    "free_tier": ("free forever", "free plan", "freemium", "free tier"),
    "free_trial": ("free trial", "-day trial", "day free trial", "try it free", "try for free"),
    "enterprise_contact": ("contact sales", "custom pricing", "request a quote", "book a demo", "request a demo"),
    "open_source": ("open source", "open-source", "github.com", "self-hosted"),
}


# Vocabulary that signals an industry on a competitor's site, beyond the industry's own name.
INDUSTRY_TERMS = {
    "health": "health healthcare clinic clinics patient patients medical hipaa hospital practice care dental therapy",
    "medic": "medical clinic patient hipaa hospital practice physician",
    "dental": "dental dentist clinic patient practice",
    "veterin": "veterinary vet pet clinic animal",
    "financ": "finance financial banking bank payments lending accounting fintech",
    "bank": "banking bank finance financial accounts",
    "insur": "insurance policy claims underwriting broker",
    "logist": "logistics shipping freight fleet delivery warehouse dispatch carrier",
    "transport": "transport transportation fleet delivery routes",
    "educat": "education school students learning teachers university courses",
    "retail": "retail store shop ecommerce merchants checkout inventory",
    "ecomme": "ecommerce online store shop merchants checkout cart",
    "real": "real estate property properties tenants landlords leasing",
    "legal": "legal law lawyers attorneys firms cases",
    "hospit": "hospitality hotel hotels restaurant restaurants guests booking",
    "beauty": "beauty salon salons spa barber",
    "fitnes": "fitness gym gyms studio classes members",
    "manufa": "manufacturing factory production plant supply",
    "constru": "construction contractors projects sites builders",
}


def industry_terms(industry: str) -> set[str]:
    out = tokens(industry)
    for key, words in INDUSTRY_TERMS.items():
        if any(t.startswith(key) for t in out):
            out |= tokens(words)
    return out


def tokens(text: str) -> set[str]:
    return {w[:6] for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def model_signals(text: str) -> set[str]:
    low = (text or "").lower()
    return {k for k, cues in MODEL_CUES.items() if any(c in low for c in cues)}


@dataclass
class ClientProfile:
    features: set[str]
    product_words: set[str]
    industry_words: set[str] = field(default_factory=set)
    customer_words: set[str] = field(default_factory=set)
    geo_words: set[str] = field(default_factory=set)
    model: set[str] = field(default_factory=set)


@dataclass
class CandidateSignals:
    text: str
    features: set[str]
    marketplace: bool = False
    search_mentions: int = 0
    oss_stars: int = 0
    has_pricing: bool = False
    has_docs: bool = False
    evidence_count: int = 1


def _share(wanted: set[str], have: set[str], unknown: float = 0.5) -> float:
    if not wanted:
        return unknown
    return round(len(wanted & have) / len(wanted), 3)


def score_candidate(client: ClientProfile, cand: CandidateSignals,
                    weights: dict[str, float] | None = None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    words = tokens(cand.text)
    model = model_signals(cand.text)
    f = {
        "feature_overlap": _share(client.features, cand.features),
        "product_similarity": min(1.0, round(_share(client.product_words, words, 0.3) * 1.5, 3)),
        # Industry vocabulary is broad, so a few shared terms already show the same industry.
        "industry_similarity": (min(1.0, round(len(client.industry_words & words) / 3, 3))
                                if client.industry_words else 0.5),
        "customer_similarity": _share(client.customer_words, words),
        "geographic_relevance": 1.0 if client.geo_words and client.geo_words & words else 0.5,
        "business_model_similarity": (round(len(client.model & model) / len(client.model | model), 3)
                                      if client.model and model else 0.5),
        "market_presence": round(min(1.0, 0.4 * cand.marketplace + 0.2 * min(cand.search_mentions, 3)
                                     + (min(0.6, math.log10(cand.oss_stars + 1) / 5) if cand.oss_stars else 0)), 3),
        "product_maturity": round(min(1.0, 0.3 * cand.has_pricing + 0.3 * cand.has_docs
                                      + 0.4 * min(1.0, len(cand.features) / 8)), 3),
        "evidence_confidence": round(min(1.0, 0.5 + 0.05 * cand.evidence_count), 3),
    }
    total = sum(w[k] for k in FACTORS) or 1.0
    overall = round(sum(w[k] * f[k] for k in FACTORS) / total, 3)
    strong = sorted((k for k in FACTORS if k not in ("evidence_confidence",) and f[k] >= 0.6),
                    key=lambda k: -w[k] * f[k])[:3]
    reason = ("Shares " + ", ".join(LABELS[k] for k in strong)) if strong else "Partial fit with the client's market"
    if f["feature_overlap"] >= 0.3:
        reason += f" ({f['feature_overlap']:.0%} of the client's capabilities)"
    return {"factors": f, "overall": overall, "reason": reason + "."}


def comparable(result: dict) -> bool:
    """A candidate must resemble the client's product or capabilities to be a competitor at all."""
    f = result["factors"]
    return f["feature_overlap"] >= 0.1 or f["product_similarity"] >= 0.3
