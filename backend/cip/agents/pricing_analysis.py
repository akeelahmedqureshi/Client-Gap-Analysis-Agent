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
from cip.core import pricing_detail
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


def comparison_rows(subject: str, kind: str, detail: dict | None, profile: dict | None, ledger: EvidenceLedger,
                    settings) -> list[dict]:
    """One row per plan: the grounded plan-level detail when available, else the price parser's plans."""
    base, rates = settings.pricing_base_currency, settings.pricing_fx_rates
    if detail and detail.get("available") and detail.get("plans"):
        return [{**p, "subject": subject, "kind": kind, "detailed": True} for p in detail["plans"]]
    rows = []
    for p in (profile or {}).get("plans", []):
        ev = ledger.get(p.get("evidence_id") or "")
        cur = (profile or {}).get("currency")
        rows.append({"subject": subject, "kind": kind, "detailed": False, "name": p["name"], "price": p.get("price"),
                     "currency": cur, "period": p.get("period"), "unit": p.get("unit"), "monthly": p.get("monthly"),
                     "monthly_normalized": pricing_detail.convert(p.get("monthly"), cur, base, rates),
                     "annual_price_per_month": None, "quote_based": p.get("quote_based", False),
                     "free": p.get("price") == 0, "included_features": [], "limits": [], "add_ons": [],
                     "evidence_id": p.get("evidence_id"), "source_url": (profile or {}).get("source_url"),
                     "accessed_at": ev.collected_at.isoformat() if ev else None,
                     "listing": "quote-based" if p.get("quote_based") else "public list price"})
    return rows


def implications(rows: list[dict], details: dict[str, dict], base: str) -> list[dict]:
    """What the pricing differences mean for customers, each statement tied to its plan evidence."""
    out: list[dict] = []
    subjects = sorted({r["subject"] for r in rows})
    paid = [r for r in rows if r.get("monthly_normalized") and not r.get("quote_based")]
    entries: dict[str, dict] = {}
    for r in sorted(paid, key=lambda r: r["monthly_normalized"]):
        entries.setdefault(r["subject"], r)
    if len(entries) >= 2:
        ordered = sorted(entries.values(), key=lambda r: r["monthly_normalized"])
        lo, hi = ordered[0], ordered[-1]
        out.append({"text": f"Lowest published paid entry: {lo['subject']} ({lo['name']}, ≈{lo['monthly_normalized']:g} "
                            f"{base}/month); highest: {hi['subject']} ({hi['name']}, ≈{hi['monthly_normalized']:g} "
                            f"{base}/month). Entry price is about {hi['monthly_normalized'] / lo['monthly_normalized']:.1f}x "
                            "apart, before plan contents are considered.",
                    "evidence_ids": [x for x in (lo.get("evidence_id"), hi.get("evidence_id")) if x]})
    seat = sorted({r["subject"] for r in paid if r.get("unit") in ("user", "seat")})
    flat = sorted({r["subject"] for r in paid if r.get("unit") in (None, "flat")} - set(seat))
    if seat:
        out.append({"text": f"Per-user / per-seat pricing at {', '.join(seat)}: cost grows with team size"
                            + (f"; {', '.join(flat)} price per plan, which favours larger teams." if flat else "."),
                    "evidence_ids": [r["evidence_id"] for r in paid if r["subject"] in seat and r.get("evidence_id")][:4]})
    free = sorted({r["subject"] for r in rows if r.get("free")})
    if free:
        out.append({"text": f"Free plan: {', '.join(free)} — a no-cost way to start"
                            + (f"; not found for {', '.join(s for s in subjects if s not in free)}." if len(free) < len(subjects) else "."),
                    "evidence_ids": [r["evidence_id"] for r in rows if r.get("free") and r.get("evidence_id")][:4]})
    quote = sorted({r["subject"] for r in rows if r.get("quote_based")})
    if quote:
        out.append({"text": f"Custom / contact-sales plans at {', '.join(quote)}: their price is not public and cannot "
                            "be compared.",
                    "evidence_ids": [r["evidence_id"] for r in rows if r.get("quote_based") and r.get("evidence_id")][:4]})
    for key, label in (("transaction_fees", "Transaction or processing fees"), ("onboarding_fees", "Onboarding fees"),
                       ("contract_terms", "Contract requirements")):
        who = sorted(s for s, d in details.items() if (d or {}).get("terms", {}).get(key))
        if who:
            out.append({"text": f"{label} published by {', '.join(who)}: they change the real cost beyond the list price.",
                        "evidence_ids": [t["evidence_id"] for s in who for t in details[s]["terms"][key]][:4]})
    return out


class PricingAnalysisAgent(Agent):
    name = "pricing_analysis"
    description = "Compare the client's pricing with the market and identify pricing gaps"
    after = ("client_research", "competitor_research")

    async def run(self, ctx: RunContext) -> AgentResult:
        client_d = ctx.data("client_research").get("pricing")
        competitors = ctx.data("competitor_research").get("competitors", [])
        s = ctx.settings
        client_name = ctx.record.client.name
        research = ctx.data("client_research")
        client_detail = await pricing_detail.extract(
            ctx.llm, ctx.ledger, research.get("pages", []) + research.get("project_pages", []), client_name,
            base_currency=s.pricing_base_currency, rates=s.pricing_fx_rates, rates_date=s.pricing_fx_date,
            deterministic=client_d)
        details = {client_name: client_detail, **{c["name"]: c.get("pricing_detail") for c in competitors}}
        table = comparison_rows(client_name, "client", client_detail, client_d, ctx.ledger, s)
        for c in competitors:
            table += comparison_rows(c["name"], "competitor", c.get("pricing_detail"), c.get("pricing_profile"),
                                     ctx.ledger, s)
        comparison = {"rows": table, "implications": implications(table, details, s.pricing_base_currency),
                      "base_currency": s.pricing_base_currency, "rates_date": s.pricing_fx_date,
                      "details": details}
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
                                     "position": position, "gaps": [], "comparison": comparison})

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
                  "gaps": [g.model_dump(mode="json") for g in gaps], "comparison": comparison},
        )
