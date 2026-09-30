# Architecture

## Layers

```
┌───────────────────────── Research layer ─────────────────────────┐
│ WebFetcher/crawler · SearchProvider · CompanyResearchProvider     │
│ SourceControlProvider (GitHub, GitLab)                            │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌───────────────────────── Evidence layer ─────────────────────────┐
│ EvidenceLedger (per run) → `evidence` table                       │
│ Grounder: verifies LLM-cited URLs + quotes against fetched docs   │
└──────────────────────────────┬────────────────────────────────────┘
                               ▼
┌─────────────────────── Intelligence layer ───────────────────────┐
│ features · comparison · gaps · opportunities · roadmap · plans    │
│ (may only reference evidence ids that exist in the ledger)        │
└───────────────────────────────────────────────────────────────────┘
```

The LLM never jumps straight from search results to recommendations. Research agents write evidence,
and analysis agents reason over structured data that cites that evidence.

## Orchestration

`cip/agents/orchestrator.py` is the only component that decides what runs. Agents never call each
other.

- **Graph.** Each agent declares `requires` (hard dependencies: if one fails or is skipped, the agent
  is skipped too) and `after` (soft ordering: wait until the dependency finishes in any state). The
  graph is checked for unknown nodes and cycles at start-up.
- **Parallelism.** Every agent whose dependencies are satisfied runs concurrently. For example,
  website research and repository analysis run in parallel.
- **Contract.** `Agent.run()` must return an `AgentResult` (status, findings, evidence, confidence,
  next_actions, data, errors). Results are normalized to JSON, and finding evidence IDs are validated
  against the ledger.
- **Retries.** Each agent has `max_attempts`, with exponential backoff between attempts. A failed agent
  does not crash the run, and the run ends as `completed_with_errors`.
- **Approval gates.** `Agent.approval_needed(ctx)` returns an `ApprovalRequest` before the agent runs.
  An agent can also raise `AwaitingApproval` mid-run, which the repository agent does for large
  repositories. Independent branches keep running, and the run ends in `awaiting_approval` until a
  decision is made.
- **Resumability.** Every state transition is persisted through the `RunStore` port. When a run
  resumes (after an approval or a crash), `build_context` reloads the completed agents' results and the
  evidence ledger from the database, so completed work is never repeated.

Agent state, as exposed by `GET /api/runs/{id}`:

```json
{
  "run_id": "run_123",
  "project_id": "prj_456",
  "status": "running",
  "agents": {"client_research": "completed", "code_analysis": "running", "competitor_research": "pending"}
}
```

### Execution

`services/runner.py` runs each analysis as an in-process asyncio task and queues a re-run if an
approval arrives mid-pass. This is enough for the MVP. The `RunStore` / `build_context` split lets the
runner move to Celery (with Redis) or a workflow engine later without touching the agents.

## Data model

`Organization → Client → Project → AnalysisRun → {AgentExecution, Evidence, Approval, Report}`

Every tenant-owned row has an `org_id`, and every API query filters on it. Other tables:
`users` (role-based), `source_connections` (encrypted tokens), `oauth_states`, and `csv_uploads` (raw
files kept in object storage under `storage/{org}/csv/`).

Migrations live in `backend/migrations` (Alembic). Development mode also runs `create_all` on
start-up.

## Knowledge layer (next step)

PostgreSQL + pgvector is already the target database (the `docker-compose` image includes pgvector).
The next increment adds an `evidence_chunks` table with embeddings for semantic retrieval across
website content, repository documents, competitor research and past reports.

## LLM usage

`core/llm.py` provides an OpenRouter client (OpenAI-compatible `/chat/completions`) with these
properties:

- JSON-object response format, with the Pydantic JSON Schema embedded in the system prompt;
- validation of the reply against the schema, plus one repair round if it fails;
- retries with backoff on 429/5xx responses and no retries on other 4xx errors;
- secret redaction of every prompt.

When no API key is configured, `NullLLM` raises `LLMUnavailable`, and each agent falls back to its
deterministic logic: keyword/taxonomy matching, dependency rules, template narratives and plans.

The LLM is used only for:

- extraction, always grounded against sources;
- classification of competitors;
- narrative estimates, with factor adjustments limited to ±1;
- patch plans.

Prioritization itself is the deterministic weighted score in `core/scoring.py`.
