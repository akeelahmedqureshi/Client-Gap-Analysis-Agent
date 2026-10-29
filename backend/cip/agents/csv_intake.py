"""CSV Intelligence Agent: validation, column detection, normalization, de-duplication."""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field

from cip.agents.base import Agent, RunContext
from cip.core import client_schema
from cip.core.client_schema import domain_of, normalize_url  # noqa: F401  (re-exported for callers)
from cip.core.schemas import AgentResult, Finding, NormalizedRecord

log = logging.getLogger(__name__)

# Column names come from the client schema (core/client_schema.py), shared with the manual client form.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {f.key: f.aliases for f in client_schema.FIELDS}
MAX_ROWS = 5_000


@dataclass
class CsvRow:
    """One data row: its line number, the raw value of each schema field, and the validation result."""

    row_number: int
    raw: dict[str, str]
    values: dict[str, str]
    errors: dict[str, str]


@dataclass
class CsvParseResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    rows: list[CsvRow] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    column_mapping: dict[str, str] = field(default_factory=dict)
    unmapped_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        """The file can be reviewed (rows with missing or invalid fields are resolved row by row)."""
        return not self.errors and bool(self.records)


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", h.strip().lower().replace("\ufeff", ""))


def detect_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map canonical field -> original header. Exact alias matches win over fuzzy ones."""
    mapping: dict[str, str] = {}
    normalized = {h: _norm_header(h) for h in headers}
    for field_name, aliases in COLUMN_ALIASES.items():
        for h, n in normalized.items():
            if n in aliases and h not in mapping.values():
                mapping[field_name] = h
                break
    for field_name, aliases in COLUMN_ALIASES.items():
        if field_name in mapping:
            continue
        for h, n in normalized.items():
            if h in mapping.values():
                continue
            if any(re.search(rf"\b{re.escape(a)}\b", n) for a in aliases if len(a) > 3):
                mapping[field_name] = h
                break
    unmapped = [h for h in headers if h not in mapping.values()]
    return mapping, unmapped


def raw_values(row: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    return {f: (row.get(h) or "").strip() for f, h in mapping.items() if isinstance(row.get(h), str)}


def build_row(row_number: int, raw: dict[str, str]) -> tuple[CsvRow, NormalizedRecord]:
    values, errors = client_schema.clean(raw)
    return CsvRow(row_number, raw, values, errors), client_schema.to_record(row_number, values, errors)


def _decode(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def parse_csv(content: bytes | str, mapping: dict[str, str] | None = None) -> CsvParseResult:
    """Read a CSV, map its columns to the client schema (or use ``mapping``: field -> header) and validate every
    row. Rows with missing or invalid fields are kept and flagged; nothing is inferred for mandatory fields and
    nothing is de-duplicated here (services/intake.py reviews duplicates and existing clients)."""
    result = CsvParseResult()
    text = _decode(content)
    if not text.strip():
        result.errors.append("CSV file is empty")
        return result
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    result.headers = headers
    if not headers:
        result.errors.append("CSV has no header row")
        return result
    if mapping is not None:
        mapping = {f: h for f, h in mapping.items() if f in client_schema.BY_KEY and h in headers}
        unmapped = [h for h in headers if h not in mapping.values()]
    else:
        mapping, unmapped = detect_columns(headers)
    result.column_mapping, result.unmapped_columns = mapping, unmapped
    if not ({"client_name", "project_name", "project_url", "client_email", "external_id"} & set(mapping)):
        result.errors.append(
            "Could not detect any identifying column (client name, client ID, project name, website URL or client "
            f"email). Found columns: {headers}"
        )
        return result
    for f in client_schema.REQUIRED:
        if f not in mapping:
            result.warnings.append(f"No column for the mandatory field '{client_schema.BY_KEY[f].label}': every row "
                                   "will need it filled in (or map a column to it).")
    for i, row in enumerate(reader, start=2):  # header is line 1
        if i - 1 > MAX_ROWS:
            result.warnings.append(f"Only the first {MAX_ROWS} rows were processed")
            break
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        try:
            parsed_row, rec = build_row(i, raw_values(row, mapping))
        except Exception as exc:  # noqa: BLE001 - one unreadable row must never fail the whole upload
            log.warning("CSV row %s could not be normalized: %s", i, exc)
            result.warnings.append(f"Row {i} could not be read and was skipped ({type(exc).__name__})")
            continue
        result.rows.append(parsed_row)
        result.records.append(rec)
    if not result.records:
        result.errors.append("CSV contains no data rows")
    return result


class CsvIntakeAgent(Agent):
    """Records the normalized CSV row as evidence so later claims can cite it."""

    name = "csv_intake"
    description = "Validate and normalize the uploaded project record"
    max_attempts = 1

    async def run(self, ctx: RunContext) -> AgentResult:
        rec = ctx.record
        source = f"csv://row/{rec.row_number}"
        evidence = []
        findings = []
        ev = ctx.ledger.add(f"Client of record: {rec.client.name}", source, "csv", 1.0,
                            extracted_text=f"client={rec.client.name}; project={rec.project.name}")
        evidence.append(ev)
        if rec.project.description:
            evidence.append(ctx.ledger.add("Project description (from client records)", source, "csv", 0.9,
                                           extracted_text=rec.project.description))
        for feat in rec.project.existing_features:
            evidence.append(ctx.ledger.add(f"Existing feature listed in client records: {feat}", source, "csv",
                                           0.7, extracted_text=feat))
        for issue in rec.issues:
            findings.append(Finding(category="data_quality", title=issue, confidence=1.0))
        return AgentResult(
            findings=findings,
            evidence=evidence,
            confidence=1.0,
            data={"record": rec.model_dump()},
            next_actions=["client_research", "repository"],
        )
