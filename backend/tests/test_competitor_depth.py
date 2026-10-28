"""Deep, competitor-specific profiles: every section quoted from the competitor's own pages, overlap with the
client computed from the shared feature data, and unverifiable claims dropped."""

from fakes import ScriptedLLM
from test_pipeline import run_all

MB, CF = "https://medibook.io/", "https://clinicflow.com/"


def sv(value, url, quote):
    return {"value": value, "source_url": url, "quote": quote}


def profile(user: str) -> dict:
    if "Candidate: MediBook" in user:
        return {
            "is_comparable": True, "classification": "direct",
            "description": sv("Clinic scheduling with an AI assistant", MB, "Online booking and scheduling for clinics"),
            "features": [{"feature_id": "ai.assistant", "source_url": MB,
                          "quote": "AI assistant answers patient questions 24/7"},
                         {"feature_id": "comm.video", "source_url": MB, "quote": "Video visits in 4K"}],  # invented
            "overview": [sv("Scheduling platform used by about 3,000 clinics", MB, "Trusted by 3,000 clinics")],
            "ai_capabilities": [sv("AI assistant that answers patient questions around the clock", MB,
                                   "AI assistant answers patient questions 24/7")],
            "integrations": [sv("Okta single sign-on", MB, "Single sign-on (SSO) with Okta"),
                             sv("Salesforce", MB, "Native Salesforce sync")],                  # invented
            "onboarding_support": [sv("Help center", MB, "Help center")],
            "strengths": [sv("Native iOS and Android apps", MB, "iOS app and Android app on Google Play")],
        }
    return {
        "is_comparable": True, "classification": "direct",
        "products": [sv("Clinic scheduling software with a patient portal", CF,
                        "Scheduling software for clinics with online booking")],
        "integrations": [sv("Public REST API", CF, "a public REST API")],
        "differentiators": [sv("Analytics dashboard for clinics", CF, "analytics dashboard")],
    }


async def test_top3_profiles_are_specific_quoted_and_compared_with_the_client(make_ctx):
    llm = ScriptedLLM({"_LLMCompetitorProfile": profile, "_LLMFeatures": {"features": []},
                       "_LLMWorkflows": {"workflows": []}})
    ctx = make_ctx(llm=llm)
    await run_all(ctx)
    comps = {c["name"]: c for c in ctx.data("competitor_research")["competitors"]}
    mb, cf = comps["MediBook"], comps["ClinicFlow"]

    # Sections are competitor-specific and only hold quoted statements.
    assert [i["value"] for i in mb["profile"]["integrations"]] == ["Okta single sign-on"]   # Salesforce dropped
    assert mb["profile"]["overview"][0]["value"].startswith("Scheduling platform")
    assert "ai_capabilities" in mb["profile"] and "ai_capabilities" not in cf["profile"]
    assert cf["profile"]["integrations"][0]["value"] == "Public REST API"
    for c in (mb, cf):
        for items in c["profile"].values():
            for i in items:
                ev = ctx.ledger.get(i["evidence_id"])
                assert ev and ev.source_url.rstrip("/") == i["source_url"].rstrip("/")

    # A feature claim whose quote is not on the page is not "documented".
    feats = {o["feature_id"]: o for o in mb["features"]}
    assert feats["ai.assistant"]["verification"] == "documented"
    video = feats.get("comm.video")
    assert video is None or video["verification"] != "documented"

    # Overlap with the client, from the shared feature data.
    client = {f for f, o in ctx.data("product_features")["observations"].items() if o["status"] in ("available", "partial")}
    shared = {x["feature_id"] for x in mb["overlap"]["shared"]}
    only = {x["feature_id"] for x in mb["overlap"]["competitor_only"]}
    assert shared <= client and not (only & client) and "ai.assistant" in only
    assert {x["feature_id"] for x in mb["overlap"]["client_only"]} <= client
