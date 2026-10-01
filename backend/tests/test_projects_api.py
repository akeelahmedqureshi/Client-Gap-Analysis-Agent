"""Client detail page data, repository picker and project repository editing."""

from test_api import client, register, upload_and_import  # noqa: F401  (fixture re-export)
from test_security_api import add_user, bearer, login

from cip.connectors.source_control.base import RepoMetadata, RepoRef
from cip.services.runner import runner

ALL_GATES = ["external_research", "repository_access", "client_report"]


async def _run(client, h, project_id):  # noqa: F811
    run_id = (await client.post("/api/runs", headers=h, json={"project_id": project_id,
                                                              "approve_gates": ALL_GATES})).json()["run_id"]
    await runner.wait(run_id)
    return run_id


async def test_client_detail_aggregates_latest_research(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    run_id = await _run(client, h, project_id)
    client_id = (await client.get(f"/api/projects/{project_id}", headers=h)).json()["client_id"]

    d = (await client.get(f"/api/clients/{client_id}", headers=h)).json()
    assert d["client"]["name"] == "ABC Healthcare" and d["client"]["project_count"] == 1
    assert d["projects"][0]["latest_run"]["status"] == "completed"
    assert d["profile"]["domain"] == "abc-healthcare.com" and d["profile_run_id"] == run_id
    assert d["profile"]["contacts"] and any(p["name"] == "Patient Scheduler" for p in d["profile"]["products"])
    recs = d["recommendations"]
    assert 1 <= len(recs) <= 3 and recs[0]["project"] == "ABC Patient Management"
    assert recs[0]["score"] >= recs[-1]["score"]

    other = await register(client, org="Other Co", email="boss@other-co.com")
    assert (await client.get(f"/api/clients/{client_id}", headers=other)).status_code == 404

    # Restricting the only project hides the client from non-members.
    await add_user(client, h, "out@acme-consulting.com", role="viewer")
    await client.put(f"/api/projects/{project_id}/access", headers=h, json={"restricted": True})
    ho = bearer(await login(client, "out@acme-consulting.com"))
    assert (await client.get(f"/api/clients/{client_id}", headers=ho)).status_code == 404


async def test_edit_project_repositories(client):  # noqa: F811
    h = await register(client)
    project_id = await upload_and_import(client, h)
    r = await client.put(f"/api/projects/{project_id}/repositories", headers=h, json={"urls": [
        "https://github.com/abc/project", "git@github.com:abc/mobile-app.git",
        "https://gitlab.com/abc/infra/-/tree/main", "https://github.com/abc/project/"]})
    assert r.status_code == 200, r.text
    src = r.json()["record"]["sources"]
    assert src["github"] == ["https://github.com/abc/project", "https://github.com/abc/mobile-app"]
    assert src["gitlab"] == ["https://gitlab.com/abc/infra"]

    # Future runs use the new list (approval preview reflects it).
    preview = (await client.get(f"/api/runs/approval-preview?project_id={project_id}", headers=h)).json()
    target = next(p for p in preview if p["gate"] == "repository_access")["target"]
    assert "abc/mobile-app" in target and "gitlab.com/abc/infra" in target

    bad = await client.put(f"/api/projects/{project_id}/repositories", headers=h,
                           json={"urls": ["https://example.com/not/a/repo"]})
    assert bad.status_code == 422

    await add_user(client, h, "view@acme-consulting.com", role="viewer")
    hv = bearer(await login(client, "view@acme-consulting.com"))
    assert (await client.put(f"/api/projects/{project_id}/repositories", headers=hv,
                             json={"urls": []})).status_code == 403
    audit = (await client.get("/api/audit?action=project.repositories", headers=h)).json()
    assert audit[0]["details"]["after"][1].endswith("abc/mobile-app")


class _FakeProvider:
    def __init__(self, token):
        self.token = token

    async def list_repositories(self, limit=100):
        return [RepoMetadata(ref=RepoRef("github", "github.com", n), default_branch="main", private=p,
                             description=f"{n} repo") for n, p in
                (("abc/project", True), ("abc/website", False), ("abc/mobile-app", True))]


async def test_list_repositories_for_connection(client, monkeypatch):  # noqa: F811
    seen_tokens = []

    def fake_provider_for(ref, token):
        seen_tokens.append(token)
        return _FakeProvider(token)

    monkeypatch.setattr("cip.api.routes.connections.provider_for", fake_provider_for)
    h = await register(client)
    pat = "ghp_" + "r" * 36
    con = (await client.post("/api/connections/token", headers=h,
                             json={"provider": "github", "token": pat})).json()
    r = await client.get(f"/api/connections/{con['id']}/repositories", headers=h)
    assert r.status_code == 200 and [x["full_name"] for x in r.json()] == ["abc/project", "abc/website",
                                                                            "abc/mobile-app"]
    assert pat not in r.text and seen_tokens == [pat]  # token used server-side only
    r = await client.get(f"/api/connections/{con['id']}/repositories?q=MOBILE", headers=h)
    assert [x["url"] for x in r.json()] == ["https://github.com/abc/mobile-app"]

    other = await register(client, org="Other Co", email="boss@other-co.com")
    assert (await client.get(f"/api/connections/{con['id']}/repositories", headers=other)).status_code == 404
