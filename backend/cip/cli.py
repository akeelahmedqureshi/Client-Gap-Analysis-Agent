"""Command-line analysis without the API/database.

    python -m cip.cli analyze clients.csv --row 2 --out report.md          # prompts at each approval gate
    python -m cip.cli analyze clients.csv --yes --github-token $GITHUB_TOKEN
    python -m cip.cli validate clients.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from cip.agents.base import ApprovalRequest, RunContext
from cip.agents.csv_intake import parse_csv
from cip.agents.orchestrator import Orchestrator
from cip.config import get_settings
from cip.connectors.research.search import get_search_provider
from cip.connectors.research.web import WebFetcher
from cip.core.evidence import EvidenceLedger
from cip.core.llm import get_llm
from cip.core.schemas import AgentStatus


class ConsoleStore:
    def __init__(self) -> None:
        self.pending: list[tuple[str, ApprovalRequest]] = []

    async def set_run_status(self, run_id, status, error=None):
        print(f"[run] {status}" + (f" — {error}" if error else ""), file=sys.stderr)

    async def set_agent_status(self, run_id, agent, status, result=None, error=None, attempts=None):
        extra = f" ({len(result.findings)} findings, {len(result.evidence)} evidence)" if result else ""
        print(f"  [{agent}] {status.value}{extra}" + (f" — {error}" if error else ""), file=sys.stderr)

    async def save_evidence(self, run_id, evidence):
        pass

    async def request_approval(self, run_id, agent, request):
        self.pending.append((agent, request))


def _ask(req: ApprovalRequest) -> bool:
    print(f"\n=== Approval required: {req.title} ===\n  Target: {req.target}\n  What:   {req.what}\n"
          f"  Why:    {req.why}\n  Data:   {req.data_analyzed}", file=sys.stderr)
    return input("Approve? [y/N] ").strip().lower() in ("y", "yes")


async def analyze(args: argparse.Namespace) -> int:
    parsed = parse_csv(open(args.csv, "rb").read())
    if not parsed.valid:
        print("\n".join(parsed.errors), file=sys.stderr)
        return 2
    rec = next((r for r in parsed.records if r.row_number == args.row), None) if args.row else parsed.records[0]
    if rec is None:
        print(f"Row {args.row} not found", file=sys.stderr)
        return 2
    settings = get_settings()

    async def token_resolver(provider: str, host: str) -> str | None:
        return args.github_token if provider == "github" else args.gitlab_token

    ctx = RunContext(run_id="cli", project_id=f"row-{rec.row_number}", record=rec, ledger=EvidenceLedger(),
                     llm=get_llm(settings), settings=settings, fetcher=WebFetcher(),
                     search=get_search_provider(settings), token_resolver=token_resolver)
    print(f"Analyzing {rec.client.name} — {rec.project.name} (LLM: "
          f"{settings.openrouter_model if settings.llm_enabled else 'disabled'})", file=sys.stderr)
    store = ConsoleStore()
    orch = Orchestrator(store)
    statuses: dict[str, AgentStatus] = {}
    while True:
        status = await orch.run(ctx, statuses)
        if status != "awaiting_approval":
            break
        requests, store.pending = store.pending, []
        progressed = bool(requests)
        for agent, req in requests:
            if args.yes or _ask(req):
                ctx.approvals.add(req.gate)
            else:
                statuses[agent] = AgentStatus.SKIPPED  # rejected: skip the agent and its hard dependents
        if not progressed:
            break
    if "report" in ctx.outputs:
        md = ctx.outputs["report"].data["markdown"]
        if args.out:
            open(args.out, "w").write(md)
            print(f"Report written to {args.out}", file=sys.stderr)
        else:
            print(md)
        if args.json:
            open(args.json, "w").write(json.dumps(ctx.outputs["report"].data["report"], indent=2, default=str))
    return 0 if status.startswith("completed") else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="cip")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="Run the full analysis for one CSV row")
    a.add_argument("csv")
    a.add_argument("--row", type=int, help="CSV line number (header is line 1); default: first record")
    a.add_argument("--out", help="Write Markdown report to this file")
    a.add_argument("--json", help="Write structured report JSON to this file")
    a.add_argument("--yes", action="store_true", help="Approve all gates non-interactively")
    a.add_argument("--github-token")
    a.add_argument("--gitlab-token")
    v = sub.add_parser("validate", help="Validate and preview a CSV")
    v.add_argument("csv")
    args = p.parse_args()
    if args.cmd == "validate":
        parsed = parse_csv(open(args.csv, "rb").read())
        print(json.dumps({"valid": parsed.valid, "errors": parsed.errors, "warnings": parsed.warnings,
                          "column_mapping": parsed.column_mapping,
                          "records": [r.model_dump() for r in parsed.records]}, indent=2))
        sys.exit(0 if parsed.valid else 2)
    sys.exit(asyncio.run(analyze(args)))


if __name__ == "__main__":
    main()
