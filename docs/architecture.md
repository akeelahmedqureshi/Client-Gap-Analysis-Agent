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

`services/runner.py` dispatches runs according to `CIP_RUN_EXECUTOR`:

- **inline**: each analysis runs as an asyncio task in the API process. An approval that arrives
  mid-pass queues a re-run.
- **celery**: runs are queued to Celery workers (`cip/workers/celery_app.py`) through Redis. Each
  execution holds a Redis lock (`services/runlock.py`), which is renewed while the run executes and
  expires if the worker dies, so only one process ever executes a given run.
  - A start request that arrives while a run executes leaves a re-run flag. The holder honours the
    flag before releasing the lock, and the requester retries the lock after setting it, which closes
    the release race.
  - Tasks are acknowledged late and redelivered if a worker process dies.
  - Workers re-queue interrupted runs when they start.

Either way, a resumed run rebuilds its context from the database, so completed agents never re-run.

### Monitoring

`services/monitoring.py` re-runs monitored projects on a schedule:

```
scheduler tick ─▶ due monitor (claimed atomically) ─▶ run with standing approvals
run finishes   ─▶ services/changes.py diff vs previous completed run ─▶ alert ─▶ webhook / email
```

- **Scheduler.** A loop in every API process (`CIP_MONITOR_SCHEDULER_ENABLED`, every
  `CIP_MONITOR_POLL_SECONDS`). A due monitor is claimed by a conditional `UPDATE` on its `version`, so
  with several API processes the run still starts once. In celery mode the run itself goes to a worker as
  usual. A monitor whose previous run is still running or waiting for approval skips the cycle.
- **Standing approvals** become ordinary approval records on the new run, decided by the user who
  saved them. They are re-checked on every run: if that user was deactivated, demoted to viewer or lost
  access to a restricted project, they are not applied and the run waits for a human.
- **Change detection** runs after every completed run, scheduled or manual, and is stored on the run
  (`baseline_run_id`, `changes`). Competitors are matched by domain because their ids change per run.
  App changes are included: client rating moves and releases, new complaint themes, competitor app
  launches and rating moves. So are UX changes: the client's UX score, new or resolved issues, and
  practices competitors added to their websites.
  A category is compared only when its agent completed in both runs and inspected the same scope, so a
  skipped step never reads as "everything disappeared".
- **Alerts** are raised for scheduled runs only: `changes`, `approval_needed` or `run_failed`. Every alert
  is shown in the app; webhook and email notifications go out when the alert reaches the monitor's
  `min_severity`.

## Research layer

`connectors/research/web.py` (`WebFetcher`) is the only way agents reach the web:

- **Safety:** SSRF guard (public addresses only, re-checked after redirects), robots.txt and a body size cap.
- **Politeness:** at most `CIP_CRAWLER_DOMAIN_CONCURRENCY` requests per domain at once, spaced by
  `CIP_CRAWLER_DOMAIN_DELAY_SECONDS`.
- **Retries:** timeouts, connection errors, 429 and 5xx are retried with exponential backoff
  (`Retry-After` honoured up to 10 s). A spent web budget is never retried.
- **Failure states:** every fetch that still fails is recorded with its reason (`timeout`, `http_503`,
  `robots_disallowed`, `blocked_address`, `unreadable_pdf`…) on the run's usage meter, per agent. The
  Pipeline tab lists them. A 404 is an answer, not a failure.
- **PDFs:** a site's product, pricing, brochure or case-study PDFs (`CIP_CRAWLER_MAX_PDFS`) are read as
  text with pypdf, within size and page limits.
- **Shared cache** (`connectors/research/cache.py`): successful pages are stored per organization for
  `CIP_RESEARCH_CACHE_TTL_HOURS` and reused by every agent and run. Partial re-runs read fresh pages. Evidence
  taken from a cached page is dated when the page was really fetched (the orchestrator re-dates it per
  agent), so stale content is never presented as current. The scheduler deletes expired entries.
- **Domain checks** (`connectors/research/domain.py`) classify a client's site as reachable,
  redirected, parked, unreachable or blocked. They run in the upload preview and again at the start of
  client research.

## Configuration and reproducibility

`services/configuration.py` keeps four kinds of organization configuration as immutable versions
(`config_versions`):

- **analysis:** Top-N competitors, deep-analysis count, pages, PDFs, roadmap size, freshness thresholds,
  source-tier factors and the default scoring profile;
- **scoring_profile:** named profiles with factor weights, evidence-confidence weight, priority bands and
  horizon thresholds;
- **taxonomy:** the capability catalogue;
- **llm:** the default model and temperature, per-agent model and temperature, and prompt-text overrides
  for the prompts registered in `core/prompts.py`.

A new run records the versions in effect (`AnalysisRun.config`). `build_context` resolves exactly those
versions into the run's settings, `ScoringConfig`, `Taxonomy` and a `ConfiguredLLM` (prompt and model
overrides applied centrally; agents are unchanged). Resumes and partial re-runs keep the parent's
versions, so a configuration change never silently alters an existing analysis. The QA agent records the
versions, the effective prompt versions and the models in its reproducibility block.

**Source-quality tiers** (`core/source_quality.py`) are derived for every evidence item: 1 official
product, docs or pricing; 2 announcements and case studies; 3 trusted third parties; 4 publications; 5
search or aggregators. Prioritization multiplies a gap's confidence by the factor of its best-supporting
tier, and QA reports the tier mix and warns when recommendations rest mostly on tier-5 sources.

## Multi-language analysis

`core/language.py` labels every crawled page with its language: the declared `<html lang>`, or a stop-word
vote. `core/taxonomy_i18n.yaml` adds Spanish, French, German, Portuguese, Italian and Dutch keywords to every
capability, including in organization-edited taxonomies (matched by id). A non-English site therefore maps
onto the same capability ids, and its evidence keeps the verbatim quote in the original language.
`hreflang` alternates on a client page are evidence of localization. The extraction prompts ask the LLM to
quote in the source language and to answer in English. Client research reports the site's languages, the
report shows them, and QA warns when the main language has no keyword pack. The workflow, process and
positioning cue lists are English only.

## Data model

`Organization → Client → Project → AnalysisRun → {AgentExecution, Evidence, Approval, Report}`

Every tenant-owned row has an `org_id`, and every API query filters on it. Other tables:
`users` (role-based), `source_connections` (encrypted tokens), `oauth_states`, `csv_uploads` (raw
files kept in object storage under `storage/{org}/csv/`), `monitors` (one per project, encrypted
webhook URL), `alerts` and `notifications` (per user, with email delivery results). Users carry a
permission `role`, a `job_function` and `notification_prefs`; organizations carry governance
`settings` (export policy, retention).

### Deletion and retention

`services/governance.py` deletes a run, project or client together with everything derived from it.
SQLite does not enforce foreign-key cascades, so child rows are deleted explicitly. Later versions of a
deleted run lose their `parent_run_id` link but keep their `updated_at`, because retention ages runs by
it. With `retention_days` set, the monitoring scheduler purges finished runs older than that once an
hour, optionally keeping each project's latest completed run. Deletions and purges are audited, and the
audit log itself is never purged.

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

## Usage tracking and budgets

`core/usage.py` keeps a usage meter per run. The orchestrator makes it current for the run and tags each
agent's work with the agent's name, so the LLM client (tokens, model, cost), the web fetcher (every HTTP
request, including robots.txt and APIs) and the search wrapper (queries) record usage without extra
plumbing. Each agent's usage is stored with its result; the runs API sums them.

Budgets (`CIP_RUN_LLM_TOKEN_BUDGET`, `CIP_AGENT_LLM_TOKEN_BUDGET`, `CIP_RUN_WEB_REQUEST_BUDGET`) never
fail a run: an exhausted LLM budget makes the LLM report itself unavailable, so agents use their
deterministic fallbacks; an exhausted web budget makes further requests fail like an unreachable site.
The quality check lists every budget event. On resume, earlier stages' usage counts toward the budget.
