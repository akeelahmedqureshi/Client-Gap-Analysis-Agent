"""Versioned configuration (analysis settings, scoring profiles, taxonomy, prompts/models) and source tiers."""

import copy

from pydantic import BaseModel

from cip.core import prompts
from cip.core.llm import ConfiguredLLM, OpenRouterClient
from cip.core.source_quality import factor, tier
from cip.core.usage import reset_agent, set_agent
from cip.services.runner import runner

from fakes import FakeSearch
from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_security_api import add_user, bearer, login

GATES = ["external_research", "repository_access", "client_report"]


async def _run(c, h, project_id, **extra) -> dict:
    r = await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": GATES, **extra})
    assert r.status_code == 201, r.text
    await runner.wait(r.json()["run_id"])
    return (await c.get(f"/api/runs/{r.json()['run_id']}", headers=h)).json()


async def _agent(c, h, run_id, agent) -> dict:
    return (await c.get(f"/api/runs/{run_id}/agents/{agent}", headers=h)).json()["result"]["data"]


# --------------------------------------------------------------------------- source tiers


def test_source_tiers():
    assert tier("website", "https://acme.com/pricing") == 1
    assert tier("website", "https://acme.com/blog/launch") == 2
    assert tier("website", "https://www.g2.com/products/acme") == 3
    assert tier("app_store", "https://apps.apple.com/app/x") == 3
    assert tier("website", "https://techcrunch.com/2026/acme") == 4
    assert tier("search", "https://duckduckgo.com/?q=acme") == 5
    assert tier("github", "https://github.com/acme/app") == 1
    assert factor(5) == 0.75 and factor(None) == 1.0 and factor(3, {"3": 0.5}) == 0.5


async def test_evidence_carries_source_tier_and_qa_reports_the_mix(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    run = await _run(client, h, project_id)
    evidence = (await client.get(f"/api/runs/{run['run_id']}/evidence", headers=h)).json()
    assert evidence and all(1 <= e["source_tier"] <= 5 for e in evidence)
    qa = await _agent(client, h, run["run_id"], "quality_assurance")
    assert qa["metrics"]["source_tiers"] and abs(sum(qa["metrics"]["source_tiers"].values()) - 1) < 0.01
    recs = (await _agent(client, h, run["run_id"], "opportunity_prioritization"))["recommendations"]
    assert any("source_quality" in r["attributes"] for r in recs)


# --------------------------------------------------------------------------- analysis settings


async def test_analysis_settings_are_versioned_and_snapshotted(client):  # noqa: F811
    h = await register(client)
    await add_user(client, h, "analyst@acme-consulting.com", "analyst")
    analyst = bearer(await login(client, "analyst@acme-consulting.com"))
    await add_user(client, h, "viewer@acme-consulting.com", "viewer")
    viewer = bearer(await login(client, "viewer@acme-consulting.com"))
    assert (await client.get("/api/config/analysis", headers=viewer)).status_code == 403
    view = (await client.get("/api/config/analysis", headers=analyst)).json()
    assert view["version"] == 0 and view["effective"]["max_competitors"] == 10
    assert (await client.put("/api/config/analysis", headers=analyst, json={"data": {}})).status_code == 403

    for bad in ({"max_competitors": 2, "deep_competitors": 3}, {"max_competitors": "five"}, {"bogus": 1},
                {"freshness_fresh_days": 400}, {"source_tier_factors": {"1": 1}},
                {"default_scoring_profile": "Nope"}):
        assert (await client.put("/api/config/analysis", headers=h, json={"data": bad})).status_code == 422, bad
    saved = (await client.put("/api/config/analysis", headers=h,
                              json={"data": {"max_competitors": 4, "deep_competitors": 1}, "note": "lighter"})).json()
    assert saved["version"] == 1 and saved["effective"]["max_competitors"] == 4

    project_id = await upload_and_import(client, h)
    previous = runner.context_hook

    def wide(ctx):
        previous(ctx)
        ctx.search = FakeSearch(landscape=True)

    runner.context_hook = wide
    try:
        run = await _run(client, h, project_id)
        assert run["config"]["analysis"] == 1
        research = await _agent(client, h, run["run_id"], "competitor_research")
        assert len(research["competitors"]) <= 1 and len(research["landscape"]) <= 4

        # A later change does not affect the earlier run or its re-run versions.
        await client.put("/api/config/analysis", headers=h, json={"data": {"max_competitors": 8, "deep_competitors": 3}})
        rerun = (await client.post(f"/api/runs/{run['run_id']}/rerun", headers=h,
                                   json={"stages": ["competitors"]})).json()
        await runner.wait(rerun["run_id"])
        again = await _agent(client, h, rerun["run_id"], "competitor_research")
        assert (await client.get(f"/api/runs/{rerun['run_id']}", headers=h)).json()["config"]["analysis"] == 1
        assert len(again["competitors"]) <= 1
    finally:
        runner.context_hook = previous
    qa = await _agent(client, h, run["run_id"], "quality_assurance")
    assert qa["reproducibility"]["config_versions"]["analysis"] == 1
    used = (await client.get(f"/api/config/runs/{run['run_id']}", headers=h)).json()
    assert used["analysis"]["max_competitors"] == 4

    hist = (await client.get("/api/config/analysis", headers=h)).json()["history"]
    assert [v["version"] for v in hist] == [2, 1] and hist[1]["note"] == "lighter"
    reverted = (await client.post("/api/config/analysis/revert", headers=h, json={"version": 1})).json()
    assert reverted["version"] == 3 and reverted["effective"]["max_competitors"] == 4
    assert (await client.get("/api/config/analysis/versions/2", headers=h)).json()["data"]["max_competitors"] == 8
    assert (await client.get("/api/config/nope", headers=h)).status_code == 404


# --------------------------------------------------------------------------- scoring profiles


async def test_scoring_profiles(client):  # noqa: F811
    h = await register(client)
    bad = await client.put("/api/config/scoring_profile?key=Growth", headers=h,
                           json={"data": {"medium_min_normalized": 0.7, "high_min_normalized": 0.6}})
    assert bad.status_code == 422
    assert (await client.put("/api/config/scoring_profile?key=Growth", headers=h,
                             json={"data": {"weights": {"magic": 1}}})).status_code == 422
    saved = await client.put("/api/config/scoring_profile?key=Growth", headers=h,
                             json={"data": {"weights": {"revenue_potential": 3.0}}, "note": "revenue first"})
    assert saved.status_code == 200 and saved.json()["effective"]["weights"]["revenue_potential"] == 3.0
    overview = (await client.get("/api/config", headers=h)).json()
    assert {p["name"] for p in overview["scoring_profiles"]} == {"Standard", "Growth"}

    project_id = await upload_and_import(client, h)
    assert (await client.post("/api/runs", headers=h, json={"project_id": project_id, "scoring_profile": "Nope"})
            ).status_code == 422
    run = await _run(client, h, project_id, scoring_profile="Growth")
    assert run["config"]["scoring_profile"] == {"name": "Growth", "version": 1}
    prio = await _agent(client, h, run["run_id"], "opportunity_prioritization")
    assert prio["scoring_weights"]["revenue_potential"] == 3.0

    # Make it the default: new runs and the start dialog's defaults use it.
    await client.put("/api/config/analysis", headers=h, json={"data": {"default_scoring_profile": "Growth"}})
    meta = (await client.get("/api/meta/scoring", headers=h)).json()
    assert meta["profile"] == "Growth" and meta["weights"]["revenue_potential"] == 3.0
    run2 = await _run(client, h, project_id)
    assert run2["config"]["scoring_profile"]["name"] == "Growth"


# --------------------------------------------------------------------------- taxonomy


async def test_taxonomy_editing(client):  # noqa: F811
    h = await register(client)
    view = (await client.get("/api/config/taxonomy", headers=h)).json()
    tax = copy.deepcopy(view["effective"])
    comm = next(c for c in tax["categories"] if c["id"] == "communication")
    comm["features"].append({"id": "communication.whatsapp", "name": "WhatsApp messaging",
                             "keywords": ["whatsapp", "sms reminders"], "code_signals": [], "defaults": {"business_value": 3}})
    dup = copy.deepcopy(tax)
    dup["categories"][0]["features"].append(dict(dup["categories"][0]["features"][0]))
    assert (await client.put("/api/config/taxonomy", headers=h, json={"data": dup})).status_code == 422
    broken = copy.deepcopy(tax)
    broken["categories"][0]["features"][0]["defaults"] = {"business_value": 9}
    assert (await client.put("/api/config/taxonomy", headers=h, json={"data": broken})).status_code == 422
    saved = await client.put("/api/config/taxonomy", headers=h, json={"data": tax, "note": "add WhatsApp"})
    assert saved.status_code == 200 and saved.json()["version"] == 1
    names = {f["id"] for f in (await client.get("/api/meta/taxonomy", headers=h)).json()}
    assert "communication.whatsapp" in names

    project_id = await upload_and_import(client, h)
    run = await _run(client, h, project_id)
    rows = (await _agent(client, h, run["run_id"], "feature_comparison"))["rows"]
    assert any(r["feature_id"] == "communication.whatsapp" for r in rows)
    # A reviewer can set the status of a capability that only exists in this run's taxonomy.
    r = await client.put(f"/api/runs/{run['run_id']}/review", headers=h, json={
        "kind": "capability_status", "target_id": "communication.whatsapp", "value": "missing", "note": "confirmed"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- prompts and models


class _Out(BaseModel):
    ok: bool = True


class _Capture(OpenRouterClient):
    def __init__(self):
        self.calls = []

    async def complete_json(self, system, user, schema, *, model=None, temperature=None):
        self.calls.append((system, model, temperature))
        return schema()


async def test_configured_llm_applies_prompt_and_model_overrides():
    inner = _Capture()
    default = prompts.default_text("reporting.summary")
    llm = ConfiguredLLM(inner, prompts={default: "Write a terse summary."}, default_model="org/model-a",
                        temperature=0.1, agents={"report": {"model": "org/model-b", "temperature": 0.0}})
    token = set_agent("report")
    try:
        await llm.complete_json(default, "x", _Out)
    finally:
        reset_agent(token)
    await llm.complete_json("other prompt", "x", _Out)
    assert inner.calls == [("Write a terse summary.", "org/model-b", 0.0), ("other prompt", "org/model-a", 0.1)]


async def test_llm_configuration_is_validated_and_recorded(client):  # noqa: F811
    h = await register(client)
    view = (await client.get("/api/config/llm", headers=h)).json()
    assert {p["id"] for p in view["prompts"]} == set(prompts.PROMPTS) and not any(p["overridden"] for p in view["prompts"])
    for bad in ({"prompts": {"nope": "x"}}, {"prompts": {"reporting.summary": ""}},
                {"prompts": {"reporting.summary": "Use key sk" + "_live_" + "abcdefghijklmnopqrstuvwx"}},
                {"agents": {"ghost": {"model": "a/b"}}}, {"default_model": "bad model!"}, {"temperature": 3}):
        assert (await client.put("/api/config/llm", headers=h, json={"data": bad})).status_code == 422, bad
    saved = (await client.put("/api/config/llm", headers=h, json={"data": {
        "default_model": "org/model-a", "agents": {"report": {"model": "org/model-b"}},
        "prompts": {"reporting.summary": "Write a terse executive summary from the findings only."}}})).json()
    summary = next(p for p in saved["prompts"] if p["id"] == "reporting.summary")
    assert summary["overridden"] and summary["version"] != summary["default_version"]

    project_id = await upload_and_import(client, h)
    seen = {}
    previous = runner.context_hook

    def capture(ctx):
        previous(ctx)
        seen["llm"] = ctx.llm

    runner.context_hook = capture
    try:
        run = await _run(client, h, project_id)
    finally:
        runner.context_hook = previous
    assert isinstance(seen["llm"], ConfiguredLLM) and seen["llm"].settings_for("report") == ("org/model-b", None)
    repro = (await _agent(client, h, run["run_id"], "quality_assurance"))["reproducibility"]
    assert repro["config_versions"]["llm"] == 1
    assert repro["prompt_version"] == prompts.fingerprint({"reporting.summary": summary["text"]})
    assert repro["prompt_version"] != prompts.fingerprint()
