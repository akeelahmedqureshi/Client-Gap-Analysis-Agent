"""Plan-level pricing research from official pricing pages, grounded and normalized.

The deterministic extractor (connectors/research/pricing.py) finds prices and plan names; it cannot say what a
plan includes. This module asks the LLM for the full picture — plans, prices, units, included features, limits,
add-ons, fees, trial, contract and cancellation terms, discounts — and keeps only what the page proves:

* a plan's price is kept only when its quote is found on the page **and contains the price**; otherwise the
  plan is listed under ``unverified`` with the reason, never in the comparison;
* included features, limits, add-ons and terms need a verbatim quote found on the page, or are dropped;
* a plan the deterministic extractor priced differently is flagged as a conflict;
* custom / contact-sales plans are labelled quote-based and never given a price;
* prices are normalized to a monthly figure and to the organization's base currency with **configured**
  exchange rates (labelled approximate, with their date); the original price and currency are always kept;
* every claim keeps its source URL and the date the page was accessed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from cip.connectors.research.pages import kind_of
from cip.core.evidence import EvidenceLedger
from cip.core.grounding import Grounder, SourceDoc, pages_to_prompt
from cip.core.llm import LLMClient, LLMError, LLMUnavailable

TERMS = ("pricing_model", "usage_charges", "transaction_fees", "free_trial", "onboarding_fees", "contract_terms",
         "cancellation_terms", "discounts", "additional_costs", "enterprise")
TERM_LABELS = {"pricing_model": "Pricing model", "usage_charges": "Usage-based charges",
               "transaction_fees": "Transaction / processing fees", "free_trial": "Free trial",
               "onboarding_fees": "Implementation / onboarding fees", "contract_terms": "Contract requirements",
               "cancellation_terms": "Cancellation terms", "discounts": "Discounts",
               "additional_costs": "Additional costs", "enterprise": "Enterprise pricing"}


class _Quoted(BaseModel):
    text: str
    source_url: str = ""
    quote: str = ""


class _Plan(BaseModel):
    name: str
    price: float | None = None
    currency: str | None = None  # ISO code, e.g. USD
    period: str | None = None    # month | year | one_time
    unit: str | None = None      # user | seat | usage | flat
    annual_price_per_month: float | None = None  # per-month price when billed annually, if published
    quote_based: bool = False
    free: bool = False
    source_url: str = ""
    quote: str = ""              # the text that states the price (or "contact sales")
    included_features: list[_Quoted] = Field(default_factory=list)
    limits: list[_Quoted] = Field(default_factory=list)
    add_ons: list[_Quoted] = Field(default_factory=list)


class _LLMPricing(BaseModel):
    plans: list[_Plan] = Field(default_factory=list)
    pricing_model: list[_Quoted] = Field(default_factory=list)
    usage_charges: list[_Quoted] = Field(default_factory=list)
    transaction_fees: list[_Quoted] = Field(default_factory=list)
    free_trial: list[_Quoted] = Field(default_factory=list)
    onboarding_fees: list[_Quoted] = Field(default_factory=list)
    contract_terms: list[_Quoted] = Field(default_factory=list)
    cancellation_terms: list[_Quoted] = Field(default_factory=list)
    discounts: list[_Quoted] = Field(default_factory=list)
    additional_costs: list[_Quoted] = Field(default_factory=list)
    enterprise: list[_Quoted] = Field(default_factory=list)


PROMPT = """You are a pricing analyst reading a company's official pricing page(s). Extract every plan exactly
as published: name, price as a number (null if not published), ISO currency, period (month | year |
one_time), unit (user, seat, usage or flat), the per-month price when billed annually if the page states it,
quote_based=true for custom / contact-sales plans, free=true for free plans, and the source_url with a short
verbatim quote that contains the price (or the contact-sales wording). For each plan list included_features,
limits (usage limits, caps, restrictions) and add_ons, each with a verbatim quote. Also list, each with a
verbatim quote: pricing_model, usage_charges, transaction_fees, free_trial, onboarding_fees, contract_terms,
cancellation_terms, discounts, additional_costs and enterprise. Copy quotes exactly from the page. Never guess
a price or a term the page does not state; leave it out instead."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number_in(price: float, text: str) -> bool:
    """The published price appears in the quote (1,299 / 1299 / 1.299,00 / 29.00 all count)."""
    digits = re.sub(r"[^\d]", "", text)
    whole = str(int(price)) if float(price).is_integer() else None
    candidates = {f"{price:g}".replace(".", ""), f"{price:.2f}".replace(".", "")}
    if whole:
        candidates.add(whole)
    return any(c and c in digits for c in candidates)


def monthly(price: float | None, period: str | None) -> float | None:
    if price is None:
        return None
    if period == "year":
        return round(price / 12, 2)
    if period == "month":
        return round(price, 2)
    return None


def convert(amount: float | None, currency: str | None, base: str, rates: dict[str, float]) -> float | None:
    """``amount`` in ``currency`` → ``base`` with configured rates (units of base per unit of currency)."""
    if amount is None or not currency:
        return None
    currency, base = currency.upper(), base.upper()
    if currency == base:
        return round(amount, 2)
    if currency in rates and base in rates and rates[base]:
        return round(amount * rates[currency] / rates[base], 2)
    return None


def pricing_docs(pages: list) -> list[SourceDoc]:
    """Official pricing pages (Page objects or page dicts)."""
    out = []
    for p in pages:
        url = p["url"] if isinstance(p, dict) else p.url
        title = p.get("title", "") if isinstance(p, dict) else p.title
        text = p.get("text", "") if isinstance(p, dict) else p.text
        if kind_of(url) == "pricing" or "pricing" in title.lower():
            out.append(SourceDoc(url, f"{title}\n{text}", "website"))
    return out


async def extract(llm: LLMClient, ledger: EvidenceLedger, pages: list, subject: str, *, base_currency: str = "USD",
                  rates: dict[str, float] | None = None, rates_date: str = "", deterministic: dict | None = None,
                  accessed_at: datetime | None = None) -> dict:
    """Plan-level pricing detail for ``subject``; never raises for a missing LLM (returns ``available=False``)."""
    docs = pricing_docs(pages)
    accessed = (accessed_at or _now()).isoformat()
    if not docs:
        return {"available": False, "reason": "No public pricing page was found on the pages analysed.", "plans": [],
                "terms": {}, "unverified": [], "conflicts": []}
    try:
        result = await llm.complete_json(PROMPT, f"Company: {subject}\n\n{pages_to_prompt(docs, 30_000)}", _LLMPricing)
    except LLMUnavailable:
        return {"available": False, "reason": "Plan-level detail needs the AI extraction (not configured).",
                "plans": [], "terms": {}, "unverified": [], "conflicts": [], "source_urls": [d.url for d in docs]}
    except LLMError as exc:
        return {"available": False, "reason": f"Pricing extraction failed: {exc}", "plans": [], "terms": {},
                "unverified": [], "conflicts": [], "source_urls": [d.url for d in docs]}
    g = Grounder(ledger, docs)
    rates = rates or {}

    def quoted(items: list[_Quoted], label: str) -> list[dict]:
        out = []
        for q in items:
            e = g.ground_verified(f"{subject} pricing — {label}: {q.text}", q.source_url, q.quote, 0.85)
            if e and q.text.strip():
                out.append({"text": q.text.strip(), "evidence_id": e.id, "source_url": q.source_url,
                            "accessed_at": accessed})
        return out

    plans, unverified, conflicts = [], [], []
    det = {p["name"].strip().lower(): p for p in (deterministic or {}).get("plans", [])}
    for p in result.plans:
        if not g.is_verified(p.source_url, p.quote):
            unverified.append({"plan": p.name, "reason": "the plan's quote was not found on the pricing page"})
            continue
        is_free = p.free or p.price == 0
        if is_free and not re.search(r"\bfree\b|gratis|gratuit|kostenlos|\b0\b", p.quote, re.I):
            unverified.append({"plan": p.name, "reason": "the quoted text does not say the plan is free"})
            continue
        if p.price is not None and not is_free and not p.quote_based and not _number_in(p.price, p.quote):
            unverified.append({"plan": p.name, "reason": f"the stated price {p.price:g} is not in the quoted text"})
            continue
        label = ("quote-based (contact sales)" if p.quote_based else "free" if p.free or p.price == 0 else
                 f"{p.price:g} {p.currency or ''}/{p.period or '?'}" + (f" per {p.unit}" if p.unit in ("user", "seat")
                                                                        else ""))
        ev = g.ground_verified(f"{subject} plan '{p.name}': {label}", p.source_url, p.quote, 0.9)
        price = None if p.quote_based else (0.0 if p.free and p.price is None else p.price)
        per_month = monthly(price, p.period or ("month" if price == 0 else None))
        plan = {
            "name": p.name.strip(), "price": price, "currency": (p.currency or "").upper() or None,
            "period": p.period, "unit": p.unit, "monthly": per_month,
            # kept only when the plan's own quote states it
            "annual_price_per_month": p.annual_price_per_month if p.annual_price_per_month is not None
            and _number_in(p.annual_price_per_month, p.quote) else None,
            "monthly_normalized": convert(per_month, p.currency, base_currency, rates),
            "quote_based": p.quote_based, "free": bool(p.free or price == 0),
            "included_features": quoted(p.included_features, f"{p.name} includes"),
            "limits": quoted(p.limits, f"{p.name} limit"), "add_ons": quoted(p.add_ons, f"{p.name} add-on"),
            "evidence_id": ev.id if ev else None, "source_url": p.source_url, "accessed_at": accessed,
            "listing": "quote-based" if p.quote_based else "public list price",
        }
        d = det.get(plan["name"].lower())
        if d and d.get("monthly") and per_month and abs(d["monthly"] - per_month) > max(0.5, per_month * 0.02):
            conflicts.append({"plan": plan["name"], "detail": f"read as {per_month:g}/month here but "
                                                             f"{d['monthly']:g}/month by the price parser"})
            plan["conflict"] = True
        plans.append(plan)
    terms = {t: quoted(getattr(result, t), TERM_LABELS[t]) for t in TERMS}
    terms = {k: v for k, v in terms.items() if v}
    not_found = [TERM_LABELS[t] for t in TERMS if t not in terms]
    return {"available": True, "plans": plans, "terms": terms, "unverified": unverified, "conflicts": conflicts,
            "not_stated": not_found, "source_urls": [d.url for d in docs], "accessed_at": accessed,
            "base_currency": base_currency, "rates_date": rates_date}
