"""The report carries the detailed data the tabs show: sourced company facts, feature details, documented
workflows, plan-level pricing, competitor profiles, gap verdicts and the quality checks."""

from cip.agents.reporting import render_markdown

from fakes import ScriptedLLM
from test_comparison_detail import ABC, features, pricing
from test_pipeline import run_all

WORKFLOWS = {"workflows": [{
    "name": "Book an appointment", "objective": "A patient books a visit online",
    "actors": [{"name": "Patient", "role": "Patient"}],
    "steps": [{"action": "Open the patient portal", "actor": "Patient", "source_url": ABC,
               "quote": "Online booking for clinics with a patient portal"},
              {"action": "Receive a confirmation", "actor": "System", "kind": "system", "inferred": True},
              {"action": "Book online", "actor": "Patient", "source_url": ABC, "quote": "Online booking for clinics"}],
    "outcome": "The visit is booked"}]}


def workflows(user):
    return WORKFLOWS if "Company: ABC Healthcare" in user else {"workflows": []}


async def test_report_carries_the_detailed_analysis(make_ctx):
    ctx = make_ctx(llm=ScriptedLLM({"_LLMFeatures": features, "_LLMPricing": pricing, "_LLMWorkflows": workflows}))
    await run_all(ctx)
    report = ctx.data("report")
    md = report["markdown"]
    assert md == render_markdown(report["report"])

    def section(title: str) -> str:
        start = md.index(title)
        nxt = md.find("\n## ", start + 1)
        return md[start: nxt if nxt > 0 else None]

    s2, s3 = section("## 2. Client Overview"), section("## 3. Client Website")
    assert "| Fact | Value | Source | Verification |" in s2
    assert "| Feature | Status | Evidence grade |" in s3 and "documented" in s3
    assert "### Feature details" in s3 and "Limitations: Clinics only" in s3
    assert "### Documented workflows" in s3 and "**Book an appointment** — 2 of 3 steps documented" in s3
    assert "Receive a confirmation _(System)_ _(inferred)_" in s3
    assert "Typical workflows for this kind of product _(inferred)_" in s3

    s4 = section("## 4. Industry")
    assert "### Plan-level pricing" in s4 and "| MediBook | Pro | 29 USD/month per user |" in s4

    s6 = section("## 6. Top 3")
    assert "**Both offer:**" in s6 and "**Plans:** Pro — 29 USD/month" in s6

    s7 = section("## 7. Feature Comparison")
    assert "❌ confirmed unavailable" in s7

    s8 = section("## 8. Feature Gap Analysis")
    assert "confirmed," in s8 and "Only confirmed gaps are stated to the client." in s8
    assert "Potential (needs validation) — open:" in s8

    appendix = section("## Appendix B.")
    assert "**Cross-tab consistency:**" in appendix and "**Corroboration of key findings**" in appendix
