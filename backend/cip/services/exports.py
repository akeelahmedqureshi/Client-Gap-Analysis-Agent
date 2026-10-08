"""Structured exports of a run (BRS 28; PRD 10.36): comparison matrix, gaps, opportunities, recommendations and
evidence as CSV, and the whole analysis as JSON.

CSV cells that a spreadsheet would treat as a formula (``=``, ``+``, ``-``, ``@``, tab, CR) are prefixed with
``'`` so a scraped competitor name can never execute in Excel. Exports carry stored results only; nothing is
re-researched and no credentials are ever part of agent data.
"""

from __future__ import annotations

import csv
import io

from cip.core.schemas import STATUS_LABELS

CSV_EXPORTS = ("matrix", "gaps", "opportunities", "recommendations", "evidence")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    text = str(value)
    return f"'{text}" if text.startswith(FORMULA_START) else text


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([safe_cell(h) for h in header])
    for r in rows:
        w.writerow([safe_cell(c) for c in r])
    return buf.getvalue()


def _label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "")


def matrix_csv(data: dict) -> str:
    comps = data.get("comparison", {}).get("competitors", [])
    header = ["Capability", "Category", "Client"] + [c["name"] for c in comps] + [
        "Top-3 coverage", "Top-10 coverage", "Market class", "Must have"]
    rows = []
    for r in data.get("comparison", {}).get("rows", []):
        rows.append([r["feature_name"], r["category"], _label(r["client"])]
                    + [_label(r["competitors"].get(c["id"])) for c in comps]
                    + [f"{r.get('top3_count', 0)}/{r.get('top3_total', 0)}",
                       f"{r.get('top10_count', 0)}/{r.get('top10_total', 0)}", r.get("market_class"),
                       r.get("must_have", False)])
    return to_csv(header, rows)


def gaps_csv(data: dict) -> str:
    names = {c["id"]: c["name"] for c in data.get("comparison", {}).get("competitors", [])}
    rows = [[g["name"], g["gap_type"], g["category"], g["description"], [names.get(c, c) for c in g["competitors_with"]],
             g.get("market_class"), g.get("landscape_share"), g["confidence"], g["basis"], g["evidence_ids"]]
            for g in data.get("gaps", {}).get("gaps", [])]
    return to_csv(["Gap", "Type", "Category", "Description", "Competitors with it", "Market class",
                   "Top-10 share", "Confidence", "Basis", "Evidence ids"], rows)


def opportunities_csv(data: dict) -> str:
    opps = sorted(data.get("prioritization", {}).get("opportunities", []), key=lambda o: -o.get("normalized_score", 0))
    factors = sorted({f for o in opps for f in o.get("factors", {})})
    rows = [[o["name"], o.get("priority"), o.get("business_category"), o.get("phase"), o.get("gap_type"),
             o.get("normalized_score"), o.get("score", {}).get("total"), o.get("score", {}).get("confidence"),
             o.get("business_opportunity"), o.get("revenue_opportunity"), o.get("basis")]
            + [o.get("factors", {}).get(f) for f in factors] for o in opps]
    return to_csv(["Opportunity", "Priority", "Category", "Horizon", "Gap type", "Normalized score", "Score",
                   "Evidence confidence", "Business opportunity", "Revenue opportunity", "Basis"] + factors, rows)


def recommendations_csv(data: dict) -> str:
    recs = sorted(data.get("prioritization", {}).get("recommendations", []), key=lambda r: -r["score"]["total"])
    rows = [[i, r["feature"], r.get("priority"), r["phase"], r.get("business_category"), r["score"]["total"],
             r["complexity"], r["problem"], r["business_impact"], r["user_impact"], r["technical_approach"],
             r["expected_outcome"], r.get("dependencies", []), r.get("basis"), r.get("evidence_ids", [])]
            for i, r in enumerate(recs, 1)]
    return to_csv(["Rank", "Recommendation", "Priority", "Horizon", "Category", "Score", "Complexity", "Problem",
                   "Business impact", "User impact", "Technical approach", "Expected outcome", "Dependencies",
                   "Basis", "Evidence ids"], rows)


def evidence_csv(evidence: list[dict]) -> str:
    rows = [[e["id"], e["claim"], e["source_type"], e["source_url"], e.get("extracted_text"),
             e.get("repository_path"), e.get("line_range"), e["confidence"], e["collected_at"]] for e in evidence]
    return to_csv(["Id", "Claim", "Source type", "Source URL", "Quote", "Repository path", "Lines", "Confidence",
                   "Collected"], rows)


def render_csv(kind: str, data: dict, evidence: list[dict]) -> str:
    if kind == "evidence":
        return evidence_csv(evidence)
    return {"matrix": matrix_csv, "gaps": gaps_csv, "opportunities": opportunities_csv,
            "recommendations": recommendations_csv}[kind](data)
