"""Competitor discovery sources: GitHub open-source alternatives and review/launch directories."""

from fakes import SITES, FakeSourceControl, ScriptedLLM, web_transport
from test_pipeline import run_all

from cip.connectors.research.web import WebFetcher

GITHUB = {
    "https://api.github.com/search/repositories?q=healthcare+appointment*": {"items": [
        {"name": "openappointments", "html_url": "https://github.com/openclinic/openappointments",
         "stargazers_count": 1200, "fork": False, "description": "Open-source clinic scheduling"},
        {"name": "tiny-scheduler", "html_url": "https://github.com/someone/tiny-scheduler",
         "stargazers_count": 7, "fork": False, "description": "toy project"},
        {"name": "forked", "html_url": "https://github.com/x/forked", "stargazers_count": 999, "fork": True},
    ]},
    "https://api.github.com/repos/openclinic/openappointments": {
        "description": "Open-source clinic scheduling", "stargazers_count": 1200, "pushed_at": "2026-09-01T00:00:00Z"},
    "https://api.github.com/repos/openclinic/openappointments/readme":
        "# OpenAppointments\nOnline booking for clinics, SMS reminders, email reminders and a patient portal.\n"
        "Self-host with Docker. REST API documentation included.",
}


def _ctx(make_ctx, llm=None):
    return make_ctx(llm=llm, fetcher=WebFetcher(transport=web_transport({**SITES, **GITHUB})),
                    factory=lambda ref, token: FakeSourceControl(ref, token))


async def test_open_source_alternatives_found_via_github_api(make_ctx):
    ctx = _ctx(make_ctx)
    await run_all(ctx)
    comps = {c["name"]: c for c in ctx.data("competitor_research")["competitors"]}
    oss = comps["openappointments"]
    assert oss["classification"] == "open_source" and oss["verified"]
    assert {"workflow.scheduling", "comm.sms"} <= {f["feature_id"] for f in oss["features"]}
    ev = ctx.ledger.get(oss["features"][0]["evidence_ids"][0])
    assert ev.source_type == "github" and ev.repository_path == "README"
    names = set(comps) | {c["name"] for c in ctx.data("competitor_research")["rejected"]}
    assert "tiny-scheduler" not in names and "forked" not in names   # low-star and fork filtered out


async def test_directory_results_are_name_sources_with_marketplace_evidence(make_ctx):
    llm = ScriptedLLM({"_LLMCandidates": {"candidates": [
        {"name": "ClinicFlow", "url": "https://clinicflow.com", "classification": "direct",
         "source_url": "https://www.g2.com/categories/scheduling"},
        {"name": "G2", "url": "https://www.g2.com/", "classification": "direct"},  # a directory, never a competitor
    ]}})
    ctx = _ctx(make_ctx, llm)
    await run_all(ctx)
    comps = {c["name"]: c for c in ctx.data("competitor_research")["competitors"]}
    assert "G2" not in comps and "ClinicFlow" in comps
    types = {ctx.ledger.get(e).source_type for e in comps["ClinicFlow"]["evidence_ids"]}
    assert "marketplace" in types
