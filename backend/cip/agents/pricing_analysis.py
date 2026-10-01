"""Pricing Analysis Agent: client vs market pricing, and pricing gaps.

Pricing is extracted deterministically from pricing pages during client and
competitor research (``connectors/research/pricing.py``); every plan price and
pricing practice is stored as evidence. This agent compares the client with
the verified competitors that publish pricing and emits pricing gaps when a
practice is common in the market but absent for the client, or when the
client's entry price is far from the market median.
"""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.connectors.research.pricing import Plan, PricingProfile, market_summary
from cip.core.evidence import EvidenceLedger
from cip.core.schemas import AgentResult, Basis, Finding, Gap, GapType

MODEL_LABELS = {"per_seat": "per seat/user", "flat": "flat fee", "usage_based": "usage-based", "tiered": "tiered",
                "freemium": "freemium", "quote_based": "quote only"}
COMMON = 0.5            # a practice is "common" when at least half of the priced competitors use it
PRICE_HIGH = 1.5        # client entry price > 1.5x market median
PRICE_LOW = 0.5         # client entry price < 0.5x market median


def record_pricing(ledger: EvidenceLedger, subject: str, prof: PricingProfile | None) -> dict | None:
    """Store a pricing profile as evidence; returns a JSON-able dict with evidence ids attached."""
    if prof is None:
        return None
    out = prof.as_dict()
    plan_ev = []
    for plan, pd in zip(prof.plans, out["plans"], strict=True):
        claim = (f"{subject} plan '{plan.name}': quote-based (contact sales)" if plan.quote_based else
                 f"{subject} plan '{plan.name}': {plan.price:g} {prof.currency or ''}/{plan.period}"
                 + (f" per {plan.unit}" if plan.unit == "seat" else " (usage-based)" if plan.unit == "usage" else ""))
        ev = ledger.add(claim, prof.source_url, "website", 0.85, extracted_text=plan.snippet)
        pd["evidence_id"] = ev.id
        plan_ev.append(ev.id)
    facts = {}
    for key, label in (("free_trial", f"offers a free trial{f' ({prof.trial_days} days)' if prof.trial_days else ''}"),
                       ("free_tier", "offers a free tier"),
                       ("enterprise_contact", "has a contact-sales / custom enterprise tier"),
                       ("annual_discount", f"offers {prof.annual_discount_pct}% off annual billing")):
        if key in prof.snippets:
            ev = ledger.add(f"{subject} {label}", prof.source_url, "website", 0.85,
                            extracted_text=prof.snippets[key])
            facts[key] = ev.id
    out["fact_evidence"] = facts
    out["evidence_ids"] = plan_ev + list(facts.values())
    return out


def _profile_from_dict(d: dict) -> PricingProfile:
    plans = [Plan(**{k: v for k, v in p.items() if k in Plan.__dataclass_fields__}) for p in d.get("plans", [])]
    return PricingProfile(source_url=d["source_url"], currency=d.get("currency"), plans=plans,
                          models=d.get("models", []), free_tier=d.get("free_tier", False),
                          free_trial=d.get("free_trial", False), trial_days=d.get("trial_days"),
                          enterprise_contact=d.get("enterprise_contact", False),
                          annual_discount_pct=d.get("annual_discount_pct"))


class PricingAnalysisAgent(Agent):
    name = "pricing_analysis"
    description = "Compare the client's pricing with the market and identify pricing gaps"
    after = ("client_research", "competitor_research")

    async def run(self, ctx: RunContext) -> AgentResult:
        client_d = ctx.data("client_research").get("pricing")
        competitors = ctx.data("competitor_research").get("competitors", [])
        priced = [(c, c["pricing_profile"]) for c in competitors if c.get("pricing_profile")]
        market = market_summary([_profile_from_dict(p) for _, p in priced])
        rows = [{"competitor_id": c["id"], "name": c["name"], "classification": c["classification"],
                 "entry_price_monthly": p.get("entry_price_monthly"), "max_price_monthly": p.get("max_price_monthly"),
                 "currency": p.get("currency"), "models": p.get("models", []), "free_trial": p.get("free_trial"),
                 "trial_days": p.get("trial_days"), "free_tier": p.get("free_tier"),
                 "enterprise_contact": p.get("enterprise_contact"), "annual_discount_pct": p.get("annual_discount_pct"),
                 "plans": [pl["name"] for pl in p.get("plans", [])], "source_url": p.get("source_url"),
                 "evidence_ids": p.get("evidence_ids", [])} for c, p in priced]

        findings: list[Finding] = []
        gaps: list[Gap] = []
        position = "unknown"
        n = market.get("competitors_with_pricing", 0)
        if not n:
            findings.append(Finding(category="pricing", title="No competitor pricing could be extracted",
                                    detail="None of the verified competitors publish machine-readable pricing pages.",
                                    confidence=0.8, basis=Basis.INFERRED))
            return AgentResult(confidence=0.3, findings=findings,
                               data={"client": client_d, "market": market, "competitors": rows,
                                     "position": position, "gaps": []})

        ids_where = lambda pred: [e for c, p in priced if pred(p) for e in p.get("evidence_ids", [])[:2]]  # noqa: E731
        names_where = lambda pred: [c["id"] for c, p in priced if pred(p)]  # noqa: E731

        def gap(name: str, description: str, pred, share_key: str, confidence: float = 0.65) -> None:
            gaps.append(Gap(name=name, category="Pricing & packaging", gap_type=GapType.PRICING,
                            description=f"{description} ({market[share_key]:.0%} of {n} priced competitor(s)).",
                            competitors_with=names_where(pred), evidence_ids=ids_where(pred)[:6],
                            confidence=confidence, basis=Basis.INFERRED))

        client = _profile_from_dict(client_d) if client_d else None
        if client is None or not client.entry_price:
            if market["published_prices_share"] >= COMMON:
                gap("Transparent self-serve pricing", "Competitors publish plan prices; the client does not",
                    lambda p: bool(p.get("entry_price_monthly")), "published_prices_share", 0.6)
        if (client is None or not client.free_trial) and market["free_trial_share"] >= COMMON:
            gap("Free trial", "Competitors let prospects try the product before buying", lambda p: p.get("free_trial"),
                "free_trial_share")
        if (client is None or not client.free_tier) and market["free_tier_share"] >= COMMON:
            gap("Free tier (freemium)", "Competitors offer a free plan as an acquisition channel",
                lambda p: p.get("free_tier"), "free_tier_share")
        if client and client.entry_price and client.annual_discount_pct is None \
                and market["annual_discount_share"] >= COMMON:
            gap("Annual billing discount", "Competitors discount annual commitments",
                lambda p: p.get("annual_discount_pct") is not None, "annual_discount_share", 0.6)
        if (client is None or not client.enterprise_contact) and market["enterprise_tier_share"] >= COMMON:
            gap("Enterprise tier", "Competitors package an enterprise / custom plan (SSO, compliance, SLAs)",
                lambda p: p.get("enterprise_contact"), "enterprise_tier_share", 0.6)

        median = market.get("entry_price_median")
        if client and client.entry_price and median and (client.currency in (None, market.get("currency"))):
            ratio = client.entry_price / median
            position = "above" if ratio > PRICE_HIGH else "below" if ratio < PRICE_LOW else "within"
            client_ev = client_d.get("evidence_ids", [])[:2]
            if position == "above":
                gaps.append(Gap(name="Entry price above market", category="Pricing & packaging",
                                gap_type=GapType.PRICING,
                                description=f"Client entry price {client.entry_price:g}/mo is {ratio:.1f}x the market "
                                            f"median of {median:g}/mo; consider a lower-priced entry plan.",
                                competitors_with=[c["id"] for c, _ in priced], confidence=0.6,
                                evidence_ids=client_ev + ids_where(lambda p: bool(p.get("entry_price_monthly")))[:4],
                                basis=Basis.INFERRED))
            findings.append(Finding(category="pricing", title=f"Client entry price is {position} market",
                                    detail=f"{client.entry_price:g}/mo vs median {median:g}/mo "
                                           f"(range {market['entry_price_min']:g}-{market['entry_price_max']:g})",
                                    evidence_ids=client_ev, confidence=0.7, basis=Basis.INFERRED))
        for g in gaps:
            findings.append(Finding(category="gap.pricing", title=g.name, detail=g.description,
                                    evidence_ids=g.evidence_ids, confidence=g.confidence, basis=g.basis))
        return AgentResult(
            confidence=0.7 if client_d else 0.5,
            findings=findings,
            data={"client": client_d, "market": market, "competitors": rows, "position": position,
                  "gaps": [g.model_dump(mode="json") for g in gaps]},
        )
