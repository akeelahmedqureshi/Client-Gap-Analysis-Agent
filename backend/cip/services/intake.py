"""Client intake: review incoming client records, then apply the person's decisions.

The same rules serve the CSV upload (many rows) and the manual client form (one row):

1. **Validation** against the client schema (core/client_schema.py). A row with a missing mandatory field or an
   invalid value is fixed (``edits``) or skipped; nothing is guessed.
2. **Duplicates within the upload**: rows that are identical, share a website, a client ID or a client + project
   name are *confirmed* duplicates; rows sharing a client name, a client domain or a website domain are
   *potential* ones. Every row of a group needs an explicit Add or Skip.
3. **Existing clients**: each row is compared with the organization's clients and projects (client ID, website,
   domain, name). A matching row needs an explicit Update, Replace, Merge or Skip (or Add, when it is only a
   possible match and adding it creates no duplicate).

There is no default action: an affected row without a decision stays *pending* and is never imported. Rows that
need nothing are added; they go ahead while others are pending only when the person allows a partial import.
``review`` reports all of this (and the plan's own conflicts, such as two rows changing the same project);
``apply`` re-runs the review on the server and executes only rows that are ready, each in its own savepoint, with
an audit entry holding the values before and after.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.core import client_schema as cs
from cip.core.schemas import NormalizedRecord
from cip.db.models import Client, CsvUpload, Project, User
from cip.services import audit
from cip.services.access import visible_projects

Action = Literal["add", "skip", "update", "replace", "merge"]
EXISTING_ACTIONS = ("update", "replace", "merge")
ACTION_LABELS = {"add": "add", "skip": "skip", "update": "update the existing record",
                 "replace": "replace the existing record", "merge": "merge into the existing record"}


class Decision(BaseModel):
    action: Action | None = None
    target: str | None = None  # the existing client to update / replace / merge into
    merge: dict[str, Literal["existing", "incoming"]] = Field(default_factory=dict)
    confirm_replace: bool = False


class Plan(BaseModel):
    mapping: dict[str, str] | None = None  # field -> CSV header (CSV only)
    edits: dict[int, dict[str, str]] = Field(default_factory=dict)  # row -> field -> corrected value
    decisions: dict[int, Decision] = Field(default_factory=dict)
    allow_partial: bool = False  # import the ready rows although others are still pending


class IntakeError(Exception):
    def __init__(self, status: int, message: str, review: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.review = status, message, review


@dataclass
class Incoming:
    row: int
    raw: dict[str, str]
    values: dict[str, str]
    errors: dict[str, str]
    edited: list[str]
    record: NormalizedRecord


@dataclass
class Match:
    client: Client
    project: Project | None
    kind: str  # confirmed | potential
    reasons: list[str]
    strength: int
    accessible: bool
    values: dict[str, str] = field(default_factory=dict)


def incoming(rows: list[tuple[int, dict[str, str]]], plan: Plan, domain_checks: dict[int, dict] | None = None
             ) -> list[Incoming]:
    """Apply the person's corrections and validate each row against the client schema."""
    out = []
    for number, raw in rows:
        edits = {k: v for k, v in (plan.edits.get(number) or {}).items() if k in cs.BY_KEY}
        values, errors = cs.clean({**raw, **edits})
        rec = cs.to_record(number, values, errors)
        rec.domain_check = (domain_checks or {}).get(number)
        out.append(Incoming(number, raw, values, errors,
                            [k for k, v in edits.items() if not cs.same_value(k, v, raw.get(k))], rec))
    return out


# --------------------------------------------------------------------------- existing clients

@dataclass
class Directory:
    clients: list[Client]
    projects: dict[str, list[Project]]  # client id -> projects
    visible: set[str]                   # project ids the person may see
    admin: bool

    def accessible(self, client: Client, project: Project | None) -> bool:
        if self.admin:
            return True
        if project is not None:
            return project.id in self.visible
        return any(p.id in self.visible for p in self.projects.get(client.id, []))


async def directory(session: AsyncSession, user: User) -> Directory:
    clients = (await session.execute(select(Client).where(Client.org_id == user.org_id))).scalars().all()
    projects = (await session.execute(select(Project).where(Project.org_id == user.org_id))).scalars().all()
    visible = set((await session.execute(select(Project.id).where(visible_projects(user)))).scalars().all())
    by_client: dict[str, list[Project]] = defaultdict(list)
    for p in projects:
        by_client[p.client_id].append(p)
    return Directory(list(clients), by_client, visible, user.role == "admin")


def _related(a: str | None, b: str | None) -> bool:
    return bool(a and b and a != b and (a.endswith("." + b) or b.endswith("." + a)))


def find_matches(inc: Incoming, d: Directory) -> list[Match]:
    v = inc.values
    ext, site = (v.get("external_id") or "").lower(), cs.site_key(v.get("project_url"))
    domain, nk = inc.record.client.domain, cs.name_key(v.get("client_name"))
    key = cs.client_key(domain, v.get("client_name")) if v.get("client_name") or domain else ""
    pname = cs.name_key(v.get("project_name") or v.get("client_name"))
    out = []
    for c in d.clients:
        reasons, confirmed, strength = [], False, 0
        projects = d.projects.get(c.id, [])
        same_site = next((p for p in projects if site and cs.site_key(p.url) == site), None)
        if ext and (c.external_id or "").lower() == ext:
            reasons.append("same client ID")
            confirmed, strength = True, max(strength, 5)
        if same_site:
            reasons.append("same website URL")
            confirmed, strength = True, max(strength, 4)
        if key and c.key == key:
            reasons.append("same domain" if domain else "same name")
            confirmed, strength = True, max(strength, 4)
        cnk = cs.name_key(c.name)
        if nk and cnk == nk and "same name" not in reasons:
            reasons.append("same name")
            strength = max(strength, 3)
        elif nk and cnk and cnk != nk and min(len(nk), len(cnk)) >= 4 and (
                cnk.startswith(nk + " ") or nk.startswith(cnk + " ")):
            reasons.append("similar name")
            strength = max(strength, 1)
        if _related(domain, c.domain):
            reasons.append("related domain")
            strength = max(strength, 2)
        if not reasons:
            continue
        project = same_site or next((p for p in projects if pname and cs.name_key(p.name) == pname), None)
        if project is not None and same_site is None:
            reasons.append("same project name")
        accessible = d.accessible(c, project)
        values = cs.record_values(project.record if project else None, c) if accessible else {}
        if accessible and project is None:
            values = {k: values[k] for k in cs.CLIENT_FIELDS if k in values}
        out.append(Match(c, project, "confirmed" if confirmed else "potential", reasons, strength, accessible,
                         values))
    return sorted(out, key=lambda m: (-m.strength, m.client.name))


# --------------------------------------------------------------------------- duplicates within the upload

def duplicate_groups(rows: list[Incoming]) -> list[dict]:
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for inc in rows:
        v = inc.values
        signature = tuple(sorted((k, x.lower()) for k, x in v.items()))

        def add(kind: str, reason: str, key) -> None:
            if key:
                buckets[(kind, reason, key)].append(inc.row)

        add("confirmed", "identical rows", signature)
        add("confirmed", "same website URL", cs.site_key(v.get("project_url")))
        add("confirmed", "same client ID", (v.get("external_id") or "").lower())
        ck = cs.client_key(inc.record.client.domain, v.get("client_name")) if v.get("client_name") else ""
        pn = cs.name_key(v.get("project_name") or v.get("client_name"))
        add("confirmed", "same client and project name", (ck, pn) if ck and pn else None)
        add("potential", "same client name", cs.name_key(v.get("client_name")))
        add("potential", "same client domain", inc.record.client.domain)
        add("potential", "same website domain", cs.domain_of(cs.normalize_url(v.get("project_url"))))
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = [(kind, reason, sorted(set(members))) for (kind, reason, _), members in buckets.items()
             if len(set(members)) > 1]
    for _, _, members in links:
        for m in members[1:]:
            parent[find(m)] = find(members[0])
    groups: dict[int, dict] = {}
    by_row = {r.row: r for r in rows}
    for kind, reason, members in links:
        g = groups.setdefault(find(members[0]), {"rows": set(), "links": [], "kind": "potential"})
        g["rows"].update(members)
        if not any(x["reason"] == reason and x["rows"] == members for x in g["links"]):
            g["links"].append({"kind": kind, "reason": reason, "rows": members})
        if kind == "confirmed":
            g["kind"] = "confirmed"
    out = []
    for i, g in enumerate(sorted(groups.values(), key=lambda g: min(g["rows"])), 1):
        members = sorted(g["rows"])
        conflicts = []
        for f in cs.FIELDS:
            vals = {r: by_row[r].values.get(f.key) for r in members if by_row[r].values.get(f.key)}
            distinct = []
            for x in vals.values():
                if not any(cs.same_value(f.key, x, y) for y in distinct):
                    distinct.append(x)
            if len(distinct) > 1:
                conflicts.append({"field": f.key, "label": f.label, "values": {str(r): x for r, x in vals.items()}})
        # rows that are identical need no further reasons
        identical = [set(x["rows"]) for x in g["links"] if x["reason"] == "identical rows"]
        links = [x for x in g["links"] if x["reason"] == "identical rows"
                 or not any(set(x["rows"]) <= rows for rows in identical)]
        out.append({"id": i, "kind": g["kind"], "rows": members, "links": links, "conflicts": conflicts})
    return out


# --------------------------------------------------------------------------- one row

def _result_values(action: str, existing: dict[str, str], new: dict[str, str], choices: dict[str, str],
                   client_only: bool) -> dict[str, str]:
    """The stored values after an update, replace or merge (only the client's fields when no project matches;
    the incoming project then becomes a new project of that client)."""
    out = {}
    for f in cs.FIELDS:
        a, b = existing.get(f.key), new.get(f.key)
        if action == "replace":
            v = b
        elif action == "update":
            v = b or a
        else:  # merge
            v = (b if choices.get(f.key) == "incoming" else a) if a and b and not cs.same_value(f.key, a, b) \
                else (a or b)
        if v:
            out[f.key] = v
    if client_only:  # the project part comes from the incoming row whatever the action
        out.update({k: v for k, v in new.items() if k not in cs.CLIENT_FIELDS})
    return out


def _changes(existing: dict[str, str], result: dict[str, str]) -> list[dict]:
    return [{"field": f.key, "label": f.label, "before": existing.get(f.key), "after": result.get(f.key)}
            for f in cs.FIELDS if not cs.same_value(f.key, existing.get(f.key), result.get(f.key))]


def _match_out(m: Match, inc: Incoming) -> dict:
    out = {"client_id": m.client.id, "project_id": m.project.id if m.project else None, "kind": m.kind,
           "reasons": m.reasons, "accessible": m.accessible}
    if not m.accessible:
        return {**out, "client_name": None, "note": "An existing client you do not have access to matches this "
                                                    "record; ask an administrator to review it."}
    fields = []
    for f in cs.FIELDS:
        a, b = m.values.get(f.key), inc.values.get(f.key)
        if not a and not b:
            continue
        fields.append({"field": f.key, "label": f.label, "existing": a, "incoming": b,
                       "same": cs.same_value(f.key, a, b), "conflict": bool(a and b and not cs.same_value(f.key, a, b))})
    exact = m.project is not None and all(x["same"] for x in fields)
    return {**out, "client_name": m.client.name, "project_name": m.project.name if m.project else None,
            "fields": fields, "exact": exact,
            "scope": "project" if m.project else "client"}


def review_row(inc: Incoming, matches: list[Match], group: dict | None, decision: Decision | None,
               done: dict | None) -> dict:
    d = decision or Decision()
    base = {"row": inc.row, "values": inc.values, "raw": inc.raw, "edited": inc.edited, "errors": inc.errors,
            "missing": [k for k in cs.REQUIRED if k in inc.errors and not inc.values.get(k)],
            "group": group["id"] if group else None,
            "matches": [_match_out(m, inc) for m in matches],
            "decision": d.model_dump(), "needs": [], "changes": [], "result": None, "target": None,
            "preview": {"client": inc.record.client.name, "project": inc.record.project.name,
                        "domain": inc.record.client.domain, "website": inc.record.project.url},
            "domain_check": inc.record.domain_check}
    if done:
        return {**base, "status": "imported", "action": done.get("action"), "allowed": [], "done": done}
    confirmed = [m for m in matches if m.kind == "confirmed"]
    allowed = (["add"] if not confirmed else []) + (list(EXISTING_ACTIONS) if any(m.accessible for m in matches)
                                                     else []) + ["skip"]
    base["allowed"] = allowed
    affected = bool(inc.errors or group or matches)
    if d.action == "skip":
        return {**base, "status": "skipped", "action": "skip"}
    needs = []
    if inc.errors:
        needs.append("Fill in or correct: " + ", ".join(cs.BY_KEY[k].label for k in inc.errors) + " — or skip the row.")
    action = d.action or (None if affected else "add")
    if action is None and (group or matches):
        options = " or ".join(ACTION_LABELS[a] for a in allowed)
        why = ("it matches an existing client" if matches else "it is a possible duplicate within the upload")
        needs.append(f"Choose what to do — {why}: {options}.")
    elif action is not None and action not in allowed:
        reason = ("the record is the same client as an existing one (add would create a duplicate)"
                  if action == "add" else "no existing client you can access matches it")
        needs.append(f"“{ACTION_LABELS[action]}” is not available: {reason}.")
    target = None
    if action in EXISTING_ACTIONS and action in allowed:
        reachable = [m for m in matches if m.accessible]
        if d.target:
            target = next((m for m in reachable if m.client.id == d.target), None)
            if target is None:
                needs.append("Choose one of the matching clients as the record to change.")
        elif len(reachable) == 1:
            target = reachable[0]
        else:
            needs.append("Several existing clients match: choose which one to " + action + ".")
        if target is not None:
            conflicts = [f.key for f in cs.FIELDS if target.values.get(f.key) and inc.values.get(f.key)
                         and not cs.same_value(f.key, target.values[f.key], inc.values[f.key])
                         and not (target.project is None and f.key not in cs.CLIENT_FIELDS)]
            if action == "merge":
                open_fields = [k for k in conflicts if k not in d.merge]
                if open_fields:
                    needs.append("Choose the value to keep for: " + ", ".join(cs.BY_KEY[k].label for k in open_fields)
                                 + ".")
            if action == "replace" and not d.confirm_replace:
                needs.append("Confirm the replacement: existing values are overwritten and empty incoming fields "
                             "clear them.")
            result = _result_values(action, target.values, inc.values, d.merge, target.project is None)
            values, errors = cs.clean(result)
            if errors and not inc.errors:
                needs.append("The resulting record would be invalid: " + "; ".join(errors.values()))
            base.update(result=values, changes=_changes(target.values, values),
                        target={"client_id": target.client.id, "client_name": target.client.name,
                                "project_id": target.project.id if target.project else None,
                                "project_name": target.project.name if target.project else None})
    if action == "add" and not needs:
        base["result"] = inc.values
    status = "invalid" if inc.errors else ("pending" if needs else "ready")
    return {**base, "status": status, "action": action, "needs": needs}


# --------------------------------------------------------------------------- the whole review

def _plan_conflicts(rows: list[dict], key_of: dict[int, str]) -> list[dict]:
    """Ready rows that cannot all be applied: two rows creating or changing the same project, or giving the same
    client different values."""
    ready = [r for r in rows if r["status"] == "ready"]
    out = []

    def flag(members: list[dict], message: str) -> None:
        out.append({"rows": sorted(r["row"] for r in members), "message": message})

    adds = [r for r in ready if r["action"] == "add"]
    for label, keyf in (("website", lambda r: cs.site_key(r["values"].get("project_url"))),
                        ("client and project name", lambda r: (key_of[r["row"]], cs.name_key(
                            r["values"].get("project_name") or r["values"].get("client_name"))))):
        seen: dict = defaultdict(list)
        for r in adds:
            if keyf(r):
                seen[keyf(r)].append(r)
        for members in seen.values():
            if len(members) > 1:
                flag(members, f"These rows would create the same project (same {label}); skip all but one.")
    by_client: dict[str, list[dict]] = defaultdict(list)
    for r in adds:
        by_client["new:" + key_of[r["row"]]].append(r)
    for r in ready:
        if r["action"] in EXISTING_ACTIONS and r["target"]:
            by_client[r["target"]["client_id"]].append(r)
    for members in by_client.values():
        if len(members) < 2:
            continue
        for f in cs.CLIENT_FIELDS:
            vals = {(r["result"] or {}).get(f) for r in members} - {None}
            if len({cs.name_key(v) if f == "client_name" else v.lower() for v in vals}) > 1:
                flag(members, f"These rows give the same client different values for {cs.BY_KEY[f].label.lower()} "
                              "(" + " / ".join(sorted(vals)) + "); correct them so they agree, or skip one.")
    projects: dict[str, list[dict]] = defaultdict(list)
    for r in ready:
        if r["action"] in EXISTING_ACTIONS and r["target"] and r["target"]["project_id"]:
            projects[r["target"]["project_id"]].append(r)
    for members in projects.values():
        if len(members) > 1:
            flag(members, "These rows change the same existing project; choose one of them and skip the others.")
    unique = []
    for c in out:
        if c not in unique:
            unique.append(c)
    return unique


async def review(session: AsyncSession, user: User, rows: list[Incoming], plan: Plan,
                 done: dict[int, dict] | None = None) -> dict:
    done = done or {}
    d = await directory(session, user)
    open_rows = [r for r in rows if r.row not in done]
    groups = duplicate_groups(open_rows)
    group_of = {r: g for g in groups for r in g["rows"]}
    out_rows = []
    key_of = {}
    for inc in rows:
        key_of[inc.row] = cs.client_key(inc.record.client.domain, inc.values.get("client_name"))
        matches = [] if inc.row in done else find_matches(inc, d)
        out_rows.append(review_row(inc, matches, group_of.get(inc.row), plan.decisions.get(inc.row),
                                   done.get(inc.row)))
    conflicts = _plan_conflicts(out_rows, key_of)
    for c in conflicts:
        for r in out_rows:
            if r["row"] in c["rows"] and r["status"] == "ready":
                r["status"] = "conflict"
                r["needs"].append(c["message"])
    open_out = [r for r in out_rows if r["status"] != "imported"]

    def count(pred) -> int:
        return sum(1 for r in open_out if pred(r))

    summary = {
        "total": len(out_rows),
        "imported_before": len(out_rows) - len(open_out),
        "valid": count(lambda r: not r["errors"]),
        "missing_mandatory": count(lambda r: r["missing"]),
        "invalid": count(lambda r: any(k not in r["missing"] for k in r["errors"])),
        "csv_duplicates": count(lambda r: r["group"] is not None),
        "csv_confirmed_duplicates": count(lambda r: r["group"] is not None
                                          and next(g for g in groups if g["id"] == r["group"])["kind"] == "confirmed"),
        "existing_matches": count(lambda r: any(m["kind"] == "confirmed" for m in r["matches"])),
        "potential_matches": count(lambda r: r["matches"] and all(m["kind"] == "potential" for m in r["matches"])),
        **{f"selected_{a}": count(lambda r, a=a: r["action"] == a and r["status"] in ("ready", "skipped"))
           for a in ("add", "update", "replace", "merge", "skip")},
        "ready": count(lambda r: r["status"] == "ready"),
        "pending": count(lambda r: r["status"] in ("pending", "invalid", "conflict")),
        "conflicts": len(conflicts),
    }
    summary["can_import"] = summary["ready"] > 0 and (summary["pending"] == 0 or plan.allow_partial)
    return {"rows": out_rows, "groups": groups, "conflicts": conflicts, "summary": summary,
            "allow_partial": plan.allow_partial, "schema": cs.schema()}


# --------------------------------------------------------------------------- applying the decisions

def _snapshot(client: Client, project: Project | None) -> dict[str, str]:
    return cs.record_values(project.record if project else None, client)


async def _apply_row(session: AsyncSession, request: Request | None, user: User, row: dict, rec: NormalizedRecord,
                     new_clients: dict[str, Client], upload: CsvUpload | None) -> dict:
    action, values = row["action"], row["result"]
    record = cs.to_record(row["row"], values)
    record.domain_check = rec.domain_check
    key = cs.client_key(record.client.domain, record.client.name)
    source = {"upload": upload.id if upload else None, "row": row["row"] if upload else None,
              "via": "csv" if upload else "form"}
    if action == "add":
        client = new_clients.get(key)
        created = client is None
        if created:
            taken = (await session.execute(select(Client).where(Client.org_id == user.org_id, Client.key == key))
                     ).scalar_one_or_none()
            if taken is not None:
                raise IntakeError(409, f"A client with this domain or name was added meanwhile ({taken.name}); "
                                       "review the record again.")
            client = Client(org_id=user.org_id, key=key, name=record.client.name, domain=record.client.domain,
                            industry=record.client.industry, external_id=record.client.external_id)
            session.add(client)
            await session.flush()
            new_clients[key] = client
        project = Project(org_id=user.org_id, client_id=client.id, upload_id=upload.id if upload else None,
                          name=record.project.name, url=record.project.url, description=record.project.description,
                          record=record.model_dump(), created_by=user.id, restricted=False)
        session.add(project)
        await session.flush()
        audit.record(session, request, user, "client.added" if created else "client.project_added", "client",
                     client.id, project=project.id, after=values, **source)
        return {"action": "add", "client_id": client.id, "client_name": client.name, "project_id": project.id,
                "new_client": created}
    target = row["target"]
    client = await session.get(Client, target["client_id"])
    if client is None or client.org_id != user.org_id:
        raise IntakeError(409, "The existing client no longer exists.")
    project = await session.get(Project, target["project_id"]) if target["project_id"] else None
    if target["project_id"] and (project is None or project.client_id != client.id):
        raise IntakeError(409, "The existing project no longer exists.")
    before = _snapshot(client, project)
    if key != client.key:
        other = (await session.execute(select(Client).where(Client.org_id == user.org_id, Client.key == key,
                                                            Client.id != client.id))).scalar_one_or_none()
        if other is not None:
            raise IntakeError(409, f"The new name or domain belongs to another client ({other.name}).")
    # the client keeps its id; its stored fields follow the decision
    client.key, client.name, client.domain = key, record.client.name, record.client.domain
    client.industry, client.external_id = record.client.industry, record.client.external_id
    if project is None:
        project = Project(org_id=user.org_id, client_id=client.id, upload_id=upload.id if upload else None,
                          name=record.project.name, created_by=user.id, restricted=False)
        session.add(project)
    elif rec.domain_check is None and (project.record or {}).get("domain_check"):
        record.domain_check = project.record["domain_check"]
    project.name, project.url, project.description = record.project.name, record.project.url, record.project.description
    project.record = record.model_dump()
    await session.flush()
    after = _snapshot(client, project)
    changed = {k for k in set(before) | set(after) if not cs.same_value(k, before.get(k), after.get(k))}
    audit.record(session, request, user, f"client.{action}d" if action != "merge" else "client.merged", "client",
                 client.id, project=project.id, before={k: before.get(k) for k in sorted(changed)},
                 after={k: after.get(k) for k in sorted(changed)}, **source)
    return {"action": action, "client_id": client.id, "client_name": client.name, "project_id": project.id,
            "changed": sorted(changed)}


async def apply(session: AsyncSession, request: Request | None, user: User, rows: list[Incoming], plan: Plan,
                upload: CsvUpload | None = None) -> dict:
    done = {int(k): v for k, v in ((upload.results if upload else None) or {}).items() if v.get("ok")}
    result = await review(session, user, rows, plan, done)
    s = result["summary"]
    if s["pending"] and not plan.allow_partial:
        raise IntakeError(409, f"{s['pending']} record(s) still need a decision or a correction. Resolve them, or "
                               "confirm that only the ready records should be imported.", result)
    ready = [r for r in result["rows"] if r["status"] == "ready"]
    if not ready:
        raise IntakeError(422, "Nothing to import: no record is ready.", result)
    by_row = {r.row: r for r in rows}
    new_clients: dict[str, Client] = {}
    outcomes, failed = [], []
    now = datetime.now(timezone.utc).isoformat()
    for r in sorted(ready, key=lambda r: r["row"]):
        try:
            async with session.begin_nested():
                outcome = await _apply_row(session, request, user, r, by_row[r["row"]].record, new_clients, upload)
            outcomes.append({"row": r["row"], **outcome})
            if upload is not None:
                upload.results = {**(upload.results or {}),
                                  str(r["row"]): {**outcome, "ok": True, "at": now, "by": user.email}}
        except Exception as exc:  # noqa: BLE001 - one failing record never leaves the others half-applied
            message = exc.message if isinstance(exc, IntakeError) else f"{type(exc).__name__}: {exc}"[:300]
            new_clients = {k: c for k, c in new_clients.items() if c in session}
            failed.append({"row": r["row"], "action": r["action"], "reason": message})
    counts = {a: sum(1 for o in outcomes if o["action"] == a) for a in ("add", "update", "replace", "merge")}
    open_rows = [r for r in result["rows"] if r["status"] != "imported"]
    summary = {
        "added": counts["add"], "updated": counts["update"], "replaced": counts["replace"],
        "merged": counts["merge"],
        "skipped": sum(1 for r in open_rows if r["status"] == "skipped"),
        "failed_validation": sum(1 for r in open_rows if r["status"] == "invalid"),
        "pending": sum(1 for r in open_rows if r["status"] in ("pending", "conflict")),
        "failed": len(failed),
        "new_clients": sum(1 for o in outcomes if o.get("new_client")),
    }
    if upload is not None:
        upload.plan = plan.model_dump(mode="json")
        audit.record(session, request, user, "upload.imported", "upload", upload.id, **summary,
                     failed_rows=[f["row"] for f in failed] or None)
    return {"summary": summary, "results": outcomes, "failed": failed,
            "pending_rows": [r["row"] for r in open_rows if r["status"] in ("pending", "invalid", "conflict")],
            "created_projects": [o["project_id"] for o in outcomes if o["action"] == "add"]}
