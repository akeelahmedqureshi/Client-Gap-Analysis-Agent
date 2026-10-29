"""Client intake: validation, duplicates within the upload and against existing clients, and explicit decisions.

Nothing affected is ever imported or changed without a decision; the server re-checks every decision."""

from sqlalchemy import select

from cip.db import session as db
from cip.db.models import AuditLog, Client, Project

from test_api import client, register  # noqa: F401  (fixture)
from test_security_api import add_user, bearer, login

HEADER = "Client Name,Client ID,Client Email,Project Name,Website URL,Industry\n"


async def upload(c, h, rows: str) -> dict:
    r = await c.post("/api/uploads", headers=h, files={"file": ("clients.csv", HEADER + rows, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


async def review(c, h, up: dict, **plan) -> dict:
    r = await c.post(f"/api/uploads/{up['id']}/review", headers=h, json=plan)
    assert r.status_code == 200, r.text
    return r.json()


def rows_of(rv: dict) -> dict[int, dict]:
    return {r["row"]: r for r in rv["rows"]}


async def clients() -> dict[str, Client]:
    async with db.sessionmaker()() as s:
        return {c.name: c for c in (await s.execute(select(Client))).scalars().all()}


async def test_missing_mandatory_information_is_fixed_or_skipped_never_guessed(client):  # noqa: F811
    h = await register(client)
    up = await upload(client, h, "Acme,,ops@acme.com,Portal,https://acme.com,SaaS\n"
                                 "Beta,,,Shop,,Retail\n"
                                 ",,,Gamma app,https://gamma.io,Health\n")
    rv = await review(client, h, up)
    r = rows_of(rv)
    assert r[2]["status"] == "ready" and r[2]["action"] == "add"
    assert r[3]["status"] == "invalid" and r[3]["missing"] == ["project_url"]
    assert len(r[3]["needs"]) == 1 and "Website URL" in r[3]["needs"][0]   # nothing else is asked of it
    assert r[4]["status"] == "invalid" and r[4]["missing"] == ["client_name"]
    assert rv["summary"]["missing_mandatory"] == 2 and not rv["summary"]["can_import"]

    # Without a decision the import is refused; nothing is written.
    refused = await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})
    assert refused.status_code == 409 and refused.json()["detail"]["review"]["summary"]["pending"] == 2
    assert await clients() == {}

    # Fill one in (validated again: a bad value stays invalid), skip the other.
    bad = rows_of(await review(client, h, up, edits={"3": {"project_url": "not a url"}}))
    assert bad[3]["status"] == "invalid" and "project_url" in bad[3]["errors"]
    plan = {"edits": {"3": {"project_url": "https://beta-shop.com"}}, "decisions": {"4": {"action": "skip"}}}
    r = rows_of(await review(client, h, up, **plan))
    assert r[3]["status"] == "ready" and r[3]["edited"] == ["project_url"] and r[4]["status"] == "skipped"
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json=plan)).json()
    assert out["summary"] | {} == {**out["summary"], "added": 2, "skipped": 1, "failed": 0, "pending": 0}
    assert set(await clients()) == {"Acme", "Beta"}
    # The upload remembers what was imported; a second import does nothing more.
    again = rows_of(await review(client, h, up, **plan))
    assert again[2]["status"] == "imported" and again[3]["status"] == "imported"
    assert (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json=plan)).status_code == 422


async def test_duplicates_within_the_upload_need_an_explicit_choice(client):  # noqa: F811
    h = await register(client)
    up = await upload(client, h, "Acme,,,Portal,https://acme.com,SaaS\n"
                                 "ACME ,,,portal,https://www.acme.com/,SaaS\n"     # same website: confirmed
                                 "Delta Ltd,,,App,https://delta.io,Fintech\n"
                                 "Delta,,,App,https://delta-app.com,Banking\n")    # same name: potential
    rv = await review(client, h, up)
    groups = {tuple(g["rows"]): g for g in rv["groups"]}
    assert groups[(2, 3)]["kind"] == "confirmed" and any(x["reason"] == "same website URL" for x in groups[(2, 3)]["links"])
    up2 = await upload(client, h, "Same,,,P,https://same.io,X\nSame,,,P,https://same.io,X\n")
    (g,) = (await review(client, h, up2))["groups"]
    assert [x["reason"] for x in g["links"]] == ["identical rows"]   # no weaker reasons repeated
    assert groups[(4, 5)]["kind"] == "potential"
    assert {x["field"] for x in groups[(4, 5)]["conflicts"]} >= {"project_url", "industry"}
    assert all(r["status"] == "pending" for r in rv["rows"])          # no default for any of them
    assert rv["summary"]["csv_duplicates"] == 4 and rv["summary"]["csv_confirmed_duplicates"] == 2

    # Adding both copies of the same project is refused by the integrity check.
    both = {"decisions": {"2": {"action": "add"}, "3": {"action": "add"}, "4": {"action": "skip"},
                          "5": {"action": "skip"}}}
    r = rows_of(await review(client, h, up, **both))
    assert r[2]["status"] == r[3]["status"] == "conflict" and "same project" in r[2]["needs"][0]

    plan = {"decisions": {"2": {"action": "skip"}, "3": {"action": "add"}, "4": {"action": "add"},
                          "5": {"action": "add"}}}
    rv = await review(client, h, up, **plan)
    assert rv["summary"]["can_import"] and rv["summary"]["selected_add"] == 3
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json=plan)).json()
    assert out["summary"]["added"] == 3 and out["summary"]["new_clients"] == 3


async def test_existing_clients_are_updated_replaced_merged_or_skipped_only_on_request(client):  # noqa: F811
    h = await register(client)
    first = await upload(client, h, "ABC Technologies,,ops@abc.com,Portal,https://abc.com,Technology\n")
    await client.post(f"/api/uploads/{first['id']}/import", headers=h, json={})
    abc = (await clients())["ABC Technologies"]

    up = await upload(client, h, "ABC Technologies Pvt. Ltd.,ABC-1,,Portal,https://www.abc.com,Software Development\n")
    r = rows_of(await review(client, h, up))[2]
    assert r["status"] == "pending" and r["allowed"] == ["update", "replace", "merge", "skip"]   # add would duplicate
    m = r["matches"][0]
    assert m["client_id"] == abc.id and m["kind"] == "confirmed" and "same website URL" in m["reasons"]
    industry = next(f for f in m["fields"] if f["field"] == "industry")
    assert industry == {**industry, "existing": "Technology", "incoming": "Software Development", "conflict": True}

    # Update: incoming values win where given; empty incoming fields keep the existing value (the email).
    upd = rows_of(await review(client, h, up, decisions={"2": {"action": "update"}}))[2]
    assert upd["status"] == "ready"
    assert {c["field"] for c in upd["changes"]} == {"client_name", "external_id", "project_url", "industry"}
    assert upd["result"]["client_email"] == "ops@abc.com"

    # Replace needs an explicit confirmation; it clears what the incoming record leaves empty.
    rep = rows_of(await review(client, h, up, decisions={"2": {"action": "replace"}}))[2]
    assert rep["status"] == "pending" and "Confirm the replacement" in rep["needs"][0]
    rep = rows_of(await review(client, h, up, decisions={"2": {"action": "replace", "confirm_replace": True}}))[2]
    assert rep["status"] == "ready" and "client_email" not in rep["result"]

    # Merge needs a value chosen for every conflicting field.
    mer = rows_of(await review(client, h, up, decisions={"2": {"action": "merge", "merge": {"industry": "existing"}}}))[2]
    assert mer["status"] == "pending" and "Client name" in mer["needs"][0] and "Website URL" in mer["needs"][0]
    choices = {"industry": "existing", "client_name": "existing", "project_url": "incoming"}
    plan = {"decisions": {"2": {"action": "merge", "merge": choices}}}
    mer = rows_of(await review(client, h, up, **plan))[2]
    assert mer["status"] == "ready" and mer["result"]["industry"] == "Technology"
    assert mer["result"]["external_id"] == "ABC-1" and mer["result"]["client_email"] == "ops@abc.com"

    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json=plan)).json()
    assert out["summary"]["merged"] == 1 and out["results"][0]["client_id"] == abc.id   # the id is kept
    after = (await clients())["ABC Technologies"]
    assert after.id == abc.id and after.external_id == "ABC-1" and after.industry == "Technology"
    async with db.sessionmaker()() as s:
        projects = (await s.execute(select(Project))).scalars().all()
        log = (await s.execute(select(AuditLog).where(AuditLog.action == "client.merged"))).scalar_one()
    assert len(projects) == 1 and projects[0].url == "https://www.abc.com"
    assert log.details["before"]["external_id"] is None and log.details["after"]["external_id"] == "ABC-1"

    # Skip leaves the existing record untouched; an identical record is reported as exact.
    again = await upload(client, h, "ABC Technologies,ABC-1,ops@abc.com,Portal,https://www.abc.com,Technology\n")
    r = rows_of(await review(client, h, again))[2]
    assert r["matches"][0]["exact"] and r["status"] == "pending"
    out = (await client.post(f"/api/uploads/{again['id']}/import", headers=h,
                             json={"decisions": {"2": {"action": "skip"}}}))
    assert out.status_code == 422   # nothing to import: the only record is skipped


async def test_a_new_project_of_an_existing_client_and_possible_matches(client):  # noqa: F811
    h = await register(client)
    first = await upload(client, h, "Northwind,,it@northwind.com,Fleet Tracker,https://northwind.com,Logistics\n")
    await client.post(f"/api/uploads/{first['id']}/import", headers=h, json={})
    up = await upload(client, h, "Northwind,,it@northwind.com,Driver App,https://drivers.northwind.com,Logistics\n"
                                 "Northwind Traders Inc,,,Shop,https://nwtraders.com,Retail\n")
    r = rows_of(await review(client, h, up))
    # Same client domain, different project: updating means adding the project to that client.
    assert r[2]["matches"][0]["scope"] == "client" and r[2]["allowed"] == ["update", "replace", "merge", "skip"]
    # Only a similar name: it may be a different company, so adding it as a new client is allowed.
    assert r[3]["matches"][0]["kind"] == "potential" and "similar name" in r[3]["matches"][0]["reasons"]
    assert r[3]["allowed"][0] == "add"
    plan = {"decisions": {"2": {"action": "update"}, "3": {"action": "add"}}}
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json=plan)).json()
    assert out["summary"] == {**out["summary"], "updated": 1, "added": 1, "new_clients": 1}
    northwind = (await clients())["Northwind"]
    async with db.sessionmaker()() as s:
        names = {p.name for p in (await s.execute(select(Project).where(Project.client_id == northwind.id))).scalars()}
    assert names == {"Fleet Tracker", "Driver App"}


async def test_partial_import_needs_explicit_permission(client):  # noqa: F811
    h = await register(client)
    up = await upload(client, h, "Acme,,,Portal,https://acme.com,SaaS\n"
                                 "Beta,,,Shop,,Retail\n")
    assert (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})).status_code == 409
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={"allow_partial": True})).json()
    assert out["summary"]["added"] == 1 and out["summary"]["failed_validation"] == 1 and out["pending_rows"] == [3]
    # The pending row is still there to resolve later.
    r = rows_of(await review(client, h, up, edits={"3": {"project_url": "https://beta.shop"}}))
    assert r[2]["status"] == "imported" and r[3]["status"] == "ready"


async def test_a_failing_record_does_not_stop_or_corrupt_the_others(client, monkeypatch):  # noqa: F811
    from cip.services import intake

    h = await register(client)
    up = await upload(client, h, "Acme,,,Portal,https://acme.com,SaaS\nBeta,,,Shop,https://beta.shop,Retail\n")
    real = intake._apply_row

    async def flaky(session, request, user, row, rec, new_clients, upload):
        out = await real(session, request, user, row, rec, new_clients, upload)
        if row["row"] == 3:
            raise RuntimeError("disk full")
        return out

    monkeypatch.setattr(intake, "_apply_row", flaky)
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=h, json={})).json()
    assert out["summary"]["added"] == 1 and out["failed"] == [{"row": 3, "action": "add", "reason": "RuntimeError: disk full"}]
    assert set(await clients()) == {"Acme"}   # Beta's half-written client was rolled back


async def test_manual_entry_uses_the_same_schema_validation_and_duplicate_rules(client):  # noqa: F811
    h = await register(client)
    schema = (await client.get("/api/intake/schema", headers=h)).json()
    assert schema["required"] == ["client_name", "project_url"]
    assert {f["key"] for f in schema["fields"]} >= {"client_name", "external_id", "client_email", "project_url"}

    bad = (await client.post("/api/intake/review", headers=h,
                             json={"values": {"client_name": "Zeta", "project_url": "zeta", "client_email": "x@"}})).json()
    assert bad["row"]["status"] == "invalid" and set(bad["row"]["errors"]) == {"project_url", "client_email"}
    assert (await client.post("/api/intake/clients", headers=h, json={"values": {"client_name": "Zeta"}})).status_code == 409

    values = {"client_name": "Zeta Labs", "project_url": "zeta-labs.com", "industry": "AI"}
    ok = (await client.post("/api/intake/review", headers=h, json={"values": values})).json()
    assert ok["row"]["status"] == "ready" and ok["row"]["values"]["project_url"] == "https://zeta-labs.com"
    added = (await client.post("/api/intake/clients", headers=h, json={"values": values})).json()
    assert added["summary"]["added"] == 1 and added["created_projects"]
    projects = (await client.get("/api/projects", headers=h)).json()
    assert projects[0]["client_name"] == "Zeta Labs"   # available for analysis

    # The same website again: a decision is required, and the chosen update keeps the client's id.
    again = {"values": {**values, "industry": "Machine learning"}}
    r = (await client.post("/api/intake/review", headers=h, json=again)).json()["row"]
    assert r["status"] == "pending" and r["matches"][0]["kind"] == "confirmed"
    assert (await client.post("/api/intake/clients", headers=h, json=again)).status_code == 409
    upd = (await client.post("/api/intake/clients", headers=h, json={**again, "decision": {"action": "update"}})).json()
    assert upd["summary"]["updated"] == 1 and (await clients())["Zeta Labs"].industry == "Machine learning"


async def test_records_the_person_cannot_see_are_never_shown_or_changed(client):  # noqa: F811
    admin = await register(client)
    up = await upload(client, admin, "Secret Co,,,Vault,https://secret.example,Defense\n")
    out = (await client.post(f"/api/uploads/{up['id']}/import", headers=admin, json={})).json()
    project_id = out["created_projects"][0]
    r = await client.put(f"/api/projects/{project_id}/access", headers=admin, json={"restricted": True, "member_ids": []})
    assert r.status_code == 200
    await add_user(client, admin, "analyst@acme-consulting.com", "analyst")
    h = bearer(await login(client, "analyst@acme-consulting.com"))
    r = (await client.post("/api/intake/review", headers=h,
                           json={"values": {"client_name": "Secret Co", "project_url": "https://secret.example"}})).json()["row"]
    m = r["matches"][0]
    assert not m["accessible"] and m["client_name"] is None and "fields" not in m
    assert r["allowed"] == ["skip"]
    refused = await client.post("/api/intake/clients", headers=h,
                                json={"values": {"client_name": "Secret Co", "project_url": "https://secret.example"},
                                      "decision": {"action": "update", "target": m["client_id"]}})
    assert refused.status_code == 409
