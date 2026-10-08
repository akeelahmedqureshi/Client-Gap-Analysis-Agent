"""End-to-end orchestration with offline fakes."""

import json

import pytest

from cip.agents.base import Agent
from cip.agents.orchestrator import Orchestrator
from cip.core.schemas import AgentResult, AgentStatus

from fakes import FAKE_STRIPE_KEY, FakeSourceControl, ScriptedLLM


class MemStore:
    def __init__(self):
        self.run_status = []
        self.agents: dict[str, list[str]] = {}
        self.approvals = []
        self.evidence = {}

    async def set_run_status(self, run_id, status, error=None):
        self.run_status.append(status)

    async def set_agent_status(self, run_id, agent, status, result=None, error=None, attempts=None):
        self.agents.setdefault(agent, []).append(status.value)

    async def save_evidence(self, run_id, evidence):
        self.evidence.update({e.id: e for e in evidence})

    async def request_approval(self, run_id, agent, request):
        self.approvals.append(request)


async def run_all(ctx, store=None):
    store = store or MemStore()
    statuses = {}
    status = await Orchestrator(store, retry_delay=0).run(ctx, statuses)
    return status, statuses, store


async def test_full_pipeline_deterministic(make_ctx):
    ctx = make_ctx()
    status, statuses, store = await run_all(ctx)
    assert status == "completed"
    assert all(s == AgentStatus.COMPLETED for s in statuses.values()), statuses

    # Every evidence id referenced anywhere resolves to collected evidence.
    for name, result in ctx.outputs.items():
        for f in result.findings:
            for eid in f.evidence_ids:
                assert eid in ctx.ledger, (name, f.title)

    # Code intelligence
    prof = ctx.data("code_analysis")["profiles"][0]
    techs = {t["name"] for t in prof["technologies"]}
    assert {"React", "Express", "PostgreSQL", "Stripe", "Jest", "Docker"} <= techs
    assert "MVC" in prof["architecture"]
    assert ".env" in prof["skipped_sensitive_files"]

    # Client features from code + website + CSV
    obs = ctx.data("product_features")["observations"]
    assert obs["billing.payments"]["status"] == "available"
    assert obs["workflow.scheduling"]["status"] == "available"
    assert obs["ai.assistant"]["status"] == "unknown"  # not publicly identified, never "missing"

    # Competitors verified; unrelated search results rejected; directories never considered.
    comps = {c["name"]: c for c in ctx.data("competitor_research")["competitors"]}
    assert set(comps) == {"MediBook", "ClinicFlow"}
    assert all(c["verified"] for c in comps.values())
    rejected = [c["url"] for c in ctx.data("competitor_research")["rejected"]]
    assert any("randomnews" in u for u in rejected)
    assert not any("g2.com" in u for u in rejected + [c["url"] for c in comps.values()])

    # Gaps & prioritization
    gaps = {g["name"]: g for g in ctx.data("gap_analysis")["gaps"]}
    assert gaps["AI assistant / chatbot"]["gap_type"] == "ai"
    # The client's blog announces SMS reminders, so SMS is only a partial gap; video calls are truly missing.
    assert gaps["SMS notifications"]["gap_type"] == "partial"
    assert gaps["Video calls"]["gap_type"] == "missing"
    assert "Native mobile app" in gaps and gaps["Native mobile app"]["gap_type"] == "ux"
    assert "CI/CD pipeline" in gaps and "Secrets management" in gaps
    recs = ctx.data("opportunity_prioritization")["recommendations"]
    totals = [r["score"]["total"] for r in recs]
    assert len(recs) == ctx.settings.roadmap_top_n
    assert all(r["basis"] == "estimate" for r in recs)
    plans = ctx.data("enhancement_planning")["plans"]
    assert len(plans) == len(recs) and plans[0]["backend_changes"]

    # Report
    md = ctx.data("report")["markdown"]
    # The 15 report sections of BRS 7.23, in order, then the appendices.
    from cip.agents.reporting import CONTENTS
    positions = [md.index(f"## {i}. {title}") for i, title in enumerate(CONTENTS, 1)]
    assert positions == sorted(positions) and len(CONTENTS) == 15
    for heading in ("Appendix A. Technical Patch Plan", "Appendix B. Analysis Quality", "Appendix C. Evidence Appendix"):
        assert heading in md
    assert "Analysis status: Complete" in md and "<details>" not in md
    appendix = ctx.data("report")["report"]["sections"]["evidence_appendix"]
    assert appendix and all(e["source_url"] for e in appendix)
    assert totals  # noqa


async def test_secrets_never_leave_the_repository_agent(make_ctx):
    fetched = []

    def factory(ref, token):
        sc = FakeSourceControl(ref, token)
        fetched.append(sc)
        return sc

    ctx = make_ctx(factory=factory)
    await run_all(ctx)
    assert ".env" not in fetched[0].fetched
    blob = json.dumps({k: v.model_dump(mode="json") for k, v in ctx.outputs.items()})
    assert FAKE_STRIPE_KEY not in blob
    assert "postgres://user:secret" not in blob


async def test_personal_emails_are_not_collected(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    contacts = ctx.data("client_research")["profile"]["contacts"]
    values = {c["value"] for c in contacts}
    assert "support@abc-healthcare.com" in values
    assert "jane.porter@abc-healthcare.com" not in values


async def test_approval_gates_pause_and_resume(make_ctx):
    ctx = make_ctx(approvals=())
    store = MemStore()
    statuses = {}
    orch = Orchestrator(store, retry_delay=0)
    assert await orch.run(ctx, statuses) == "awaiting_approval"
    gates = {r.gate for r in store.approvals}
    assert gates == {"external_research", "repository_access"}
    assert statuses["csv_intake"] == AgentStatus.COMPLETED
    assert statuses["client_research"] == AgentStatus.AWAITING_APPROVAL
    req = next(r for r in store.approvals if r.gate == "repository_access")
    assert "github.com/abc/project" in req.target and req.what and req.why and req.data_analyzed

    ctx.approvals |= {"external_research", "repository_access"}
    assert await orch.run(ctx, statuses) == "awaiting_approval"  # now waits for the client report gate
    assert statuses["enhancement_planning"] == AgentStatus.COMPLETED
    assert statuses["report"] == AgentStatus.AWAITING_APPROVAL

    completed_before = dict(ctx.outputs)
    ctx.approvals.add("client_report")
    assert await orch.run(ctx, statuses) == "completed"
    # Resuming never re-runs completed agents.
    for name, result in completed_before.items():
        assert ctx.outputs[name] is result


async def test_large_repository_needs_runtime_approval(make_ctx):
    ctx = make_ctx()
    ctx.settings = ctx.settings.model_copy(update={"large_repo_file_threshold": 3})
    status, statuses, store = await run_all(ctx)
    assert status == "awaiting_approval"
    assert statuses["repository"] == AgentStatus.AWAITING_APPROVAL
    assert store.approvals[0].gate == "large_repository_scan:abc/project"
    assert statuses["client_research"] == AgentStatus.COMPLETED  # independent work continued


async def test_private_repo_without_connection_degrades_gracefully(make_ctx):
    ctx = make_ctx(factory=lambda ref, token: FakeSourceControl(ref, token, private=True))
    status, statuses, _ = await run_all(ctx)
    assert status == "completed"
    repo = ctx.data("repository")["repositories"][0]
    assert repo["accessible"] is False and "connect Github" in repo["reason"]
    assert ctx.outputs["code_analysis"].status == AgentStatus.SKIPPED

    async def token(provider, host):
        return "decrypted-token"

    ctx = make_ctx(factory=lambda ref, t: FakeSourceControl(ref, t, private=True), token_resolver=token)
    await run_all(ctx)
    assert ctx.data("repository")["repositories"][0]["accessible"] is True


class Boom(Agent):
    name = "boom"
    max_attempts = 2

    def __init__(self):
        self.calls = 0

    async def run(self, ctx):
        self.calls += 1
        raise RuntimeError("kaput")


class NeedsBoom(Agent):
    name = "needs_boom"
    requires = ("boom",)

    async def run(self, ctx):
        return AgentResult()


class AfterBoom(Agent):
    name = "after_boom"
    after = ("boom",)

    async def run(self, ctx):
        return AgentResult(data={"ran": True})


async def test_failures_retry_then_skip_hard_dependents(make_ctx):
    boom = Boom()
    store = MemStore()
    statuses = {}
    status = await Orchestrator(store, [boom, NeedsBoom(), AfterBoom()], retry_delay=0).run(make_ctx(), statuses)
    assert status == "completed_with_errors"
    assert boom.calls == 2
    assert statuses == {"boom": AgentStatus.FAILED, "needs_boom": AgentStatus.SKIPPED,
                        "after_boom": AgentStatus.COMPLETED}


def test_graph_validation():
    class Cyclic(Agent):
        name = "a"
        after = ("a",)

        async def run(self, ctx):
            return AgentResult()

    with pytest.raises(ValueError):
        Orchestrator(MemStore(), [Cyclic()])


async def test_llm_extractions_are_grounded(make_ctx):
    llm = ScriptedLLM({
        "_LLMCompany": {
            "industry": {"value": "Healthcare IT", "source_url": "https://abc-healthcare.com/",
                         "quote": "patient scheduling software for clinics"},
            "founded_year": {"value": "2012", "source_url": "https://abc-healthcare.com/",
                             "quote": "Founded in 2012 in Austin, Texas"},
            "products": [
                {"name": "Patient Scheduler", "kind": "saas", "source_url":
                    "https://abc-healthcare.com/products/patient-scheduler", "quote": "Online booking for clinics"},
                {"name": "Hallucinated Pharmacy App", "kind": "mobile_app",
                 "source_url": "https://abc-healthcare.com/pharmacy", "quote": "pharmacy"},
            ],
        },
        "_LLMOpps": lambda user: {"opportunities": []},
    })
    ctx = make_ctx(llm=llm)
    await run_all(ctx)
    profile = ctx.data("client_research")["profile"]
    assert profile["founded_year"] == 2012
    names = {p["name"] for p in profile["products"]}
    assert "Patient Scheduler" in names and "Hallucinated Pharmacy App" not in names
    ev = next(ctx.ledger.get(e) for e in profile["evidence_ids"] if "founded" in ctx.ledger.get(e).claim)
    assert ev.confidence >= 0.8 and ev.source_url.rstrip("/") == "https://abc-healthcare.com"


async def test_emerging_ai_gaps_when_client_has_no_ai(make_ctx):
    from cip.connectors.research.search import NullSearchProvider

    ctx = make_ctx(search=NullSearchProvider())  # no competitors => AI gaps are emerging estimates
    await run_all(ctx)
    ai_gaps = [g for g in ctx.data("gap_analysis")["gaps"] if g["gap_type"] == "ai"]
    assert len(ai_gaps) == 3 and all(g["basis"] == "estimate" for g in ai_gaps)


async def test_rejected_external_research_means_no_search_and_no_competitor_sites(make_ctx):
    """The external-research gate covers web search, GitHub search and competitor websites."""
    import httpx

    from cip.connectors.research.web import WebFetcher
    from fakes import FakeSearch, web_transport

    fetched: list[str] = []
    transport = web_transport()
    orig = transport.handle_async_request

    async def spy(request: httpx.Request):
        fetched.append(str(request.url))
        return await orig(request)

    transport.handle_async_request = spy
    search = FakeSearch()
    ctx = make_ctx(approvals=("repository_access", "client_report"), search=search,
                   fetcher=WebFetcher(transport=transport))
    # As the runner does after a rejection: the gated agent is marked skipped and the run continues.
    statuses = {"client_research": AgentStatus.SKIPPED}
    status = await Orchestrator(MemStore(), retry_delay=0).run(ctx, statuses)
    assert status == "completed"
    assert statuses["competitor_research"] == AgentStatus.SKIPPED
    assert search.queries == []
    hosts = {httpx.URL(u).host for u in fetched}
    assert not hosts & {"medibook.io", "clinicflow.com", "abc-healthcare.com", "api.github.com"}, hosts


def test_features_without_evidence_are_not_publicly_identified_never_missing():
    """BRS 5.3 / 7.3: a lack of public evidence is never recorded as a confirmed absence."""
    from cip.agents.product_features import fill_missing
    from cip.core.schemas import FeatureStatus
    from cip.core.taxonomy import load_taxonomy

    taxonomy = load_taxonomy()
    for coverage in ({"website": True, "docs": True, "code": True}, {"code": True}, {"website": True}, {}):
        obs: dict = {}
        fill_missing(obs, taxonomy, coverage)
        assert {o.status for o in obs.values()} == {FeatureStatus.UNKNOWN}
    well, thin = {}, {}
    fill_missing(well, taxonomy, {"website": True, "docs": True})
    fill_missing(thin, taxonomy, {"website": True})
    fid = taxonomy.features[0].id
    assert well[fid].confidence > thin[fid].confidence
    assert "website, docs" in well[fid].notes
