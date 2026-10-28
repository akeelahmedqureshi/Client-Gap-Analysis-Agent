"""Plan-level pricing: only prices the page proves, terms quoted, conflicts flagged, normalization labelled."""

from cip.connectors.research.web import parse_html
from cip.core import pricing_detail
from cip.core.evidence import EvidenceLedger

from fakes import SITES, ScriptedLLM
from test_pipeline import run_all

CF = "https://clinicflow.com/pricing"
PAGES = [parse_html(CF, 200, SITES[CF])]


def plan(name, price, quote, **kw):
    return {"name": name, "price": price, "currency": "USD", "period": "month", "unit": "user", "source_url": CF,
            "quote": quote, **kw}


ANSWER = {
    "plans": [
        plan("Starter", 49, "$49 /month per provider",
             included_features=[{"text": "14-day free trial", "source_url": CF, "quote": "Start a 14-day free trial"}],
             limits=[{"text": "Up to 3 providers", "source_url": CF, "quote": "Up to 3 providers"}]),  # not on page
        plan("Growth", 99, "$99 /month per provider"),
        plan("Premium", 199, "$199 /month per provider"),                       # invented plan
        plan("Scale", 59, "$49 /month per provider"),                           # price not in its quote
        {"name": "Enterprise", "quote_based": True, "source_url": CF, "quote": "Custom pricing - contact sales."},
    ],
    "free_trial": [{"text": "14-day free trial", "source_url": CF, "quote": "Start a 14-day free trial."}],
    "discounts": [{"text": "20% off with annual billing", "source_url": CF, "quote": "Save 20% with annual billing."}],
    "cancellation_terms": [{"text": "Cancel anytime", "source_url": CF, "quote": "Cancel anytime, no fees."}],
}


async def test_only_proven_prices_and_quoted_terms_are_kept():
    ledger = EvidenceLedger()
    d = await pricing_detail.extract(ScriptedLLM({"_LLMPricing": ANSWER}), ledger, PAGES, "ClinicFlow",
                                     rates={"USD": 1.0, "EUR": 1.1})
    assert d["available"]
    by = {p["name"]: p for p in d["plans"]}
    assert set(by) == {"Starter", "Growth", "Enterprise"}
    assert {u["plan"] for u in d["unverified"]} == {"Premium", "Scale"}
    assert "not in the quoted text" in next(u["reason"] for u in d["unverified"] if u["plan"] == "Scale")
    starter = by["Starter"]
    assert starter["monthly"] == 49 and starter["monthly_normalized"] == 49 and starter["listing"] == "public list price"
    assert [f["text"] for f in starter["included_features"]] == ["14-day free trial"] and starter["limits"] == []
    assert ledger.get(starter["evidence_id"]).source_url == CF and starter["accessed_at"]
    ent = by["Enterprise"]
    assert ent["quote_based"] and ent["price"] is None and ent["listing"] == "quote-based"
    assert set(d["terms"]) == {"free_trial", "discounts"}                       # invented cancellation term dropped
    assert "Cancellation terms" in d["not_stated"]


async def test_a_price_the_parser_read_differently_is_flagged():
    det = {"plans": [{"name": "Starter", "monthly": 49.0}]}
    answer = {"plans": [plan("Starter", 49, "$49 /month per provider", period="year")]}
    d = await pricing_detail.extract(ScriptedLLM({"_LLMPricing": answer}), EvidenceLedger(), PAGES, "ClinicFlow",
                                     deterministic=det)
    assert d["plans"][0]["conflict"] and "49/month by the price parser" in d["conflicts"][0]["detail"]


async def test_no_pricing_page_and_no_llm_are_explained():
    home = [parse_html("https://medibook.io/", 200, SITES["https://medibook.io/"])]
    d = await pricing_detail.extract(ScriptedLLM({}), EvidenceLedger(), home, "MediBook")
    assert not d["available"] and "No public pricing page" in d["reason"]
    d = await pricing_detail.extract(ScriptedLLM({}), EvidenceLedger(), PAGES, "ClinicFlow")
    assert not d["available"] and "AI extraction" in d["reason"]


def test_normalization_keeps_the_original_and_converts_with_configured_rates():
    assert pricing_detail.monthly(120, "year") == 10 and pricing_detail.monthly(5, "one_time") is None
    rates = {"USD": 1.0, "EUR": 1.1}
    assert pricing_detail.convert(100, "EUR", "USD", rates) == 110
    assert pricing_detail.convert(110, "USD", "EUR", rates) == 100
    assert pricing_detail.convert(100, "XYZ", "USD", rates) is None             # unknown currency: not converted


async def test_pricing_tab_compares_plans_with_implications(make_ctx):
    def answer(user):
        if "Company: ClinicFlow" in user:
            return ANSWER
        if "Company: MediBook" in user:
            mb = "https://medibook.io/pricing"
            return {"plans": [{"name": "Free", "price": 0, "free": True, "source_url": mb,
                               "quote": "Free forever for one practitioner."},
                              {"name": "Pro", "price": 29, "currency": "USD", "period": "month", "unit": "user",
                               "source_url": mb, "quote": "$29 /month per practitioner"}]}
        return {"plans": []}

    ctx = make_ctx(llm=ScriptedLLM({"_LLMPricing": answer}))
    await run_all(ctx)
    cmp = ctx.data("pricing_analysis")["comparison"]
    rows = {(r["subject"], r["name"]): r for r in cmp["rows"]}
    assert rows[("MediBook", "Pro")]["detailed"] and rows[("ClinicFlow", "Starter")]["monthly_normalized"] == 49
    assert ("ClinicFlow", "Premium") not in rows
    client_rows = [r for r in cmp["rows"] if r["kind"] == "client"]
    assert client_rows and all(not r["detailed"] for r in client_rows)          # parser fallback, labelled
    texts = " ".join(i["text"] for i in cmp["implications"])
    assert "Lowest published paid entry: MediBook" in texts and "Free plan: MediBook" in texts
    assert "Custom / contact-sales plans at ClinicFlow" in texts
    assert all(i["evidence_ids"] for i in cmp["implications"])
