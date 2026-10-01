"""Login protection, session revocation, user management, project-level access and audit log."""

from test_api import client, register, upload_and_import  # noqa: F401  (fixture re-export)

from cip.services.runner import runner

PW = "viewer-password-1"


async def add_user(c, h, email, role="analyst", password=PW):
    r = await c.post("/api/auth/users", headers=h, json={"email": email, "password": password, "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def login(c, email, password=PW):
    return await c.post("/api/auth/login", json={"email": email, "password": password})


def bearer(r):
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_account_locks_after_repeated_failures_and_admin_can_unlock(client):  # noqa: F811
    admin = await register(client)
    uid = await add_user(client, admin, "ana@acme-consulting.com")
    for _ in range(5):
        assert (await login(client, "ana@acme-consulting.com", "wrong-password")).status_code == 401
    r = await login(client, "ana@acme-consulting.com")  # correct password, but locked
    assert r.status_code == 429 and "locked" in r.json()["detail"]
    users = (await client.get("/api/users", headers=admin)).json()
    assert next(u for u in users if u["id"] == uid)["locked"] is True
    assert (await client.post(f"/api/users/{uid}/unlock", headers=admin)).status_code == 200
    assert (await login(client, "ana@acme-consulting.com")).status_code == 200


async def test_ip_rate_limit(client):  # noqa: F811
    codes = [(await login(client, f"nobody{i}@acme-consulting.com", "x" * 12)).status_code for i in range(25)]
    assert codes[0] == 401 and codes[-1] == 429


async def test_password_change_revokes_other_sessions(client):  # noqa: F811
    admin = await register(client)
    await add_user(client, admin, "bo@acme-consulting.com")
    old = bearer(await login(client, "bo@acme-consulting.com"))
    r = await client.post("/api/auth/change-password", headers=old,
                          json={"current_password": PW, "new_password": "brand-new-password-2"})
    assert r.status_code == 200
    assert (await client.get("/api/auth/me", headers=old)).status_code == 401
    assert (await client.get("/api/auth/me", headers=bearer(r))).status_code == 200
    r = await client.post("/api/auth/change-password", headers=bearer(r),
                          json={"current_password": "wrong", "new_password": "another-password-3"})
    assert r.status_code == 400


async def test_admin_user_management(client):  # noqa: F811
    admin = await register(client)
    me = (await client.get("/api/auth/me", headers=admin)).json()
    uid = await add_user(client, admin, "cy@acme-consulting.com", role="viewer")
    session = bearer(await login(client, "cy@acme-consulting.com"))

    # Role change applies immediately: old token revoked.
    r = await client.patch(f"/api/users/{uid}", headers=admin, json={"role": "analyst"})
    assert r.json()["role"] == "analyst"
    assert (await client.get("/api/auth/me", headers=session)).status_code == 401

    # Admin password reset revokes sessions; new password works.
    session = bearer(await login(client, "cy@acme-consulting.com"))
    await client.post(f"/api/users/{uid}/reset-password", headers=admin, json={"new_password": "temporary-pass-9"})
    assert (await client.get("/api/auth/me", headers=session)).status_code == 401
    assert (await login(client, "cy@acme-consulting.com", "temporary-pass-9")).status_code == 200

    # Deactivated users can't sign in.
    await client.patch(f"/api/users/{uid}", headers=admin, json={"is_active": False})
    assert (await login(client, "cy@acme-consulting.com", "temporary-pass-9")).status_code == 401

    # The last active admin can't be demoted or deactivated.
    assert (await client.patch(f"/api/users/{me['id']}", headers=admin, json={"role": "viewer"})).status_code == 409
    assert (await client.patch(f"/api/users/{me['id']}", headers=admin, json={"is_active": False})).status_code == 409

    # Other organizations' admins can't see or modify these users.
    other = await register(client, org="Other Co", email="boss@other-co.com")
    assert (await client.patch(f"/api/users/{uid}", headers=other, json={"role": "admin"})).status_code == 404
    assert all(u["email"] != "cy@acme-consulting.com" for u in (await client.get("/api/users", headers=other)).json())


async def test_restricted_projects_are_hidden_from_non_members(client):  # noqa: F811
    admin = await register(client)
    project_id = await upload_and_import(client, admin)
    run_id = (await client.post("/api/runs", headers=admin, json={"project_id": project_id})).json()["run_id"]
    await runner.wait(run_id)
    member = await add_user(client, admin, "member@acme-consulting.com", role="viewer")
    await add_user(client, admin, "outsider@acme-consulting.com", role="analyst")
    hm = bearer(await login(client, "member@acme-consulting.com"))
    ho = bearer(await login(client, "outsider@acme-consulting.com"))

    # Default: open to the whole organization.
    assert len((await client.get("/api/projects", headers=ho)).json()) == 1

    r = await client.put(f"/api/projects/{project_id}/access", headers=admin,
                         json={"restricted": True, "member_ids": [member]})
    assert r.status_code == 200 and r.json() == {"restricted": True, "member_ids": [member]}

    # Outsider: project, its client, runs, evidence and report all disappear (404, not 403).
    assert (await client.get("/api/projects", headers=ho)).json() == []
    assert (await client.get("/api/clients", headers=ho)).json() == []
    assert (await client.get("/api/runs", headers=ho)).json() == []
    for path in (f"/api/projects/{project_id}", f"/api/runs/{run_id}", f"/api/runs/{run_id}/evidence",
                 f"/api/runs/approval-preview?project_id={project_id}"):
        assert (await client.get(path, headers=ho)).status_code == 404, path
    assert (await client.post("/api/runs", headers=ho, json={"project_id": project_id})).status_code == 404

    # Member and admin still see it.
    assert len((await client.get("/api/projects", headers=hm)).json()) == 1
    assert (await client.get(f"/api/runs/{run_id}", headers=hm)).status_code == 200
    assert (await client.get(f"/api/projects/{project_id}", headers=admin)).json()["restricted"] is True

    # Only admins manage access; members must belong to the org.
    assert (await client.put(f"/api/projects/{project_id}/access", headers=hm,
                             json={"restricted": False})).status_code == 403
    assert (await client.put(f"/api/projects/{project_id}/access", headers=admin,
                             json={"restricted": True, "member_ids": ["usr_nope"]})).status_code == 422


async def test_audit_log_records_security_events(client):  # noqa: F811
    admin = await register(client)
    project_id = await upload_and_import(client, admin)
    await add_user(client, admin, "dee@acme-consulting.com")
    await login(client, "dee@acme-consulting.com", "wrong-password")
    run_id = (await client.post("/api/runs", headers=admin, json={"project_id": project_id})).json()["run_id"]
    await runner.wait(run_id)
    entries = (await client.get("/api/audit", headers=admin)).json()
    actions = {e["action"] for e in entries}
    assert {"org.register", "upload.created", "upload.imported", "user.created", "auth.login_failed",
            "run.started"} <= actions
    failed = next(e for e in entries if e["action"] == "auth.login_failed")
    assert failed["user_email"] == "dee@acme-consulting.com" and failed["ip"]
    assert (await client.get("/api/audit?action=auth.", headers=admin)).json()[0]["action"].startswith("auth.")

    analyst = bearer(await login(client, "dee@acme-consulting.com"))
    assert (await client.get("/api/audit", headers=analyst)).status_code == 403
    other = await register(client, org="Other Co", email="boss@other-co.com")
    assert all(e["action"] == "org.register" for e in (await client.get("/api/audit", headers=other)).json())
