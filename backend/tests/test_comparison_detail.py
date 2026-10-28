"""Comparison cells say what each party offers, how, and how it is sold — and 'confirmed unavailable' needs an
explicit statement from the company itself."""

from cip.agents.product_features import combine_signals

from fakes import ScriptedLLM
from test_pipeline import run_all

ABC = "https://abc-healthcare.com/products/patient-scheduler"
MB, MB_PRICING = "https://medibook.io/", "https://medibook.io/pricing"


def features(user):
    if "Company: ABC Healthcare" in user:
        return {"features": [
            {"name": "Online booking", "feature_id": "workflow.scheduling", "category": "Scheduling",
             "description": "Patients book appointments online through a patient portal.",
             "source_url": ABC, "quote": "Online booking for clinics with a patient portal",
             "limitations": [{"text": "Clinics only", "source_url": ABC, "quote": "Online booking for clinics"}]},
            {"name": "Video calls", "feature_id": "comm.video", "status": "unavailable",
             "source_url": ABC, "quote": "Video consultations are not available yet."},
            # "unavailable" without a quote that says so: nothing is known, never "confirmed unavailable"
            {"name": "Live chat", "feature_id": "comm.chat", "status": "unavailable",
             "source_url": ABC, "quote": "Online booking for clinics with a patient portal"}]}
    if "Company: MediBook" in user:
        return {"features": [
            {"name": "AI assistant", "feature_id": "ai.assistant", "description": "Answers patient questions 24/7.",
             "source_url": MB, "quote": "AI assistant answers patient questions 24/7"}]}
    return {"features": []}


def pricing(user):
    if "Company: MediBook" in user:
        return {"plans": [{"name": "Pro", "price": 29, "currency": "USD", "period": "month", "unit": "user",
                           "source_url": MB_PRICING, "quote": "$29 /month per practitioner",
                           "add_ons": [{"text": "AI assistant add-on", "source_url": MB_PRICING,
                                        "quote": "$29 /month per practitioner"}]}]}
    return {"plans": []}


def test_confirmed_unavailable_needs_an_explicit_documented_statement():
    doc = {"feature_id": "comm.video", "status": "missing", "evidence_id": "e1", "source": "llm_extraction",
           "confidence": 0.85}
    obs = combine_signals([doc], {"website": True})
    assert obs["comm.video"].status.value == "missing" and obs["comm.video"].verification == "documented"
    # Any evidence that it is offered wins over a "not offered" reading.
    web = {"feature_id": "comm.video", "status": "available", "evidence_id": "e2", "source": "website",
           "confidence": 0.6, "keywords": 2, "page": "https://x.com/features"}
    assert combine_signals([doc, web], {"website": True})["comm.video"].status.value == "available"


async def test_cells_describe_implementation_limitations_and_packaging(make_ctx):
    ctx = make_ctx(llm=ScriptedLLM({"_LLMFeatures": features, "_LLMPricing": pricing,
                                    "_LLMWorkflows": {"workflows": []}}))
    await run_all(ctx)
    data = ctx.data("feature_comparison")
    rows = {r["feature_id"]: r for r in data["rows"]}
    mb = next(c["id"] for c in data["competitors"] if c["name"] == "MediBook")

    booking = rows["workflow.scheduling"]["cells"]["client"]
    assert booking["verification"] == "documented" and "patient portal" in booking["implementation"]
    assert booking["limitations"] == ["Clinics only"]

    video = rows["comm.video"]
    assert video["client"] == "missing" and video["cells"]["client"]["label"] == "confirmed unavailable"
    assert ctx.data("product_features")["observations"].get("comm.chat", {}).get("status") != "missing"
    chat = next(i for i in ctx.data("product_features")["inventory"] if i["name"] == "Live chat")
    assert chat["status"] == "unknown" and chat["verification"] == "unverified"

    ai = rows["ai.assistant"]
    assert ai["cells"][mb]["packaging"]["add_on"] and ai["cells"][mb]["verification"] == "documented"
    assert ai["description"].startswith("Answers patient questions")
    assert ai["relevance"].endswith("(AI capability)") and "Not publicly identified for the client" in ai["significance"]


def test_negated_statements_do_not_evidence_a_capability():
    from cip.core.negation import affirmed, negates

    assert affirmed("SMS reminders reduce no-shows.", ["sms"]) == ["sms"]          # "no-shows" is not a negation
    assert affirmed("No setup fees. Online booking included.", ["online booking"]) == ["online booking"]
    assert affirmed("Video calls are not available yet.", ["video calls"]) == []
    assert affirmed("Live chat is coming soon.", ["live chat"]) == []
    assert affirmed("Book without video-call support.", ["video call"]) == []
    assert affirmed("La videollamada no está disponible.", ["videollamada"]) == []
    assert affirmed("Chat is coming soon. We already offer live chat for admins.", ["live chat"]) == ["live chat"]
    assert negates("Group bookings are not supported") and not negates("Online booking for clinics")
