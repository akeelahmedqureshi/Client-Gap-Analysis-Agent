# REST API

Interactive OpenAPI docs are served at `/docs`. Every endpoint except register, login, health and the
OAuth callback requires `Authorization: Bearer <jwt>`.

## Auth

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/auth/register` | — | Create an organization and its admin user; returns `access_token` |
| POST | `/api/auth/login` | — | Returns `access_token` |
| GET | `/api/auth/me` | any | Current user |
| POST | `/api/auth/users` | admin | Add a user (`email`, `password`, `role`) |
| POST | `/api/auth/change-password` | any | `{current_password, new_password}`. Signs out other sessions and returns a new token |

Login protection: after 5 consecutive failures the account is locked for 15 minutes (429), and each IP
gets 20 login/register attempts per 5 minutes (429). Tokens carry a version, so a password change,
reset, role change or deactivation invalidates existing sessions.

## Users and audit (admin)

| Method | Path | Description |
|---|---|---|
| GET | `/api/users` | Organization users (`is_active`, `locked`) |
| PATCH | `/api/users/{id}` | `{name?, role?, is_active?}`. The last active admin can't be demoted or deactivated (409) |
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
| GET | `/api/runs/{id}/report.md` | viewer | Markdown download |
| GET | `/api/runs/{id}/report.pdf` | viewer | Client-ready PDF (A4, page numbers), rendered offline in headless Chromium. Returns 503 if no browser is installed. Each export is audited |

Run statuses: `queued`, `running`, `awaiting_approval`, `completed`, `completed_with_errors`, `failed`.

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
