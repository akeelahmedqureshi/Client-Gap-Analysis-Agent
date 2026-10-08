"""LLM token / cost and web-request tracking with budgets (BRS 16, 20.5, PRD 10.46)."""

import httpx
import pytest
from pydantic import BaseModel

from cip.config import Settings
from cip.core import usage
from cip.core.llm import LLMUnavailable, OpenRouterClient
from cip.core.usage import UsageMeter
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_pipeline import run_all


class Out(BaseModel):
    name: str


def _client(cost: float | None = 0.002) -> OpenRouterClient:
    def handler(req: httpx.Request) -> httpx.Response:
        u = {"prompt_tokens": 100, "completion_tokens": 20}
        if cost is not None:
            u["cost"] = cost
        return httpx.Response(200, json={"model": "openai/gpt-6-luna", "usage": u,
                                         "choices": [{"message": {"content": '{"name": "x"}'}}]})

    return OpenRouterClient(Settings(openrouter_api_key="k", llm_max_retries=0), transport=httpx.MockTransport(handler))


async def test_llm_tokens_cost_and_model_are_recorded_per_agent():
    meter = UsageMeter(price_in_per_million=1.0, price_out_per_million=4.0)
    token, agent = usage.activate(meter), usage.set_agent("client_research")
    try:
        await _client().complete_json("s", "u", Out)
        await _client(cost=None).complete_json("s", "u", Out)  # no reported cost: configured prices apply
    finally:
        usage.reset_agent(agent)
        usage.deactivate(token)
    t = meter.totals
    assert t["llm_calls"] == 2 and t["prompt_tokens"] == 200 and t["total_tokens"] == 240
    assert t["cost_usd"] == pytest.approx(0.002 + (100 * 1.0 + 20 * 4.0) / 1e6)
    assert t["models"] == {"openai/gpt-6-luna": 240}
    assert meter.agent_usage("client_research")["llm_calls"] == 2


async def test_token_budgets_make_the_llm_unavailable_so_agents_fall_back():
    meter = UsageMeter(run_token_budget=150)
    token = usage.activate(meter)
    try:
        await _client().complete_json("s", "u", Out)  # 120 tokens: within budget
        await _client().complete_json("s", "u", Out)  # starts below 150, ends at 240
        with pytest.raises(LLMUnavailable, match="budget"):
            await _client().complete_json("s", "u", Out)
    finally:
        usage.deactivate(token)
    assert meter.events == ["Run LLM token budget (150) exhausted"]

    meter = UsageMeter(agent_token_budget=100)
    token = usage.activate(meter)
    try:
        a = usage.set_agent("one")
        await _client().complete_json("s", "u", Out)
        with pytest.raises(LLMUnavailable):
            await _client().complete_json("s", "u", Out)
        usage.reset_agent(a)
        b = usage.set_agent("two")
        await _client().complete_json("s", "u", Out)  # another agent has its own budget
        usage.reset_agent(b)
    finally:
        usage.deactivate(token)


async def test_web_requests_and_searches_are_metered_per_agent(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    t = ctx.usage.totals
    assert t["web_requests"] > 10 and t["search_queries"] > 0
    assert ctx.outputs["client_research"].usage["web_requests"] > 0
    assert ctx.outputs["competitor_research"].usage["search_queries"] > 0
    assert ctx.outputs["gap_analysis"].usage is None, "agents without external calls record nothing"
    assert ctx.data("quality_assurance")["metrics"]["usage"]["web_requests"] == t["web_requests"]


async def test_web_budget_degrades_gracefully(make_ctx):
    ctx = make_ctx()
    ctx.usage.web_request_budget = 8
    status, _, _ = await run_all(ctx)
    assert status == "completed", "a budget never crashes the run"
    assert ctx.usage.totals["web_requests"] + ctx.usage.totals["search_queries"] <= 8
    assert any("web request budget" in e for e in ctx.usage.events)
    q = ctx.data("quality_assurance")
    assert any(i["area"] == "budget" for i in q["issues"])


async def test_run_api_reports_usage(client):  # noqa: F811
    h = await register(client)
    pid = await upload_and_import(client, h)
    run_id = (await client.post("/api/runs", headers=h, json={
        "project_id": pid, "approve_gates": ["external_research", "repository_access", "client_report"]})).json()["run_id"]
    await runner.wait(run_id)
    run = (await client.get(f"/api/runs/{run_id}", headers=h)).json()
    assert run["usage"]["web_requests"] > 0 and run["usage"]["llm_calls"] == 0
    research = next(d for d in run["agent_details"] if d["agent"] == "client_research")
    assert research["usage"]["web_requests"] > 0
