"""Pricing extraction, market comparison and pricing gaps."""

from cip.connectors.research.pricing import extract_pricing, market_summary
from cip.connectors.research.web import parse_html
from test_pipeline import run_all


def _page(url, body, title="Pricing"):
    return parse_html(url, 200, f"<html><head><title>{title}</title></head><body>{body}</body></html>")


def test_extracts_plans_models_and_practices():
    prof = extract_pricing([_page("https://x.io/pricing",
        "<h2>Starter</h2><p>€19 per user / month</p><h2>Business</h2><p>€190 per year</p>"
        "<p>Try free for 7 days. Usage-based SMS at €0.05 per message.</p><p>Talk to sales</p><p>Raised €5M.</p>")])
    by_name = {p.name: p for p in prof.plans}
    assert by_name["Starter"].monthly == 19 and by_name["Starter"].unit == "seat"
    assert round(by_name["Business"].monthly, 2) == 15.83 and by_name["Business"].period == "year"
    assert prof.currency == "EUR" and prof.free_trial and prof.trial_days == 7 and prof.enterprise_contact
    assert {"per_seat", "tiered", "usage_based"} <= set(prof.models)
    assert all(p.price != 5 for p in prof.plans)  # "Raised €5M" is not a price


def test_non_pricing_pages_are_ignored():
    assert extract_pricing([_page("https://x.io/about", "<p>$49 /month</p>", title="About us")]) is None


def test_market_summary_never_mixes_currencies():
    usd = extract_pricing([_page("https://a.io/pricing", "<h2>Pro</h2><p>$40 /month</p>")])
    usd2 = extract_pricing([_page("https://b.io/pricing", "<h2>Pro</h2><p>$60 /month</p>")])
    eur = extract_pricing([_page("https://c.io/pricing", "<h2>Pro</h2><p>€5 /month</p>")])
    m = market_summary([usd, usd2, eur])
    assert m["currency"] == "USD" and m["entry_price_median"] == 50 and m["excluded_other_currency"] == 1


async def test_pricing_analysis_end_to_end(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    client = ctx.data("client_research")["pricing"]
    assert client["entry_price_monthly"] == 79 and "per_seat" in client["models"] and not client["free_trial"]
    comps = {c["name"]: c for c in ctx.data("competitor_research")["competitors"]}
    assert comps["ClinicFlow"]["pricing_profile"]["annual_discount_pct"] == 20
    assert comps["ClinicFlow"]["pricing"] == "from 49 USD/month"
    assert comps["MediBook"]["pricing_profile"]["free_tier"] is True

    pa = ctx.data("pricing_analysis")
    assert pa["market"]["entry_price_median"] == 39 and pa["position"] == "above"  # 79 vs median 39
    gaps = {g["name"]: g for g in ctx.data("gap_analysis")["gaps"] if g["gap_type"] == "pricing"}
    assert {"Free trial", "Entry price above market", "Annual billing discount", "Enterprise tier",
            "Free tier (freemium)"} <= set(gaps)
    assert "Transparent self-serve pricing" not in gaps            # the client does publish prices
    assert len(gaps["Free trial"]["competitors_with"]) == 2
    for g in gaps.values():                                        # every pricing gap cites real evidence
        assert g["evidence_ids"] and all(e in ctx.ledger for e in g["evidence_ids"])

    md = ctx.data("report")["markdown"]
    assert "### Pricing" in md and "| **Client** | 79 USD |" in md and "Client position: **above** market" in md
    plans = ctx.data("enhancement_planning")["plans"]
    # "Free trial" is a top opportunity here (every priced competitor offers one; low effort) and pricing gaps
    # get a commercial / billing plan, not a generic feature plan.
    trial_plan = next(p for p in plans if p["feature"] == "Free trial")
    assert any("Billing provider" in b for b in trial_plan["backend_changes"])
    # A free trial on a pricing page is a pricing practice, not evidence of guided onboarding.
    assert "Guided onboarding" not in {g["name"] for g in ctx.data("gap_analysis")["gaps"]}
