"""Change detection between two analysis runs of the same project.

Compares the stored agent outputs of a run with its baseline (the previous
completed run) and lists what changed: competitors appearing or disappearing,
new evidence of competitor features, competitor and client price changes, gaps
opened or closed, new or resolved security issues, client announcements and
hiring. Every change cites evidence ids from the *current* run.

A category is compared only when the agent that produces it completed in both
runs and inspected the same scope — a skipped research step must never read as
"all competitors disappeared".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from cip.core.urls import urlparse

SEVERITIES = ["critical", "warning", "info"]
SECURITY_TO_ALERT = {"critical": "critical", "high": "critical", "medium": "warning", "low": "info", "info": "info"}
PRICE_CHANGE_THRESHOLD = 0.05  # relative change in entry price worth reporting


@dataclass
class Change:
    kind: str
    severity: str
    title: str
    detail: str = ""
    subject: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    before: object = None
    after: object = None

    def to_dict(self) -> dict:
        return asdict(self)


def severity_rank(sev: str) -> int:
    return SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES)


def at_least(sev: str, minimum: str) -> bool:
    return severity_rank(sev) <= severity_rank(minimum)


def _host(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.") or None


def _competitor_key(c: dict) -> str:
    return _host(c.get("url")) or (c.get("name") or "").strip().lower()


def _money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return "n/a"
    return f"{amount:g} {currency or ''}/month".replace("  ", " ")


# --------------------------------------------------------------------------- categories


def _competitors(prev: dict, cur: dict) -> list[Change]:
    before = {_competitor_key(c): c for c in prev.get("competitors", [])}
    after = {_competitor_key(c): c for c in cur.get("competitors", [])}
    out = []
    for key in after.keys() - before.keys():
        c = after[key]
        out.append(Change("competitor_new", "warning", f"New competitor: {c['name']}",
                          f"{c.get('classification', 'direct').replace('_', ' ')} competitor"
                          + (f" — {c['description']}" if c.get("description") else ""),
                          subject=c["name"], evidence_ids=c.get("evidence_ids", [])[:5], after=c.get("url")))
    for key in before.keys() - after.keys():
        c = before[key]
        out.append(Change("competitor_dropped", "info", f"No longer listed as a competitor: {c['name']}",
                          "Not found or not verified as comparable in this run.", subject=c["name"],
                          before=c.get("url")))
    return out


def _competitor_features(prev_cmp: dict, cur_cmp: dict, prev_fc: dict, cur_fc: dict) -> list[Change]:
    def by_competitor(fc: dict, cmp: dict) -> dict[str, dict[str, str]]:
        key_of = {c["id"]: _competitor_key(c) for c in cmp.get("competitors", [])}
        table: dict[str, dict[str, str]] = {}
        for row in fc.get("rows", []):
            for cid, status in row.get("competitors", {}).items():
                if cid in key_of:
                    table.setdefault(key_of[cid], {})[row["feature_id"]] = status
        return table

    before, after = by_competitor(prev_fc, prev_cmp), by_competitor(cur_fc, cur_cmp)
    client_status = {r["feature_id"]: r.get("client") for r in cur_fc.get("rows", [])}
    names = {r["feature_id"]: r.get("feature_name", r["feature_id"]) for r in cur_fc.get("rows", [])}
    competitors = {_competitor_key(c): c for c in cur_cmp.get("competitors", [])}
    out = []
    for key in before.keys() & after.keys():
        comp = competitors.get(key, {})
        evidence = {f["feature_id"]: f.get("evidence_ids", []) for f in comp.get("features", [])}
        for fid, status in after[key].items():
            old = before[key].get(fid, "unknown")
            if status == "available" and old != "available":
                client_has = client_status.get(fid) == "available"
                out.append(Change(
                    "competitor_feature", "info" if client_has else "warning",
                    f"{comp.get('name', key)}: new evidence of {names.get(fid, fid)}",
                    "The client already offers this." if client_has else
                    f"The client does not offer this ({client_status.get(fid, 'missing')}).",
                    subject=comp.get("name", key), evidence_ids=evidence.get(fid, [])[:5], before=old, after=status))
    return out


def _competitor_pricing(prev: dict, cur: dict, prev_cmp: dict, cur_cmp: dict) -> list[Change]:
    def by_key(pa: dict, cmp: dict) -> dict[str, dict]:
        key_of = {c["id"]: _competitor_key(c) for c in cmp.get("competitors", [])}
        return {key_of[p["competitor_id"]]: p for p in pa.get("competitors", []) if p.get("competitor_id") in key_of}

    before, after = by_key(prev, prev_cmp), by_key(cur, cur_cmp)
    out = []
    for key in before.keys() & after.keys():
        b, a = before[key], after[key]
        out += _price_changes(a.get("name", key), b, a, competitor=True)
    return out


def _price_changes(name: str, b: dict, a: dict, competitor: bool) -> list[Change]:
    out = []
    ev = a.get("evidence_ids", [])[:5]
    old, new = b.get("entry_price_monthly"), a.get("entry_price_monthly")
    if old and new and b.get("currency") == a.get("currency") and abs(new - old) / old >= PRICE_CHANGE_THRESHOLD:
        direction = "cut" if new < old else "raised"
        sev = "warning" if competitor and new < old else "info"
        out.append(Change("competitor_price" if competitor else "client_price", sev,
                          f"{name} {direction} its entry price: {_money(old, b.get('currency'))} → "
                          f"{_money(new, a.get('currency'))}",
                          f"{(new - old) / old:+.0%} on the cheapest published plan.", subject=name,
                          evidence_ids=ev, before=old, after=new))
    for flag, label in (("free_trial", "a free trial"), ("free_tier", "a free tier"),
                        ("enterprise_contact", "an enterprise / contact-sales tier")):
        if bool(a.get(flag)) != bool(b.get(flag)):
            started = bool(a.get(flag))
            out.append(Change("competitor_pricing_practice" if competitor else "client_pricing_practice",
                              "warning" if competitor and started else "info",
                              f"{name} {'now offers' if started else 'no longer advertises'} {label}",
                              subject=name, evidence_ids=ev, before=bool(b.get(flag)), after=started))
    return out


def _client_pricing(prev: dict, cur: dict) -> list[Change]:
    b, a = prev.get("client") or {}, cur.get("client") or {}
    out = _price_changes("The client", b, a, competitor=False) if b and a else []
    if prev.get("position") and cur.get("position") and prev["position"] != cur["position"]:
        out.append(Change("market_position", "warning",
                          f"Price position vs market: {prev['position']} → {cur['position']}",
                          f"Market entry-price median now {_money((cur.get('market') or {}).get('entry_price_median'), (cur.get('market') or {}).get('currency'))}.",
                          evidence_ids=(a.get("evidence_ids") or [])[:5], before=prev["position"], after=cur["position"]))
    return out


def _gap_key(g: dict) -> str:
    return g.get("feature_id") or f"{g.get('gap_type')}:{g.get('name', '').lower()}"


def _gaps(prev: dict, cur: dict) -> list[Change]:
    before = {_gap_key(g): g for g in prev.get("gaps", [])}
    after = {_gap_key(g): g for g in cur.get("gaps", [])}
    existing = {e["feature_id"]: e for e in cur.get("existing", []) if e.get("feature_id")}
    out = []
    for key in after.keys() - before.keys():
        g = after[key]
        out.append(Change("gap_new", "warning", f"New gap: {g['name']}", g.get("description", ""),
                          subject=g.get("category", ""), evidence_ids=g.get("evidence_ids", [])[:5],
                          after=g.get("gap_type")))
    for key in before.keys() - after.keys():
        g = before[key]
        now = existing.get(g.get("feature_id") or "")
        out.append(Change("gap_closed", "info", f"Gap closed: {g['name']}",
                          f"Now {now['status']} for the client." if now else "No longer identified as a gap.",
                          subject=g.get("category", ""), evidence_ids=(now or {}).get("evidence_ids", [])[:5],
                          before=g.get("gap_type")))
    return out


def _inspected(sec: dict) -> set[str]:
    scope = " ".join(sec.get("scope", []))
    out = set()
    if (sec.get("site") or {}).get("checked"):
        out |= {"web", "process"}
    if "OSV.dev" in scope:
        out.add("dependency")
    if "insecure patterns" in scope:
        out.add("code")
    return out


def _security(prev: dict, cur: dict) -> list[Change]:
    if not prev.get("enabled") or not cur.get("enabled"):
        return []
    both = _inspected(prev) & _inspected(cur)
    before = {i["key"]: i for i in prev.get("issues", []) if i.get("category") in both}
    after = {i["key"]: i for i in cur.get("issues", []) if i.get("category") in both}
    out = []
    for key in after.keys() - before.keys():
        i = after[key]
        out.append(Change("security_new", SECURITY_TO_ALERT.get(i["severity"], "info"),
                          f"New {i['severity']} security issue: {i['title']}", i.get("recommendation", ""),
                          subject=i.get("category", ""), evidence_ids=[i["evidence_id"]], after=i["severity"]))
    for key in before.keys() - after.keys():
        i = before[key]
        out.append(Change("security_resolved", "info", f"Security issue resolved: {i['title']}",
                          subject=i.get("category", ""), before=i["severity"]))
    if _inspected(prev) == _inspected(cur) and prev.get("grade") and prev.get("grade") != cur.get("grade"):
        worse = (cur.get("score") or 0) < (prev.get("score") or 0)
        out.append(Change("security_grade", "warning" if worse else "info",
                          f"Security grade {prev['grade']} → {cur['grade']} (score {prev.get('score')} → "
                          f"{cur.get('score')})", before=prev.get("grade"), after=cur.get("grade")))
    return out


def _client_activity(prev: dict, cur: dict) -> list[Change]:
    out = []
    seen = {a.get("url") for a in prev.get("announcements", [])}
    for a in cur.get("announcements", []):
        if a.get("url") not in seen:
            out.append(Change("client_announcement", "info",
                              f"New {'product ' if a.get('is_product') else ''}announcement: {a['title']}",
                              subject=a.get("section", ""), evidence_ids=[a["evidence_id"]] if a.get("evidence_id")
                              else [], after=a.get("url")))
    hb, ha = prev.get("hiring") or {}, cur.get("hiring") or {}
    old_areas = {s["area"]: s for s in hb.get("signals", [])}
    for s in ha.get("signals", []):
        if s["area"] not in old_areas:
            out.append(Change("client_hiring", "info", f"Client started hiring in {s['area']} ({s['count']} role(s))",
                              ", ".join(s.get("examples", [])[:3]), subject=s["area"],
                              evidence_ids=[s["evidence_id"]] if s.get("evidence_id") else [], after=s["count"]))
    nb, na = hb.get("job_count"), ha.get("job_count")
    if nb and na is not None and abs(na - nb) / nb >= 0.25 and abs(na - nb) >= 2:
        out.append(Change("client_hiring_volume", "info", f"Open roles at the client: {nb} → {na}",
                          before=nb, after=na))
    return out


def _apps(prev: dict, cur: dict, known_competitors: set[str]) -> list[Change]:
    """``known_competitors``: lower-cased names of competitors already present in the previous run."""
    out = []
    before = {(a["platform"], a["app_id"]): a for a in prev.get("client_apps", [])}
    for a in cur.get("client_apps", []):
        b = before.get((a["platform"], a["app_id"]))
        label = f"{a['name']} ({'iOS' if a['platform'] == 'ios' else 'Android'})"
        if b is None:
            out.append(Change("client_app_new", "info", f"Client app now listed: {label}",
                              evidence_ids=[a["evidence_id"]], after=a.get("rating")))
            continue
        if a.get("rating") and b.get("rating") and abs(a["rating"] - b["rating"]) >= 0.1:
            drop = a["rating"] < b["rating"]
            out.append(Change("client_app_rating", "warning" if drop else "info",
                              f"{label} rating {'fell' if drop else 'rose'}: {b['rating']:g}★ → {a['rating']:g}★",
                              f"{a.get('rating_count') or 0:,} ratings.", evidence_ids=[a["evidence_id"]],
                              before=b["rating"], after=a["rating"]))
        if a.get("version") and b.get("version") and a["version"] != b["version"]:
            out.append(Change("client_app_release", "info", f"Client released {label} {a['version']}",
                              (a.get("release_notes") or "")[:200], evidence_ids=[a["evidence_id"]],
                              before=b["version"], after=a["version"]))
    old_themes = {t["theme"]: t for t in (prev.get("client_reviews") or {}).get("themes", [])}
    for t in (cur.get("client_reviews") or {}).get("themes", []):
        if t["negative"] >= 3 and old_themes.get(t["theme"], {}).get("negative", 0) < 3:
            out.append(Change("client_app_theme", "warning",
                              f"New complaint theme in app reviews: {t['label']} ({t['negative']} negative reviews)",
                              t["examples"][0]["quote"] if t.get("examples") else "",
                              evidence_ids=t.get("evidence_ids", [])[:3]))

    def comp_apps(d: dict) -> dict[tuple[str, str], tuple[str, dict]]:
        return {(a["platform"], a["app_id"]): (e["name"], a) for e in d.get("competitor_apps", []) for a in e["apps"]}

    cb, ca = comp_apps(prev), comp_apps(cur)
    for key, (name, a) in ca.items():
        platform = "iOS" if a["platform"] == "ios" else "Android"
        if key not in cb:
            if name.lower() in known_competitors:  # a brand-new competitor is reported as such, not as an app launch
                out.append(Change("competitor_app_new", "warning", f"{name} has a new {platform} app: {a['name']}",
                                  subject=name, evidence_ids=[a["evidence_id"]]))
            continue
        b = cb[key][1]
        if a.get("rating") and b.get("rating") and abs(a["rating"] - b["rating"]) >= 0.2:
            out.append(Change("competitor_app_rating", "info",
                              f"{name} {platform} app rating {b['rating']:g}★ → {a['rating']:g}★", subject=name,
                              evidence_ids=[a["evidence_id"]], before=b["rating"], after=a["rating"]))
    return out


UX_PRACTICE_LABELS = {"self_serve_cta": "a self-serve sign-up call to action", "sales_cta": "a book-a-demo call to action",
                      "live_chat": "live chat", "help": "a help center / FAQ", "trust": "trust signals",
                      "search": "site search", "sign_in": "a sign-in link"}


def _ux(prev: dict, cur: dict) -> list[Change]:
    out = []
    pc, cc = prev.get("client") or {}, cur.get("client") or {}
    comparable = pc.get("browser") == cc.get("browser")  # same set of checks in both runs
    po, co = (pc.get("score") or {}).get("overall"), (cc.get("score") or {}).get("overall")
    if comparable and po is not None and co is not None and abs(co - po) >= 10:
        out.append(Change("ux_score", "warning" if co < po else "info", f"Website UX score {po} → {co}",
                          before=po, after=co))
    if comparable:
        before = {i["key"]: i for i in prev.get("issues", []) if i["severity"] in ("high", "medium")}
        after = {i["key"]: i for i in cur.get("issues", []) if i["severity"] in ("high", "medium")}
        for key in after.keys() - before.keys():
            i = after[key]
            out.append(Change("ux_issue_new", "warning" if i["severity"] == "high" else "info",
                              f"New {i['severity']} UX issue: {i['title']}", i.get("recommendation", ""),
                              subject=i["category"], evidence_ids=[i["evidence_id"]]))
        for key in before.keys() - after.keys():
            out.append(Change("ux_issue_resolved", "info", f"UX issue resolved: {before[key]['title']}",
                              subject=before[key]["category"]))
    rivals_before = {c["name"].lower(): c for c in prev.get("companies", []) if not c.get("is_client")}
    for c in cur.get("companies", []):
        b = rivals_before.get(c["name"].lower())
        if c.get("is_client") or b is None:
            continue
        for key, ev in (c.get("practices") or {}).items():
            if ev and not (b.get("practices") or {}).get(key) and key in UX_PRACTICE_LABELS:
                out.append(Change("competitor_ux_practice", "info",
                                  f"{c['name']} added {UX_PRACTICE_LABELS[key]} to its website", subject=c["name"],
                                  evidence_ids=[ev]))
    return out


# --------------------------------------------------------------------------- entry point


def diff_outputs(prev: dict[str, dict], cur: dict[str, dict]) -> list[dict]:
    """``prev``/``cur`` map agent name -> result data for agents that *completed* in that run."""
    changes: list[Change] = []

    def both(*agents: str) -> bool:
        return all(a in prev and a in cur for a in agents)

    if both("competitor_research"):
        changes += _competitors(prev["competitor_research"], cur["competitor_research"])
        if both("feature_comparison"):
            changes += _competitor_features(prev["competitor_research"], cur["competitor_research"],
                                            prev["feature_comparison"], cur["feature_comparison"])
        if both("pricing_analysis"):
            changes += _competitor_pricing(prev["pricing_analysis"], cur["pricing_analysis"],
                                           prev["competitor_research"], cur["competitor_research"])
    if both("pricing_analysis"):
        changes += _client_pricing(prev["pricing_analysis"], cur["pricing_analysis"])
    if both("gap_analysis"):
        changes += _gaps(prev["gap_analysis"], cur["gap_analysis"])
    if both("security_review"):
        changes += _security(prev["security_review"], cur["security_review"])
    if both("client_research"):
        changes += _client_activity(prev["client_research"], cur["client_research"])
    if both("app_store") and prev["app_store"].get("enabled") and cur["app_store"].get("enabled"):
        known = {c["name"].lower() for c in prev.get("competitor_research", {}).get("competitors", [])}
        changes += _apps(prev["app_store"], cur["app_store"], known)
    if both("ux_review") and prev["ux_review"].get("client") and cur["ux_review"].get("client"):
        changes += _ux(prev["ux_review"], cur["ux_review"])
    changes.sort(key=lambda c: (severity_rank(c.severity), c.kind, c.title))
    return [c.to_dict() for c in changes]


def summarize(changes: list[dict]) -> dict:
    counts = {s: sum(1 for c in changes if c["severity"] == s) for s in SEVERITIES}
    top = next((s for s in SEVERITIES if counts[s]), None)
    return {"counts": counts, "total": len(changes), "highest": top}
