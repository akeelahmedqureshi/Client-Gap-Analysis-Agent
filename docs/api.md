# REST API

Interactive OpenAPI docs are served at `/docs`. Every endpoint except register, login, health and the
OAuth callback requires `Authorization: Bearer <jwt>`.

## Auth

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/auth/register` | — | Create an organization and its admin user; returns `access_token` |
| POST | `/api/auth/login` | — | Returns `access_token` |
| GET | `/api/auth/me` | any | Current user |
| POST | `/api/auth/users` | admin | Add a user (`email`, `password`, `role`, optional `job_function`) |
| POST | `/api/auth/change-password` | any | `{current_password, new_password}`. Signs out other sessions and returns a new token |

Login protection: after 5 consecutive failures the account is locked for 15 minutes (429), and each IP
gets 20 login/register attempts per 5 minutes (429). Tokens carry a version, so a password change,
reset, role change or deactivation invalidates existing sessions.

## Users and audit (admin)

| Method | Path | Description |
|---|---|---|
| GET | `/api/users` | Organization users (`is_active`, `locked`) |
| PATCH | `/api/users/{id}` | `{name?, role?, is_active?, job_function?}` (`job_function`: sales, business_development, product, technical, management; `""` clears it). The last active admin can't be demoted or deactivated (409) |
| POST | `/api/users/{id}/reset-password` | `{new_password}`. Sets a temporary password, signs the user out and unlocks the account |
| POST | `/api/users/{id}/unlock` | Clear a lockout |
| GET | `/api/audit?action=&limit=&offset=` | Audit log, newest first. `action` is a prefix filter, e.g. `auth.` |

## CSV → projects

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/uploads` | analyst | Multipart `file`. Validates and returns a preview (`records`, `column_mapping`, `errors`, `warnings`) |
| GET | `/api/uploads/{id}` | viewer | Preview again |
| POST | `/api/uploads/{id}/import` | analyst | `{rows?: int[], include_duplicates?: bool}`. Creates clients (de-duplicated by domain or name) and projects |
| GET | `/api/clients` | viewer | Clients with project counts |
| GET | `/api/clients/{id}` | viewer | Client detail: visible projects, the latest researched company profile, and the top 3 recommendations from the latest run of each project |
| GET | `/api/projects?client_id=` | viewer | Projects with their latest run |
| GET | `/api/projects/{id}` | viewer | One project |
| PUT | `/api/projects/{id}/repositories` | analyst | `{urls: [...]}`. Replaces the GitHub/GitLab repositories analysed in future runs; URLs are validated and normalized |
| GET | `/api/projects/{id}/access` | admin | `{restricted, member_ids}` |
| PUT | `/api/projects/{id}/access` | admin | `{restricted: bool, member_ids: [user ids]}`. Restricted projects, and their runs, evidence and reports, are visible only to admins and members; everyone else gets 404 |

## Analysis runs

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/runs/approval-preview?project_id=` | viewer | The gates this project will hit, with what, why, target and data analyzed |
| POST | `/api/runs` | analyst | `{project_id, approve_gates?: string[], scoring_weights?: {factor: weight}}`. Starts a run in the background |
| GET | `/api/runs?project_id=` | viewer | Runs list |
| GET | `/api/runs/{id}` | viewer | State: `status`, `agents{}`, `agent_details[]`, `approvals[]`, `has_report`, `monitor_id` (set for scheduled runs) |
| GET | `/api/runs/{id}/agents/{agent}` | viewer | The agent's full `AgentResult` |
| POST | `/api/runs/{id}/approvals/{approval_id}` | analyst | `{approve: bool}`. Resumes the run once no approvals are pending |
| POST | `/api/runs/{id}/resume` | analyst | Retry failed agents; completed agents are kept |
| GET | `/api/runs/{id}/evidence?source_type=&q=` | viewer | Evidence records |
| GET | `/api/runs/{id}/report` | viewer | `{title, content (structured), markdown}` |
| GET | `/api/runs/{id}/report.md` | export | Markdown download (audited) |
| GET | `/api/runs/{id}/report.pdf` | export | Client-ready PDF (A4, page numbers), rendered offline in headless Chromium. Returns 503 if no browser is installed. Each export is audited |

Each run and each agent report `usage`: LLM calls, prompt / completion / total tokens, cost in USD (as
reported by OpenRouter, or estimated from `CIP_LLM_PRICE_*`), tokens per model, web requests and search
queries.

Lifecycle (BRS 26.2–26.3):

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/runs` | analyst | 409 when the project already has an active (queued, running, waiting or paused) run; the message names that run |
| POST | `/api/runs/bulk` | analyst | `{project_ids[] (≤100), approve_gates?, scoring_weights?}` → `{runs[], skipped[{project_id, reason, run_id?}]}`: one independent run per project; projects with an active run are skipped. In-process runs execute at most `CIP_MAX_CONCURRENT_RUNS` (3) at a time; the rest wait as `queued` |
| POST | `/api/runs/{id}/cancel` | analyst | Stops the run: remaining stages are skipped (completed ones kept). In-process runs stop immediately, Celery runs before their next wave |
| POST | `/api/runs/{id}/pause` | analyst | Stops after the agents already running finish their step; `/resume` continues |
| POST | `/api/runs/{id}/resume` | analyst | Continues a paused or cancelled run and retries failed or skipped stages |
| POST | `/api/runs/{id}/rerun` | analyst | `{stages: [industry\|competitors\|opportunities\|sales\|outreach\|report\|<agent>]}` → a **new run version** (`parent_run_id`, `rerun_stages`) that reuses every other completed stage, its evidence and approval decisions, and re-runs the selected stages plus everything downstream. The earlier run is unchanged |
| GET | `/api/runs/rerun-stages` | viewer | The named refresh presets and their agents |

Run statuses: `queued`, `running`, `awaiting_approval`, `paused`, `cancelled`, `completed`, `completed_with_errors`, `failed`.

Agent statuses: `pending`, `running`, `awaiting_approval`, `completed`, `failed`, `skipped`.

## Monitoring and alerts

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/monitors` | viewer | Monitored projects the caller can see |
| GET | `/api/projects/{id}/monitor` | viewer | The project's monitor (404 if none) |
| PUT | `/api/projects/{id}/monitor` | analyst | `{enabled, frequency: daily\|weekly\|monthly, standing_approvals: [external_research, repository_access, client_report, large_repository_scan], min_severity: critical\|warning\|info, notify_emails: [...], webhook_url?: https URL ("" removes, omitted keeps), scoring_weights?, run_now?}`. The caller is recorded as the approver of the standing approvals. The webhook URL is stored encrypted and only returned masked |
| DELETE | `/api/projects/{id}/monitor` | analyst | Stop monitoring (alerts are kept) |
| POST | `/api/projects/{id}/monitor/run-now` | analyst | Start a scheduled-style run now. 409 while the previous monitored run is still in flight |
| POST | `/api/projects/{id}/monitor/test` | analyst | Send a sample alert to the configured channels; returns per-channel results |
| GET | `/api/alerts?unread_only=&project_id=&limit=` | viewer | Alerts (`changes`, `approval_needed`, `run_failed`) with their changes and delivery results |
| GET | `/api/alerts/unread-count` | viewer | `{count}` |
| POST | `/api/alerts/{id}/read`, `/api/alerts/read-all` | viewer | Mark read (shared across the organization) |
| GET | `/api/runs/{id}/changes` | viewer | `{baseline_run_id, changes[], summary}`: what changed since the previous completed run of the project. Each change has `kind`, `severity`, `title`, `detail` and `evidence_ids` from this run |

## Governance

"export" in the Role column means the organization's export policy applies: the caller needs at least
`export_min_role` and, when `export_job_functions` is set, one of those job functions (admins always pass).
Otherwise 403. Viewing results in the app is never gated.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/org/settings` | viewer | `{settings, can_export, job_functions}`. Settings: `export_min_role`, `export_job_functions`, `retention_days` (null = keep forever, else 7-3650), `retention_keep_latest`, `last_purge_at` |
| PUT | `/api/org/settings` | admin | Partial update; unknown keys or invalid values → 422. Audited |
| POST | `/api/org/retention/purge?dry_run=` | admin | Apply the retention policy now (`dry_run` lists what would go). The scheduler also applies it hourly. 409 without a policy |
| DELETE | `/api/runs/{id}` | analyst | Admins: any run; analysts: runs they started. Removes agent results, evidence, approvals, report, sales documents, review changes, alerts and notifications. 409 while in progress |
| DELETE | `/api/projects/{id}` | admin | The project with all of its runs, monitor and access list. 409 while a run is in progress |
| DELETE | `/api/clients/{id}` | admin | The client and all of its projects |
| GET | `/api/runs/{id}/export/{name}` | export | `matrix.csv`, `gaps.csv`, `opportunities.csv`, `recommendations.csv`, `evidence.csv` (UTF-8 with BOM; cells that would start a spreadsheet formula are prefixed with `'`) or `analysis.json` (every agent result, the evidence ledger and run metadata). Audited |
| GET | `/api/notifications?unread_only=&limit=` | viewer | Your in-app notifications (projects you can still see) |
| GET | `/api/notifications/unread-count` | viewer | `{count}` |
| POST | `/api/notifications/{id}/read`, `/api/notifications/read-all` | viewer | Mark read |
| GET/PUT | `/api/notifications/preferences` | viewer | `{scope: mine\|all, events: {event: {in_app, email}}}`. Events: `run_completed`, `run_failed`, `approval_needed`, `needs_review`, `outreach_ready` |

Deletions and purges are written to the audit log, which is never purged.

## Portfolio

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/portfolio?q=&industry=&status=&priority=high\|medium\|low&sort=score\|date\|name\|confidence` | viewer | From each visible project's latest completed run: `summary` (clients, projects, analysed, running, awaiting approval, needs review, failed, never analysed), `projects[]` (industry, status, quality, top priority, opportunity score, evidence coverage, dates; searchable, filterable, sortable), `top_opportunities[]`, `recurring_gaps[]`, `recurring_ai[]`, `recurring_automation[]`, `requested_capabilities[]` (app reviews), `industries[]`, `capability_demand[]` (knowledge-base records matched across clients) and `shared_case_studies[]` |

## Human review

Reviewers correct a finished run (BRS 17). Overrides key on stable labels (capability id, competitor
domain, gap name, recommendation name) so they survive re-runs; applying them creates a new run version.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/runs/{id}/review` | viewer | Overrides of the run: kind, target, value, note, who, when, `pending` / `applied` |
| PUT | `/api/runs/{id}/review` | analyst | `{kind, target_id, field?, value, note?}` (upserts by kind + target + field). Kinds: `capability_status` (value `available` / `partial` / `unknown` / `missing` — the only way to *confirmed missing*), `competitor` (`exclude`), `gap` (`reject` / `approve` / `rework`), `recommendation` (field `priority` / `phase` / `business_category` / `business_impact`) |
| DELETE | `/api/runs/{id}/review/{override_id}` | analyst | Remove a pending override |
| POST | `/api/runs/{id}/review/apply` | analyst | → `{run_id, applied, stages}`: a new run version with the overrides written into the reused stages and the affected stages re-run. Applied overrides carry over to every later version |

## Sales summary and outreach

The run's sales summary and outreach email are created from the agent results the first time they are
opened, then reviewed by people. Every change is versioned in `history`.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/runs/{id}/sales` | viewer | `{summary, outreach}`, each `{content, status: draft\|approved, version, history, edited}` |
| PATCH | `/api/runs/{id}/sales/summary` | analyst | `{conversation_angle?, next_step?, reviewer_notes?, note?}`; back to draft |
| POST | `/api/runs/{id}/sales/summary/approve` | analyst | `{note?}` |
| GET | `/api/runs/{id}/sales-summary.md` | export | Markdown export (internal-only items labelled) |
| PATCH | `/api/runs/{id}/outreach` | analyst | `{to?, subject?, body?, note?}`; re-runs the claim check, back to draft |
| POST | `/api/runs/{id}/outreach/regenerate` | analyst | `{instructions?}`: new LLM draft (template without an LLM), claim-checked |
| POST | `/api/runs/{id}/outreach/approve` | analyst | `{acknowledge_warnings?, note?}`. 409 while the draft mentions internal-only knowledge or security findings; other claim-check warnings need `acknowledge_warnings: true` |
| GET | `/api/runs/{id}/outreach.eml` | export | The email as an unsent `.eml` draft (file name ends in `-DRAFT` until approved) |

## Knowledge base

Internal capabilities, reusable solutions, previous projects and case studies (BRS 27). Viewers see
approved records only; analysts see everything except restricted records; admins see everything.

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/knowledge?q=&kind=&status=&industry=&technology=&tag=&ai=&automation=&include_archived=` | viewer | Search and filter records (archived hidden by default) |
| POST | `/api/knowledge` | analyst | `{kind: capability\|solution\|project\|case_study, title, summary?, details?, outcomes?, customer_name?, industries[], technologies[], project_types[], capability_tags[] (taxonomy ids or keywords), ai?, automation?, linked_ids[], visibility: internal\|client_facing, reference_allowed?, status: draft\|in_review (admins may create approved), note?}` |
| GET | `/api/knowledge/{id}` | viewer | One record (404 if not visible to the caller) |
| PATCH | `/api/knowledge/{id}` | analyst | Edit any field (+ `note`). An analyst's edit to an approved record sends it back to `in_review`. Only admins edit restricted or archived records |
| POST | `/api/knowledge/{id}/status` | analyst | `{status, note?}`. Analysts move draft ↔ in_review; approve, restrict, archive and restore need an admin |
| GET | `/api/knowledge/{id}/versions` | analyst | Full history: every version's snapshot, change, note, who and when |

Statuses: `draft`, `in_review`, `approved`, `restricted`, `archived`. Only `approved` records are used by
analyses; only approved `client_facing` records may be used in client-facing output.

## Source control connections

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/connections` | viewer | Connections (tokens are never returned) |
| POST | `/api/connections/token` | admin | `{provider: github|gitlab, host?, token}` |
| DELETE | `/api/connections/{id}` | admin | Remove a connection |
| GET | `/api/connections/{id}/repositories?q=` | analyst | Repositories the connected account can read (most recently pushed first), for the repository picker |
| GET | `/api/connections/{provider}/authorize` | admin | Returns `{authorize_url}` |
| GET | `/api/connections/{provider}/callback` | — | OAuth redirect target |

## Meta

- `GET /api/health`: LLM provider, model and search provider.
- `GET /api/meta/taxonomy`: the feature taxonomy.
- `GET /api/meta/scoring`: the default scoring weights.
