"""Detailed features and documented workflows: facts must be quoted from the source; interpretation is labelled."""

from cip.core import offering
from cip.core.evidence import EvidenceLedger
from cip.core.grounding import SourceDoc
from cip.core.taxonomy import load_taxonomy

from fakes import ScriptedLLM

FEATURES_PAGE = ("https://acme.example/features/booking",
                 "Online booking. Patients pick a free slot from the live calendar and receive an email confirmation. "
                 "Works on web and iOS. Syncs with Google Calendar. Group bookings are not supported.")
DOCS_PAGE = ("https://acme.example/docs/payments",
             "Payments. Connect your Stripe account in Settings before taking deposits. The invoice is created "
             "automatically when the appointment ends.")
BLOG = ("https://acme.example/blog/ai", "We think AI chat is the future.")


def docs():
    pages = [{"url": u, "title": "", "headings": [], "text": t} for u, t in (BLOG, DOCS_PAGE, FEATURES_PAGE)]
    return offering.capability_docs(pages)


def quoted(text, url, quote):
    return {"text": text, "source_url": url, "quote": quote}


FEATURES = {"features": [
    {"name": "Online booking", "feature_id": "workflow.scheduling", "category": "Scheduling",
     "description": "Patients book a free slot from a live calendar and get an email confirmation.",
     "source_url": FEATURES_PAGE[0], "quote": "Patients pick a free slot from the live calendar",
     "how_it_works": [quoted("Patient picks a slot; an email confirmation follows", FEATURES_PAGE[0],
                             "receive an email confirmation")],
     "platforms": [quoted("Web", FEATURES_PAGE[0], "Works on web and iOS"),
                   quoted("Android", FEATURES_PAGE[0], "Works on Android")],          # not in the source
     "integrations": [quoted("Google Calendar", FEATURES_PAGE[0], "Syncs with Google Calendar")],
     "limitations": [quoted("No group bookings", FEATURES_PAGE[0], "Group bookings are not supported")],
     "purpose": "Fewer phone calls to book appointments", "intended_users": ["Patients", "Front desk"],
     "use_cases": ["Booking a check-up"], "benefits": ["Less admin time"]},
    {"name": "AI chat", "source_url": FEATURES_PAGE[0], "quote": "Our AI answers every question"},  # invented
]}
WORKFLOWS = {"workflows": [
    {"name": "Take a deposit", "objective": "Get paid before the visit", "trigger": "A patient books",
     "actors": [{"role": "Clinic admin", "responsibilities": "Connects Stripe"}],
     "steps": [
         {"action": "Connect Stripe in Settings", "actor": "Clinic admin", "source_url": DOCS_PAGE[0],
          "quote": "Connect your Stripe account in Settings"},
         {"action": "Patient pays the deposit", "actor": "Patient", "inferred": True},
         {"action": "Invoice is created when the appointment ends", "kind": "system", "source_url": DOCS_PAGE[0],
          "quote": "The invoice is created automatically when the appointment ends"}],
     "outcome": "Paid appointment with an invoice"},
    {"name": "Invented refund flow", "steps": [{"action": "Refund", "source_url": DOCS_PAGE[0],
                                                 "quote": "Refunds are instant"}]},
]}


async def test_facts_need_a_quote_found_in_the_source_and_interpretation_is_separate():
    ledger = EvidenceLedger()
    llm = ScriptedLLM({"_LLMFeatures": FEATURES})
    items, errors = await offering.extract_features(llm, ledger, docs(), "Acme", "Acme Clinic", load_taxonomy())
    assert errors == []
    by = {i["name"]: i for i in items}
    booking = by["Online booking"]
    assert booking["verification"] == "documented" and booking["status"] == "available"
    assert [p["text"] for p in booking["facts"]["platforms"]] == ["Web"]     # "Android" had no matching quote
    assert booking["dropped_details"] == 1
    assert booking["facts"]["integrations"][0]["text"] == "Google Calendar"
    assert booking["facts"]["limitations"][0]["text"] == "No group bookings"
    assert booking["interpretation"]["intended_users"] == ["Patients", "Front desk"]
    for f in ("how_it_works", "platforms", "integrations", "limitations"):
        for x in booking["facts"][f]:
            assert ledger.get(x["evidence_id"]).source_url == FEATURES_PAGE[0]
    assert by["AI chat"]["verification"] == "unverified" and by["AI chat"]["evidence_ids"] == []


async def test_only_offering_pages_are_read_in_batches_of_whole_pages():
    d = docs()
    assert [x.url for x in d] == [FEATURES_PAGE[0], DOCS_PAGE[0]]            # blog post left out; features first
    big = [SourceDoc(f"https://acme.example/docs/{i}", "x" * 20_000, "website") for i in range(5)]
    groups = offering.batches(big, max_batches=3)
    assert len(groups) == 3 and all(len(g) == 1 for g in groups)            # never more than the batch budget


async def test_features_found_in_several_batches_are_merged():
    ledger = EvidenceLedger()
    pages = [SourceDoc(FEATURES_PAGE[0], FEATURES_PAGE[1] + " " + "x" * 30_000, "website"),  # fills a batch
             SourceDoc(DOCS_PAGE[0], DOCS_PAGE[1], "website")]

    def answer(user):
        if DOCS_PAGE[0] in user:
            return {"features": [{"name": "Online Booking", "feature_id": "workflow.scheduling",
                                  "source_url": DOCS_PAGE[0], "quote": "Connect your Stripe account in Settings",
                                  "prerequisites": [quoted("A Stripe account", DOCS_PAGE[0],
                                                           "Connect your Stripe account in Settings")]}]}
        return FEATURES

    items, _ = await offering.extract_features(ScriptedLLM({"_LLMFeatures": answer}), ledger, pages, "Acme", "Acme",
                                               load_taxonomy())
    booking = next(i for i in items if i["feature_id"] == "workflow.scheduling")
    assert booking["facts"]["prerequisites"][0]["text"] == "A Stripe account"
    assert set(booking["sources"]) == {FEATURES_PAGE[0], DOCS_PAGE[0]}


async def test_workflows_are_built_from_documented_steps():
    ledger = EvidenceLedger()
    flows, errors = await offering.extract_workflows(ScriptedLLM({"_LLMWorkflows": WORKFLOWS}), ledger, docs(),
                                                     "Acme", "Acme")
    assert errors == [] and [w["name"] for w in flows] == ["Take a deposit"]   # nothing of the refund flow is sourced
    w = flows[0]
    assert [s["documented"] for s in w["steps"]] == [True, False, True]       # the inferred step is labelled
    assert w["documented_steps"] == 2 and w["verification"] == "documented"
    assert w["steps"][2]["kind"] == "system" and w["actors"][0]["role"] == "Clinic admin"


async def test_client_inventory_and_workflows_reach_the_products_tab(make_ctx):
    from test_pipeline import run_all

    def features(user):
        return {"features": [{"name": "Online booking", "feature_id": "workflow.scheduling",
                              "source_url": "https://abc-healthcare.com/products/patient-scheduler",
                              "quote": "Online booking for clinics with a patient portal",
                              "integrations": [quoted("Dashboard", "https://abc-healthcare.com/products/patient-scheduler",
                                                      "a patient portal and dashboard")]}]}

    ctx = make_ctx(llm=ScriptedLLM({"_LLMFeatures": features, "_LLMWorkflows": {"workflows": []}}))
    await run_all(ctx)
    data = ctx.data("product_features")
    booking = next(i for i in data["inventory"] if i["name"] == "Online booking")
    assert booking["verification"] == "documented" and booking["facts"]["integrations"]
    assert data["observations"]["workflow.scheduling"]["verification"] == "documented"
    assert data["documented_workflows"] == [] and "workflows" in data           # catalogue workflows still there
