"""Multi-language analysis: language detection, multilingual capability keywords, localization, reporting."""

from cip.agents.csv_intake import parse_csv
from cip.connectors.research.web import WebFetcher, parse_html
from cip.core import language

from fakes import FakeSearch, html, web_transport
from test_pipeline import run_all

SPANISH_CSV = """Client Name,Project Name,Project URL,Description,Industry
Clínica Sol,Portal de pacientes,https://clinicasol.example,Software de citas para clínicas,Healthcare
"""
ALTERNATES = ('<link rel="alternate" hreflang="es" href="https://clinicasol.example/">'
              '<link rel="alternate" hreflang="en" href="https://clinicasol.example/en/">'
              '<link rel="alternate" hreflang="x-default" href="https://clinicasol.example/">')
SPANISH_SITE = {
    "https://clinicasol.example/": html(
        "Clínica Sol", "<h1>Reserve su cita en minutos</h1><p>Con Clínica Sol puede reservar cita online y recibir "
        "recordatorios por SMS. Pago en línea con tarjeta de crédito y facturación automática para su clínica.</p>",
        "Software de gestión de citas para clínicas.", ["/precios", "/contacto"], head=ALTERNATES, lang="es"),
    "https://clinicasol.example/precios": html(
        "Precios", "<h1>Precios</h1><p>Plan mensual desde 49 EUR. Suscripción anual con descuento. Portal del "
        "paciente incluido para todas las clínicas.</p>", lang="es"),
    "https://clinicasol.example/contacto": html("Contacto", "<p>Escríbanos y le responderemos en el día.</p>", lang="es"),
}


def test_language_detection():
    assert language.detect("", "es-ES") == "es" and language.detect("x", "pt_BR") == "pt"
    assert language.detect("Wir bieten Ihnen die beste Lösung für Ihre Praxis und mehr mit der App für den Alltag") == "de"
    assert language.detect("Nous avons la meilleure solution pour votre cabinet et des outils pour les équipes") == "fr"
    assert language.detect("予約はオンラインで簡単にできます。" * 20) == "ja"
    assert language.detect("ok") is None
    s = language.summarize(["es", "es", "en", None])
    assert s["primary"] == "es" and s["pages"] == {"es": 2, "en": 1} and s["supported"]
    assert not language.summarize(["ja"])["supported"]


def test_page_language_and_hreflang_alternates():
    page = parse_html("https://clinicasol.example/", 200, SPANISH_SITE["https://clinicasol.example/"])
    assert page.lang == "es"
    assert page.alternates == {"es": "https://clinicasol.example/", "en": "https://clinicasol.example/en/"}


async def test_spanish_site_maps_onto_the_taxonomy_with_verbatim_quotes(make_ctx):
    record = parse_csv(SPANISH_CSV).records[0]
    ctx = make_ctx(record=record, fetcher=WebFetcher(transport=web_transport(SPANISH_SITE)), search=FakeSearch())
    await run_all(ctx)
    research = ctx.data("client_research")
    assert research["languages"]["primary"] == "es" and research["languages"]["supported"]
    obs = ctx.data("product_features")["observations"]
    for fid in ("workflow.scheduling", "billing.payments", "billing.invoicing", "comm.sms", "billing.subscriptions",
                "ux.self_service_portal", "ux.localization"):
        assert obs[fid]["status"] in ("available", "partial"), fid
    quotes = [ctx.ledger.get(e).extracted_text for e in obs["workflow.scheduling"]["evidence_ids"]]
    assert any("reservar cita" in (q or "").lower() for q in quotes)
    loc = ctx.ledger.get(obs["ux.localization"]["evidence_ids"][0])
    assert loc.claim == "Website is published in 2 languages (en, es)" and "hreflang" in loc.extracted_text
    assert "**Website language(s):** Spanish" in ctx.data("report")["markdown"]
    assert ctx.data("quality_assurance")["metrics"]["languages"]["es"] >= 1


async def test_unsupported_language_is_flagged(make_ctx):
    sites = {"https://clinicasol.example/": html("クリニック", "<p>" + "オンライン予約ができます。" * 30 + "</p>", lang="ja")}
    record = parse_csv(SPANISH_CSV).records[0]
    ctx = make_ctx(record=record, fetcher=WebFetcher(transport=web_transport(sites)), search=FakeSearch())
    await run_all(ctx)
    assert ctx.data("client_research")["languages"]["primary"] == "ja"
    issues = ctx.data("quality_assurance")["issues"]
    assert any(i["area"] == "language" and "Japanese" in i["message"] for i in issues)
