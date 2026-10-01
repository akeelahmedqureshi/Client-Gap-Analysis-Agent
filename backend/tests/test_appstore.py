"""App-store analysis: listing discovery & ownership, review themes, requests, gaps and prioritization."""

import pytest

from cip.connectors.research.appstore import (
    AppListing,
    Review,
    analyze_reviews,
    apple_reviews,
    mask_pii,
    ownership,
    parse_app_link,
    play_listing,
)
from cip.connectors.research.web import WebFetcher
from cip.core.taxonomy import load_taxonomy

from fakes import sites_with_client_app, web_transport
from test_pipeline import run_all


def test_parse_app_links():
    assert parse_app_link("https://apps.apple.com/us/app/medibook/id2222222222?mt=8") == ("ios", "2222222222")
    assert parse_app_link("https://play.google.com/store/apps/details?id=com.medibook.app&hl=de") == \
        ("android", "com.medibook.app")
    assert parse_app_link("https://play.google.com/store/apps/details?id=javascript:alert(1)") is None
    assert parse_app_link("https://example.com/app/id123456") is None


def test_ownership_requires_domain_or_name_match():
    app = AppListing("ios", "1", "ABC Kids", "u", developer="ABC Learning LLC", developer_url="https://abckids.example")
    assert ownership(app, "ABC Healthcare", "abc-healthcare.com") == 0
    app.developer_url = "https://www.abc-healthcare.com/mobile"
    assert ownership(app, "ABC Healthcare", "abc-healthcare.com") == 0.95
    named = AppListing("ios", "2", "X", "u", developer="ABC Healthcare Holdings Inc.")
    assert ownership(named, "ABC Healthcare", None) == 0.8  # corporate suffixes ignored


def test_review_themes_requests_and_pii():
    assert mask_pii("call +1 512 555 0199 or me@example.com") == "call [phone] or [email]"
    reviews = [Review(1, "Crash", "It crashes on login"), Review(2, "Bug", "So buggy"),
               Review(3, "Idea", "Please add SMS reminders"), Review(5, "Great", "Love the booking")]
    a = analyze_reviews(reviews, load_taxonomy())
    themes = {t["theme"]: t for t in a.themes}
    assert themes["stability"]["negative"] == 2 and a.sentiment == {"negative": 2, "neutral": 1, "positive": 1}
    assert themes["login"]["negative"] == 1
    assert [r["feature_id"] for r in a.requests] == ["comm.sms"]


async def test_reviews_feed_drops_authors_and_masks_pii():
    fetcher = WebFetcher(transport=web_transport(sites_with_client_app()))
    reviews = await apple_reviews(fetcher, "1111111111")
    assert len(reviews) == 9
    text = " ".join(r.text for r in reviews)
    assert "jane_doe_88" not in repr(reviews) and "512 555 0199" not in text and "me@example.com" not in text


async def test_play_listing_from_json_ld():
    fetcher = WebFetcher(transport=web_transport())
    app = await play_listing(fetcher, "com.medibook.app")
    assert app.rating == 4.4 and app.rating_count == 1520 and app.developer == "MediBook Ltd"


async def test_client_without_app(make_ctx):
    ctx = make_ctx()
    status, statuses, _ = await run_all(ctx)
    data = ctx.data("app_store")
    assert statuses["app_store"].value == "completed"
    # The name-collision search hit ("ABC Kids Learning") is not attributed to the client.
    assert data["client_apps"] == [] and not data["client_has_app"]
    comps = {e["name"]: e for e in data["competitor_apps"]}
    assert {a["platform"] for a in comps["MediBook"]["apps"]} == {"ios", "android"}  # linked from its website
    assert comps["ClinicFlow"]["apps"][0]["found_via"] == "App Store search 'ClinicFlow'"  # verified by domain
    gap = next(g for g in ctx.data("gap_analysis")["gaps"] if g["feature_id"] == "ux.mobile_app")
    ev_ids = {a["evidence_id"] for e in data["competitor_apps"] for a in e["apps"]}
    assert ev_ids & set(gap["evidence_ids"])  # store listings merged into the comparison's gap
    assert ctx.data("competitor_research") and data["market"]["competitors_with_apps"] == 2


@pytest.fixture
def ctx_with_app(make_ctx):
    return make_ctx(fetcher=WebFetcher(transport=web_transport(sites_with_client_app())))


async def test_client_app_reviews_drive_gaps_and_priorities(ctx_with_app, make_ctx):
    ctx = ctx_with_app
    await run_all(ctx)
    data = ctx.data("app_store")
    app = data["client_apps"][0]
    assert app["name"] == "ABC Patient Scheduler" and app["rating"] == 3.1 and app["days_since_update"] > 180
    assert ctx.ledger.get(app["evidence_id"]).source_type == "app_store"
    names = {g["name"] for g in data["gaps"]}
    assert {"Mobile app stability (crashes & bugs)", "App store rating below competitors",
            "Mobile app release cadence"} <= names
    # Every quoted review is evidence with the verbatim (PII-masked) text.
    stability = next(t for t in data["client_reviews"]["themes"] if t["theme"] == "stability")
    quotes = [ctx.ledger.get(i).extracted_text for i in stability["evidence_ids"]]
    assert any("crashes every time" in q for q in quotes) and not any("555 0199" in q for q in quotes)

    gaps = ctx.data("gap_analysis")["gaps"]
    # A verified store listing means the client HAS a mobile app: no "Native mobile app" gap.
    assert not any(g["feature_id"] == "ux.mobile_app" for g in gaps)
    assert any(e["feature_id"] == "ux.mobile_app" for e in ctx.data("gap_analysis")["existing"])

    # Customers asking for SMS raise market demand for that gap (+1 vs the same run without the app reviews).
    request = next(r for r in data["requests"] if r["feature_id"] == "comm.sms")
    assert request["count"] == 2
    sms = next(o for o in ctx.data("opportunity_prioritization")["opportunities"] if "SMS" in o["name"])
    assert "2 recent App Store reviews" in sms["business_opportunity"]
    baseline = make_ctx()
    await run_all(baseline)
    sms_before = next(o for o in baseline.data("opportunity_prioritization")["opportunities"] if "SMS" in o["name"])
    assert sms["factors"]["market_demand"] == min(5, sms_before["factors"]["market_demand"] + 1)

    # A crash-ridden app is a top priority, with a mobile-quality patch plan.
    recs = {r["feature"]: r for r in ctx.data("opportunity_prioritization")["recommendations"]}
    assert "Mobile app stability (crashes & bugs)" in recs
    plan = next(p for p in ctx.data("enhancement_planning")["plans"]
                if p["feature"] == "Mobile app stability (crashes & bugs)")
    assert "Crash reporting" in " ".join(plan["infrastructure_changes"])
    assert any(f.title.startswith("MediBook users complain about pricing")
               for f in ctx.outputs["app_store"].findings)  # 2 negative MediBook reviews about pricing


async def test_skipped_without_external_research(make_ctx):
    ctx = make_ctx(approvals=("repository_access", "client_report"))
    fetched: list[str] = []
    transport = web_transport()
    orig = transport.handle_async_request

    async def spy(request):
        fetched.append(str(request.url))
        return await orig(request)

    transport.handle_async_request = spy
    ctx.fetcher = WebFetcher(transport=transport)
    await run_all(ctx)
    assert ctx.outputs.get("app_store") is None or ctx.outputs["app_store"].status.value == "skipped"
    assert not any("itunes.apple.com" in u or "play.google.com" in u for u in fetched)


def test_app_changes_between_runs():
    from cip.services.changes import diff_outputs

    def run(rating, version, comp_apps, themes_neg):
        return {"app_store": {
            "enabled": True,
            "client_apps": [{"platform": "ios", "app_id": "1", "name": "ABC", "rating": rating, "rating_count": 400,
                             "version": version, "evidence_id": f"ev_{version}"}],
            "client_reviews": {"themes": [{"theme": "stability", "label": "Crashes & bugs", "negative": themes_neg,
                                           "examples": [{"quote": "crashes"}], "evidence_ids": ["ev_q"]}]},
            "competitor_apps": [{"name": "MediBook", "apps": comp_apps}]},
            "competitor_research": {"competitors": [{"name": "MediBook"}]}}

    ios = {"platform": "ios", "app_id": "2", "name": "MediBook", "rating": 4.6, "evidence_id": "ev_m1"}
    android = {"platform": "android", "app_id": "com.medibook", "name": "MediBook", "rating": 4.4,
               "evidence_id": "ev_m2"}
    changes = {c["kind"]: c for c in diff_outputs(run(3.6, "2.2", [ios], 1), run(3.1, "2.3", [ios, android], 4))}
    assert changes["client_app_rating"]["severity"] == "warning" and changes["client_app_rating"]["after"] == 3.1
    assert changes["client_app_release"]["after"] == "2.3"
    assert changes["competitor_app_new"]["title"] == "MediBook has a new Android app: MediBook"
    assert changes["client_app_theme"]["severity"] == "warning"


async def test_each_quoted_review_is_its_own_evidence(ctx_with_app):
    await run_all(ctx_with_app)
    for t in ctx_with_app.data("app_store")["client_reviews"]["themes"]:
        assert len(set(t["evidence_ids"])) == len(t["examples"]), t["label"]
    md = ctx_with_app.data("report")["markdown"]
    assert "### Mobile apps (app stores)" in md and "ABC Patient Scheduler" in md and "⚠ stale" in md
