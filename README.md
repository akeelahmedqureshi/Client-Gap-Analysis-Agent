# Client Intelligence & Product Gap Analysis Platform

A multi-agent platform that takes a CSV of historical client projects and produces an
**evidence-backed intelligence report** for each project. The report covers the client company,
the project's features, architecture and stack (from its website and GitHub/GitLab source), verified
competitors, a feature comparison matrix, gap analysis, scored opportunities, a phased roadmap and a
technical patch plan per recommendation.

LLM access goes through **OpenRouter**, and the model is configurable (`CIP_OPENROUTER_MODEL`, default
`openai/gpt-6-luna`).

```
CSV ─▶ CSV Intake ─┬─▶ Client/Website Research ─────────────┐
                   └─▶ GitHub/GitLab ─▶ Code Intelligence ───┤
                                                      Product Features
                                                            │
                                  Competitor Discovery + Verification + Profiling
                                                            │
             Feature Comparison ─▶ Gap Analysis ─▶ Opportunity & Prioritization
                                                            │
                                       Enhancement (patch) Planning ─▶ Report
```

The system has three strictly separated layers:

| Layer | What lives there | Rule |
|---|---|---|
| **Research** | web crawler, search, GitHub/GitLab connectors | collects raw material only |
| **Evidence** | `Evidence` records (claim, source URL, repo path + line, extracted text, confidence) | every fact is stored here before it is used |
| **Intelligence** | features, gaps, opportunities, roadmap, plans | may only cite evidence IDs that exist in the ledger |

LLM extractions must return a source URL and a verbatim quote for every fact. `core/grounding.py`
checks both against the pages actually fetched. Facts that cite an unknown source are dropped, and
facts whose quote can't be found are kept only at low confidence. Every finding carries a `basis` of
`evidence`, `inferred` or `estimate`, and the report labels AI estimates explicitly.

## What's implemented (MVP scope, §31 of the spec)

| # | Agent | Module |
|---|---|---|
| 1 | CSV Intake: validation, column detection, normalization, de-duplication, URL/domain extraction | `agents/csv_intake.py` |
| 2 | Client/Website Research: crawl, company profile, products, leadership, contact & social discovery | `agents/client_research.py` |
| 3 | GitHub/GitLab: metadata, tree, activity, key-file fetch, secret filtering | `agents/repository.py`, `connectors/source_control/` |
| 4 | Code Intelligence: languages, frameworks, DBs, cloud, CI/CD, AI usage, architecture, tech debt | `agents/code_analysis.py`, `core/code_scanner.py` |
| 5 | Product Features: website + code + CSV signals mapped to the taxonomy | `agents/product_features.py` |
| 6 | Competitor Research: discovery, website verification, classification, feature extraction | `agents/competitor_research.py` |
| 7 | Feature Comparison: client vs. competitors matrix | `agents/comparison.py` |
| 8 | Gap Analysis: missing, partial, technology, UX and AI gaps | `agents/gap_analysis.py` |
| 9 | Opportunity & Prioritization: transparent weighted scoring, phase assignment | `agents/prioritization.py`, `core/scoring.py` |
| 10 | Enhancement Planning: frontend/backend/DB/API/AI/infra/security/testing plan per recommendation | `agents/planning.py` |
| 11 | Report: Markdown and JSON report with an evidence appendix | `agents/reporting.py` |

Platform features:

- **Orchestrator** (`agents/orchestrator.py`) runs the agents as a DAG, with parallel stages, retries,
  hard and soft dependencies, and a persisted, resumable state per run ID. Completed agents are never
  re-run.
- **Human approval gates.** Four steps pause for approval: starting external research, accessing
  repositories, scanning a large repository (detected at runtime), and generating the client-facing
  report. Each request states what will be accessed, why, the target, and what data is analyzed.
  Gates can be pre-approved when a run starts. Rejecting a gate skips that step.
- **Security.** JWT auth with RBAC (admin / analyst / viewer) and per-organization data isolation.
  GitHub/GitLab tokens are Fernet-encrypted at rest and never returned by the API or shown to an LLM.
  A secret scanner skips sensitive files, and all LLM prompts are redacted as a last line of defense.
  The crawler has an SSRF guard, and only role-based contact emails are kept.
- **Provider abstractions.** `SourceControlProvider` has GitHub and GitLab implementations.
  `CompanyResearchProvider` has website and search implementations, plus a LinkedIn provider that works
  only through a licensed adapter and never scrapes. `SearchProvider` supports Tavily and Brave.
- **Normalized feature taxonomy** (`core/taxonomy.yaml`): 40+ features in 9 categories, each with
  keywords, code signals and default scoring factors.
- **Web UI** (React, TypeScript, Tailwind, React Query) with screens for Dashboard, Upload
  (validate → preview → import), Projects, Clients, Runs, Monitoring (alerts and schedules) and Run
  detail. Run detail has tabs for the pipeline, changes since the previous run, client, project,
  security, UX, competitors, pricing, apps, comparison, gaps, opportunity matrix, roadmap and patch plans,
  evidence explorer, and the report. A Settings screen manages connections and users.
- **CLI** for running an analysis without the API or a database.
- **Report export** as a client-ready PDF, plus Markdown and JSON.
- **Pricing analysis:** plans, prices, pricing models and practices for the client and competitors;
  market positioning; pricing gaps.
- **Architecture diagrams:** current vs. target (after the roadmap), in the UI, PDF and Markdown
  (Mermaid).
- **Passive security review:** site headers and HTTPS, cookie flags, OSV.dev dependency advisories,
  insecure code patterns, and a score and grade.
- **Deeper research:** schema.org company facts, job-board hiring signals, announcements, and
  open-source and review-site competitor sources.
- **UX deep dive:** the client's key pages and competitors' homepages.
  - Accessibility (WCAG), mobile layout and page speed, with markup evidence and WCAG references.
  - Conversion practices (calls to action, help, live chat, trust signals) compared with
    competitors.
  - Per-category scores, plus UX gaps with front-end patch plans.
- **App-store analysis:** the client's and competitors' iOS and Android apps.
  - Ownership is verified before an app is attributed to a company.
  - It reports ratings against competitors, release staleness, review themes with quotes, and
    features customers request.
  - These feed the app-quality gaps, the "Native mobile app" decision and prioritization.
- **Monitoring and alerts:** per-project schedules (daily, weekly or monthly) with standing
  approvals. Each run is compared with the previous one: new or dropped competitors, competitor feature
  evidence, price moves, gaps opened or closed, new or resolved security issues, client announcements
  and hiring. Alerts appear in the app and go to a Slack-compatible webhook or email.

Also built:

- headless-browser rendering for JavaScript-heavy sites;
- automatic OAuth token refresh;
- auto-resume of interrupted runs;
- login protection, user management, per-project access control and an audit log;
- a CI pipeline;
- optional Celery + Redis workers for scaling.

Planned for Phase 2/3 (not built yet): pgvector semantic retrieval, a LinkedIn licensed adapter,
and a deeper LLM evaluation set.

## Quick start

### Backend

```bash
cd backend
pip install -e ".[dev,browser]" && python -m playwright install chromium   # browser is optional
cp ../.env.example .env            # set CIP_OPENROUTER_API_KEY, CIP_JWT_SECRET, …
uvicorn cip.api.main:app --reload  # http://localhost:8000/docs
```

SQLite is used by default. For PostgreSQL, set
`CIP_DATABASE_URL=postgresql+asyncpg://…` and run `alembic upgrade head`.

### Frontend

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173 (proxies /api to :8000)
```

### Docker (PostgreSQL + API + UI)

```bash
cp .env.example .env && docker compose up --build   # UI on http://localhost:8080
```

### CLI

```bash
cd backend
python -m cip.cli validate ../examples/clients.csv
python -m cip.cli analyze ../examples/clients.csv --row 2 --out report.md           # asks at each gate
python -m cip.cli analyze ../examples/clients.csv --yes --github-token "$GITHUB_TOKEN"
```

## Configuration

All settings are environment variables with the `CIP_` prefix (see `backend/cip/config.py` and
`.env.example`). The main ones:

| Variable | Purpose |
|---|---|
| `CIP_OPENROUTER_API_KEY` | Enables LLM extraction, classification, narratives and plans. Without it the pipeline still runs using its deterministic logic only. |
| `CIP_OPENROUTER_MODEL` | OpenRouter model slug (default `openai/gpt-6-luna`; check the exact slug at openrouter.ai/models). |
| `CIP_SEARCH_PROVIDER` | `tavily`, `brave` or `none`, used for competitor discovery. |
| `CIP_TOKEN_ENCRYPTION_KEY` | Fernet key for stored OAuth tokens. Required in production. |
| `CIP_GITHUB_CLIENT_ID` / `…_SECRET`, `CIP_GITLAB_…` | OAuth apps. Personal access tokens can be added in Settings instead. |
| `CIP_MAX_COMPETITORS`, `CIP_CRAWLER_MAX_PAGES`, `CIP_REPO_MAX_FILES_FETCHED`, `CIP_ROADMAP_TOP_N` | Research budgets. |

Scoring weights can be overridden for each run from the start dialog or via the `scoring_weights` API
field.

## Tests

```bash
cd backend && python -m pytest       # fully offline (fake web, repos, search, LLM) (fake web, repos, search, LLM)
cd frontend && npm run build         # type-check and build
```

## Documentation

- [docs/architecture.md](docs/architecture.md): layers, orchestration, state, data model
- [docs/agents.md](docs/agents.md): each agent's inputs, outputs and evidence rules
- [docs/connectors.md](docs/connectors.md): GitHub, GitLab, web, search and LinkedIn providers
- [docs/security.md](docs/security.md): auth, isolation, secret handling, LLM safety
- [docs/api.md](docs/api.md): REST endpoints
- [docs/deployment.md](docs/deployment.md): server deployment, with or without Docker
