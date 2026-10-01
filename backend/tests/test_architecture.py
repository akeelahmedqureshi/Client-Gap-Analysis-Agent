"""Architecture diagrams: model, role equivalence, escaping, rendering, report/PDF embedding."""

import xml.etree.ElementTree as ET

from cip.core.architecture import build_architecture, current_model, target_model, to_mermaid, to_svg
from cip.services.report_pdf import report_html
from test_pipeline import run_all

PROFILE = {"architecture": ["Monolith"], "technologies": [
    {"name": "React", "category": "frontend"}, {"name": "Django", "category": "backend"},
    {"name": "PostgreSQL", "category": "database"}, {"name": "Stripe", "category": "payments"},
    {"name": "Sentry", "category": "observability"}, {"name": "Jest", "category": "testing"},
    {"name": "Tailwind CSS", "category": "frontend"}]}


def test_layers_and_noise_filtering():
    m = current_model([PROFILE], [])
    layers = {n.label: n.layer for n in m.nodes}
    assert layers == {"React": "clients", "Django": "application", "PostgreSQL": "data", "Stripe": "external",
                      "Sentry": "platform", "Web users": "users"}  # Jest / Tailwind are not architecture


def test_target_adds_only_missing_roles():
    current = current_model([PROFILE], [])
    recs = [{"gap_id": "g1", "feature": "Free trial"}, {"gap_id": "g2", "feature": "AI knowledge base"},
            {"gap_id": "g3", "feature": "Observability (error tracking, metrics, tracing)"}]
    gaps = [{"id": "g1", "gap_type": "pricing"}, {"id": "g2", "gap_type": "ai", "feature_id": "ai.rag"},
            {"id": "g3", "gap_type": "technology"}]
    new = {n.label: n.note for n in target_model(current, recs, gaps).nodes if n.new}
    assert "Payments provider" not in new           # Stripe already fills the role
    assert "Error tracking & metrics" not in new    # Sentry already fills the role
    assert {"Billing & entitlements", "RAG / knowledge service", "Vector store (pgvector)",
            "LLM API (OpenRouter)"} <= set(new)
    assert new["Vector store (pgvector)"] == "AI knowledge base"


def test_hostile_labels_are_escaped_everywhere():
    a = build_architecture([], ['<script>alert(1)</script>', 'x" onload="y'], [], [])
    for svg in (a["svg_current"], a["svg_target"]):
        ET.fromstring(svg)  # well-formed XML
        assert "<script" not in svg and "&lt;script&gt;" in svg and 'onload="y' not in svg
    assert "<script>" not in a["mermaid_current"] and 'x" onload' not in a["mermaid_current"]
    assert a["based_on"].startswith("declared technology")


def test_mermaid_structure():
    mm = to_mermaid(current_model([PROFILE], []))
    assert mm.startswith("flowchart LR") and 'subgraph data["Data"]' in mm and "application --> external" in mm


async def test_report_and_pdf_embedding(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    arch = ctx.data("enhancement_planning")["architecture"]
    ET.fromstring(arch["svg_target"])
    assert arch["new_components"] and all(c["for"] for c in arch["new_components"])
    md = ctx.data("report")["markdown"]
    assert md.count("```mermaid") == 2 and "<!-- architecture-diagram:target -->" in md
    html = report_html(md, "r", ctx.data("report")["report"]["sections"]["architecture"])
    assert html.count("<svg") == 2 and "```mermaid" not in html and "flowchart LR" not in html
    assert "<svg" not in report_html(md, "r")  # without data the block is dropped, never shown as raw code
    assert "flowchart LR" not in report_html(md, "r")
    assert to_svg(current_model([], [])).startswith("<svg")  # empty model still renders
