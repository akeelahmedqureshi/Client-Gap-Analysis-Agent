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

## CSV → projects

| Method | Path | Role | Description |
|---|---|---|---|
| POST | `/api/uploads` | analyst | Multipart `file`. Validates and returns a preview (`records`, `column_mapping`, `errors`, `warnings`) |
| GET | `/api/uploads/{id}` | viewer | Preview again |
| POST | `/api/uploads/{id}/import` | analyst | `{rows?: int[], include_duplicates?: bool}`. Creates clients (de-duplicated by domain or name) and projects |
| GET | `/api/clients` | viewer | Clients with project counts |
| GET | `/api/projects?client_id=` | viewer | Projects with their latest run |
| GET | `/api/projects/{id}` | viewer | One project |

## Analysis runs

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/runs/approval-preview?project_id=` | viewer | The gates this project will hit, with what, why, target and data analyzed |
| POST | `/api/runs` | analyst | `{project_id, approve_gates?: string[], scoring_weights?: {factor: weight}}`. Starts a run in the background |
| GET | `/api/runs?project_id=` | viewer | Runs list |
| GET | `/api/runs/{id}` | viewer | State: `status`, `agents{}`, `agent_details[]`, `approvals[]`, `has_report` |
| GET | `/api/runs/{id}/agents/{agent}` | viewer | The agent's full `AgentResult` |
| POST | `/api/runs/{id}/approvals/{approval_id}` | analyst | `{approve: bool}`. Resumes the run once no approvals are pending |
| POST | `/api/runs/{id}/resume` | analyst | Retry failed agents; completed agents are kept |
| GET | `/api/runs/{id}/evidence?source_type=&q=` | viewer | Evidence records |
| GET | `/api/runs/{id}/report` | viewer | `{title, content (structured), markdown}` |
| GET | `/api/runs/{id}/report.md` | viewer | Markdown download |

Run statuses: `queued`, `running`, `awaiting_approval`, `completed`, `completed_with_errors`, `failed`.

Agent statuses: `pending`, `running`, `awaiting_approval`, `completed`, `failed`, `skipped`.

## Source control connections

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/connections` | viewer | Connections (tokens are never returned) |
| POST | `/api/connections/token` | admin | `{provider: github|gitlab, host?, token}` |
| DELETE | `/api/connections/{id}` | admin | Remove a connection |
| GET | `/api/connections/{provider}/authorize` | admin | Returns `{authorize_url}` |
| GET | `/api/connections/{provider}/callback` | — | OAuth redirect target |

## Meta

- `GET /api/health`: LLM provider, model and search provider.
- `GET /api/meta/taxonomy`: the feature taxonomy.
- `GET /api/meta/scoring`: the default scoring weights.
