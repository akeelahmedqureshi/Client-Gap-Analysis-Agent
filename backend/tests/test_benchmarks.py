"""Industry-wide benchmarking and predicted trends."""

import copy

from sqlalchemy import select

from cip.db import session as db
from cip.db.models import AgentExecution
from cip.services.runner import runner

from test_api import client, register, upload_and_import  # noqa: F401  (fixture)
from test_security_api import add_user, bearer, login

GATES = ["external_research", "repository_access", "client_report"]


async def _run(c, h, project_id) -> str:
    r = (await c.post("/api/runs", headers=h, json={"project_id": project_id, "approve_gates": GATES})).json()
    await runner.wait(r["run_id"])
    return r["run_id"]


async def test_industry_benchmark_and_client_position(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = await _run(client, h, project_id)

    industries = (await client.get("/api/portfolio/benchmarks", headers=h)).json()
    assert industries and industries[0]["clients"] == 1 and industries[0]["companies"] >= 2
    bench = (await client.get(f"/api/portfolio/benchmarks/{industries[0]['name']}", headers=h)).json()
    assert bench["companies"] == industries[0]["companies"] and bench["clients"] == 1
    caps = bench["capabilities"]
    assert caps and all(0 < c["adoption"] <= 1 and c["band"] in ("standard", "common", "emerging") for c in caps)
    assert [c["adoption"] for c in caps] == sorted((c["adoption"] for c in caps), reverse=True)
    pos = bench["client_positions"][0]
    assert pos["project_id"] == project_id and 0 < pos["percentile"] <= 100
    assert pos["standards_covered"] + len(pos["standards_missing"]) == pos["standards_total"]
    assert bench["predicted_trends"] == [] and bench["trend_note"]

    mine = (await client.get(f"/api/portfolio/runs/{run_id}/benchmark", headers=h)).json()
    assert mine["industry"] == bench["industry"] and mine["capabilities"] == pos["capabilities"]
    assert all(c["band"] in ("standard", "common") for c in mine["missing_common"])
    assert (await client.get("/api/portfolio/benchmarks/unknown-industry", headers=h)).status_code == 404

    # Other organizations see nothing; restricted projects are excluded for non-members.
    other = await register(client, org="Other Co", email="boss@other.example")
    assert (await client.get("/api/portfolio/benchmarks", headers=other)).json() == []
    assert (await client.get(f"/api/portfolio/runs/{run_id}/benchmark", headers=other)).status_code == 404
    await add_user(client, h, "viewer@acme-consulting.com", "viewer")
    await client.put(f"/api/projects/{project_id}/access", headers=h, json={"restricted": True, "member_ids": []})
    viewer = bearer(await login(client, "viewer@acme-consulting.com"))
    assert (await client.get("/api/portfolio/benchmarks", headers=viewer)).json() == []


async def test_rising_capabilities_are_predicted_as_estimates(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    first = await _run(client, h, project_id)
    await _run(client, h, project_id)
    # In the earlier analysis nobody offered online payments; in the later one several do.
    async with db.sessionmaker()() as s:
        rows = (await s.execute(select(AgentExecution).where(AgentExecution.run_id == first, AgentExecution.agent.in_(
            ["competitor_research", "product_features"])))).scalars().all()
        for e in rows:
            result = copy.deepcopy(e.result)
            data = result["data"]
            if e.agent == "competitor_research":
                for c in data.get("landscape", []):
                    c["feature_ids"] = [f for f in c["feature_ids"] if f != "billing.payments"]
                for c in data.get("competitors", []):
                    c["features"] = [o for o in c["features"] if o["feature_id"] != "billing.payments"]
            else:
                data["observations"].pop("billing.payments", None)
            e.result = result
        await s.commit()
    industry = (await client.get("/api/portfolio/benchmarks", headers=h)).json()[0]["name"]
    bench = (await client.get(f"/api/portfolio/benchmarks/{industry}", headers=h)).json()
    rising = {t["feature_id"]: t for t in bench["predicted_trends"]}
    assert "billing.payments" in rising, bench["trend_note"]
    t = rising["billing.payments"]
    assert t["before"] == 0 and t["after"] > 0 and t["projected"] >= t["after"] and t["basis"] == "estimate"
