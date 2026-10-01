"""Company enrichment: structured data, legal name, job boards / hiring signals, announcements."""

from cip.connectors.research.enrichment import (
    Job,
    find_announcements,
    find_ats_boards,
    hiring_signals,
    legal_name_from_footer,
    organization_facts,
)
from cip.connectors.research.web import parse_html
from test_pipeline import run_all


def test_jsonld_graph_and_footer_parsing():
    page = parse_html("https://acme.io/", 200, """<html><head>
      <script type="application/ld+json">{"@graph":[{"@type":["Organization","Corporation"],"legalName":"Acme Labs GmbH",
        "foundingDate":"2019","numberOfEmployees":42,"subOrganization":{"@type":"Organization","name":"Acme Cloud"}},
        {"@type":"WebSite","name":"x"}]}</script>
      <script type="application/ld+json">{ not valid json </script></head>
      <body><p>Text</p><footer>Copyright © 2020–2026 Acme Labs GmbH. All rights reserved.</footer></body></html>""")
    facts = {(f.label, f.value) for f in organization_facts([page])}
    assert {("legal_name", "Acme Labs GmbH"), ("founded_year", "2019"), ("company_size", "42"),
            ("subsidiary", "Acme Cloud")} <= facts
    assert legal_name_from_footer([page]).value == "Acme Labs GmbH"


def test_ats_detection_and_hiring_areas():
    page = parse_html("https://acme.io/careers", 200, "<a href='https://jobs.lever.co/acme'>Jobs</a>"
                      "<a href='https://jobs.ashbyhq.com/acme-labs'>More</a>")
    boards = {(b.provider, b.token) for b in find_ats_boards([page])}
    assert boards == {("lever", "acme"), ("ashby", "acme-labs")}
    areas = {s["area"]: s["count"] for s in hiring_signals(
        [Job("Data Scientist"), Job("ML Engineer"), Job("Site Reliability Engineer"), Job("Office Manager")])}
    assert areas == {"AI / Machine learning": 2, "Cloud / DevOps / SRE": 1}


def test_announcements_skip_navigation_links():
    page = parse_html("https://acme.io/news", 200, "<a href='/news/acme-launches-copilot'>Acme launches Copilot for clinics</a>"
                      "<a href='/news/page/2'>Next</a><a href='/news'>All news</a><a href='/pricing'>Pricing</a>")
    items = find_announcements([page])
    assert [(a.title, a.is_product) for a in items] == [("Acme launches Copilot for clinics", True)]


async def test_pipeline_enrichment_end_to_end(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    research = ctx.data("client_research")
    p = research["profile"]
    assert p["legal_name"] == "ABC Healthcare Holdings Inc."      # schema.org beats the footer/text
    assert p["founded_year"] == 2012 and p["headquarters"] == "Austin, TX, US"
    assert p["company_size"] == "51-200"
    assert p["geographic_markets"] == ["United States", "Canada"] and "Patient Scheduler" in p["brands"]
    assert any(c["type"] == "youtube" for c in p["contacts"])   # sameAs -> social profile

    hiring = research["hiring"]
    assert hiring["job_count"] == 4 and hiring["sources"] == ["greenhouse:https://boards.greenhouse.io/abchealth"]
    ai = next(s for s in hiring["signals"] if s["area"] == "AI / Machine learning")
    assert ai["count"] == 2 and ctx.ledger.get(ai["evidence_id"]).source_url == "https://boards.greenhouse.io/abchealth"

    titles = {a["title"]: a for a in research["announcements"]}
    assert titles["Introducing SMS reminders for every clinic"]["is_product"] is True
    assert titles["A practical guide to reducing patient no-shows"]["is_product"] is False

    # Hiring for AI raises strategic alignment of the AI gap and cites the job-board evidence.
    recs = ctx.data("opportunity_prioritization")["recommendations"]
    ai_rec = next(r for r in recs if r["feature"] == "AI assistant / chatbot")
    assert ai["evidence_id"] in ai_rec["evidence_ids"]
    opp = next(o for o in ctx.data("opportunity_prioritization")["opportunities"] if o["name"] == "AI assistant / chatbot")
    assert opp["factors"]["strategic_alignment"] == 5 and "hiring 2 AI / Machine learning" in opp["business_opportunity"]

    md = ctx.data("report")["markdown"]
    assert "### Hiring signals" in md and "AI / Machine learning | 2" in md
    assert "### Recent announcements" in md and "**Product:** [Introducing SMS reminders" in md
    assert "**Legal Name:** ABC Healthcare Holdings Inc." in md and "**Markets served:** United States, Canada" in md
