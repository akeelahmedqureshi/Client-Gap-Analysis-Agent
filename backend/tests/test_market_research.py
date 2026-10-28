"""Market research from pages that were read: verified statements, figures with their context, no snippets."""

from cip.connectors.research.search import SearchProvider, SearchResult
from cip.connectors.research.web import WebFetcher
from cip.core import market_research
from cip.core.evidence import EvidenceLedger

from fakes import ScriptedLLM, html, web_transport

STATS = "https://www.census.gov/clinics-2025"
REPORT = "https://www.grandviewresearch.com/clinic-scheduling"
BLOG = "https://someblog.example/scheduling"
SITES = {
    STATS: html("Clinics 2025", "<p>There were 210,000 outpatient clinics in the United States in 2025. "
                                "Most clinics now offer online appointment booking.</p>" + "<p>Detail.</p>" * 40),
    REPORT: html("Clinic scheduling market", "<p>The global patient scheduling software market was valued at "
                                             "USD 351.6 million in 2023 and is expected to grow at a CAGR of 11.4% "
                                             "from 2024 to 2030. Clinics increasingly expect AI-driven reminders. "
                                             "HIPAA compliance remains a key buying criterion.</p>" + "<p>More.</p>" * 40),
    BLOG: html("Tips", "<p>Ten tips.</p>" + "<p>Filler.</p>" * 60),
}


class Search(SearchProvider):
    name = "fake"

    async def search(self, query, limit=10):
        return [SearchResult("G2 list", "https://www.g2.com/categories/scheduling", "reviews"),
                SearchResult("Client", "https://abc-healthcare.com/blog", "our blog"),
                SearchResult("Tips", BLOG, "tips"), SearchResult("Report", REPORT, "market size"),
                SearchResult("Census", STATS, "clinics"), SearchResult("Gone", "https://gone.example/x", "404")]


def st(statement, url, quote):
    return {"statement": statement, "source_url": url, "quote": quote}


ANSWER = {
    "figures": [
        {"metric": "Global patient scheduling software market size", "value": "USD 351.6 million", "period": "2023",
         "geography": "Global", "original_source": "Grand View Research", "source_url": REPORT,
         "quote": "valued at USD 351.6 million in 2023"},
        {"metric": "Market growth", "value": "11.4% CAGR", "period": "2024-2030", "source_url": REPORT,
         "quote": "expected to grow at a CAGR of 11.4%"},                     # geography not stated
        {"metric": "Number of clinics", "value": "250,000", "period": "2025", "geography": "United States",
         "source_url": STATS, "quote": "There were 210,000 outpatient clinics"},  # number not in the quote
    ],
    "trends": [st("Clinics expect AI-driven reminders", REPORT, "Clinics increasingly expect AI-driven reminders")],
    "selection_criteria": [st("HIPAA compliance is a key buying criterion", REPORT,
                              "HIPAA compliance remains a key buying criterion")],
    "customer_needs": [st("Clinics want fewer no-shows", STATS, "Clinics hate no-shows")],   # not on the page
    "qualitative_assessment": "A growing, compliance-driven market.",
}


async def test_reads_sources_and_keeps_only_verified_statements_and_figures():
    ledger = EvidenceLedger()
    fetcher = WebFetcher(transport=web_transport(SITES))
    profile = {"industry": "Healthcare", "market_segment": "clinic scheduling software", "geography": ["United States"]}
    r = await market_research.research(Search(), fetcher, ScriptedLLM({"_LLMMarket": ANSWER}), ledger, profile,
                                       client_domain="abc-healthcare.com", max_sources=8)
    assert r["available"]
    read = {s["url"] for s in r["sources"]}
    assert {STATS, REPORT} <= read
    assert not any("g2.com" in u or "abc-healthcare.com" in u for u in read)   # directories and the client: never
    assert "https://gone.example/x" in r["unread"]
    assert [s["statement"] for s in r["sections"]["trends"]] == ["Clinics expect AI-driven reminders"]
    assert "customer_needs" not in r["sections"] and r["dropped"] >= 2
    figs = {f["metric"]: f for f in r["figures"]}
    assert figs["Global patient scheduling software market size"]["complete"]
    assert figs["Global patient scheduling software market size"]["original_source"] == "Grand View Research"
    growth = figs["Market growth"]
    assert growth["geography"] is None and not growth["complete"]               # shown, but "not stated"
    assert "Number of clinics" not in figs                                       # number not in its quote
    for f in r["figures"]:
        ev = ledger.get(f["evidence_id"])
        assert ev.source_type == "publication" and ev.source_tier in (3, 4)
    assert ledger.get(figs["Global patient scheduling software market size"]["evidence_id"]).source_tier == 4
    assert r["qualitative_assessment"]


def test_queries_follow_the_client_profile():
    qs = market_research.queries({"industry": "Healthcare", "market_segment": "clinic scheduling software",
                                  "geography": ["Canada"], "customer_segment": ["clinics"]}, 2026)
    assert "clinic scheduling software market size Canada" in qs and "Healthcare industry trends 2026" in qs


async def test_market_tab_trends_come_from_read_pages(make_ctx):
    from test_pipeline import run_all

    ctx = make_ctx(llm=ScriptedLLM({"_LLMMarket": ANSWER}), search=Search(),
                   fetcher=WebFetcher(transport=web_transport({**SITES, **__import__("fakes").SITES})))
    await run_all(ctx)
    market = ctx.data("industry_market")
    assert market["research"]["available"]
    assert [t["statement"] for t in market["trends"]] == ["Clinics expect AI-driven reminders"]
