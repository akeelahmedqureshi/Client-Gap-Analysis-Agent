# Getting started: complete setup

This guide takes you from an empty machine to a working platform. Pick a path:

| Path | Use it for | Time |
|---|---|---|
| [A. Offline demo](#a-offline-demo-no-keys-no-internet) | Click through the whole product with simulated data. No API keys. | 10 min |
| [B. Local development](#b-local-development-setup) | Running real analyses on your laptop (SQLite, one process). | 20 min |
| [C. Full stack with Docker](#c-full-stack-with-docker) | PostgreSQL + Redis + background workers + UI, as in production. | 20 min |
| [D. Production server](deployment.md) | A server with HTTPS, with or without Docker. | 1 h |

Then follow [docs/testing.md](testing.md) to test the complete system.

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Git | any | getting the code |
| Python | **3.11+** | backend (paths A, B) |
| Node.js | **22** (20+ works) | web UI (paths A, B) |
| Docker + Compose plugin | 24+ | path C |
| PostgreSQL | 16 (pgvector image recommended) | optional in B, included in C |
| Redis | 7 | optional in B (Celery mode), included in C |

**Accounts and keys.** All are optional for the demo. For real analyses you need the first two:

| Key | Where to get it | Setting | What happens without it |
|---|---|---|---|
| OpenRouter API key | openrouter.ai → Keys | `CIP_OPENROUTER_API_KEY` | LLM steps are skipped. Deterministic extraction, scoring and reports still run, but are less rich. |
| Web search: Tavily or Brave | tavily.com / brave.com/search/api | `CIP_SEARCH_PROVIDER` + `CIP_TAVILY_API_KEY` / `CIP_BRAVE_API_KEY` | Competitors are discovered only from the CSV, the client's site and open-source/marketplace sources. |
| GitHub / GitLab token | GitHub → Settings → Developer settings → fine-grained token, read-only *Contents* + *Metadata* | Added in the UI under **Settings → Source control connections** | Only public repositories are analysed. |
| SMTP server | your mail provider | `CIP_SMTP_*` | Monitoring alerts go in-app and to webhooks only. |
| Slack incoming webhook | Slack → Apps → Incoming Webhooks | per project, in the **Monitor** dialog | No chat notifications. |

Check the exact model slug at openrouter.ai/models; the default is `openai/gpt-6-luna`.

## 2. Get the code

```bash
git clone -b claude/intelligent-brahmagupta-0azm81 \
    https://github.com/akeelahmedqureshi/Client-Gap-Analysis-Agent.git
cd Client-Gap-Analysis-Agent
```

All work so far is on the `claude/intelligent-brahmagupta-0azm81` branch. Once it is merged, clone
the default branch instead.

---

## A. Offline demo (no keys, no internet)

The demo runs the real API, database, agents, scheduler and UI. Only the outside world is simulated:
websites, search, GitHub, the App Store, Google Play and OSV.dev all use the test-suite's fakes.

```bash
# backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# UI (built once and served by the demo)
cd ../frontend && npm install && npm run build && cd ../backend

python scripts/demo_server.py              # add --client-app to give the fake client an iOS app with reviews
```

Open **http://localhost:8000**, then:

1. Choose **Create an organization**. You become its admin.
2. **Upload** `examples/clients.csv`, validate it, then import it.
3. **Projects**: on **ABC Patient Management**, choose **Analyze**, tick all three approvals, and start.
   Only this project is wired to the fake web.
4. Explore the run tabs. Then follow [testing.md §3](testing.md#3-offline-demo-walkthrough) for the
   full checklist, including monitoring and alerts:
   - `curl -X POST localhost:8000/demo/change` changes the fake world;
   - `curl -X POST localhost:8000/demo/due` triggers the scheduler.

The demo stores its database in a temporary folder; pass `--data-dir ./demo-data` to keep it. It
refuses to start with `CIP_ENVIRONMENT=production`.

---

## B. Local development setup

### B1. Backend

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,browser,worker]"     # browser: JS rendering, UX browser checks, PDF; worker: Celery mode
python -m playwright install chromium      # only if you installed the browser extra
cp ../.env.example .env
```

### B2. Configure `backend/.env`

Generate the two secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"                                   # CIP_JWT_SECRET
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CIP_TOKEN_ENCRYPTION_KEY
```

Minimum `.env` for real analyses:

```bash
CIP_ENVIRONMENT=development
CIP_JWT_SECRET=<generated>
CIP_TOKEN_ENCRYPTION_KEY=<generated>        # keep a copy: losing it makes stored repo tokens unreadable
CIP_OPENROUTER_API_KEY=<your key>
CIP_OPENROUTER_MODEL=openai/gpt-6-luna      # check the slug on openrouter.ai/models
CIP_SEARCH_PROVIDER=tavily                  # or brave / none
CIP_TAVILY_API_KEY=<your key>
CIP_CORS_ORIGINS=["http://localhost:5173"]
CIP_APP_BASE_URL=http://localhost:5173      # links in alert notifications
```

The database defaults to SQLite (`./cip.db`). In development the tables are created automatically.
Every other setting is listed, with comments, in `.env.example`.

### B3. Run

```bash
# terminal 1 — API (http://localhost:8000/docs)
cd backend && source .venv/bin/activate
uvicorn cip.api.main:app --reload

# terminal 2 — UI (http://localhost:5173, proxies /api to :8000)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173, choose **Create an organization**, and you are the admin.

### B4. Optional components

**PostgreSQL** instead of SQLite:

```bash
docker run -d --name cip-db -e POSTGRES_USER=cip -e POSTGRES_PASSWORD=cip -e POSTGRES_DB=cip \
    -p 5432:5432 pgvector/pgvector:pg16
# .env
CIP_DATABASE_URL=postgresql+asyncpg://cip:cip@localhost:5432/cip
cd backend && alembic upgrade head
```

**Background workers (Celery + Redis).** Use these when runs must survive API restarts or you run
several API processes.

```bash
docker run -d --name cip-redis -p 6379:6379 redis:7-alpine
# .env
CIP_RUN_EXECUTOR=celery
CIP_REDIS_URL=redis://localhost:6379/0
# terminal 3
cd backend && celery -A cip.workers.celery_app worker --loglevel=info --concurrency=2
```

**Private repositories.** Under **Settings → Source control connections**, paste a GitHub or GitLab
token. Or configure an OAuth app with these settings:
- `CIP_GITHUB_CLIENT_ID` / `CIP_GITHUB_CLIENT_SECRET`;
- callback URL `http://localhost:8000/api/connections/github/callback`;
- `CIP_OAUTH_REDIRECT_BASE=http://localhost:8000`.

**Email alerts.** Set `CIP_SMTP_HOST`, `CIP_SMTP_PORT`, `CIP_SMTP_USERNAME`, `CIP_SMTP_PASSWORD` and
`CIP_SMTP_FROM`.

**Feature switches**, all on by default:

| Setting | Turns off |
|---|---|
| `CIP_SECURITY_REVIEW_ENABLED` | the passive security review |
| `CIP_OSV_ENABLED` | dependency lookups on OSV.dev |
| `CIP_APP_STORE_ENABLED` | app-store analysis |
| `CIP_UX_REVIEW_ENABLED` | the UX review |
| `CIP_UX_BROWSER_CHECKS` | the UX review's browser checks |
| `CIP_MONITOR_SCHEDULER_ENABLED` | scheduled monitoring runs |
| `CIP_BROWSER_RENDERING=never` | JavaScript rendering of websites |

### B5. Command line (no UI, no database)

```bash
cd backend
python -m cip.cli validate ../examples/clients.csv
python -m cip.cli analyze ../examples/clients.csv --row 2 --out report.md      # asks at each approval gate
python -m cip.cli analyze ../examples/clients.csv --yes --github-token "$GITHUB_TOKEN" --json report.json
```

---

### Updating an existing installation

After pulling a new version, install any new dependencies and bring the database schema up to date:

```bash
cd backend
pip install -e ".[dev]"            # picks up new dependencies (e.g. pypdf for PDF sources)
alembic upgrade head               # PostgreSQL, or any database managed with Alembic
```

A database created without Alembic (the development SQLite file, which start-up creates with
`create_all`) can lack columns that newer code uses: `create_all` adds new tables but never new
columns. In development the server repairs this at start-up: it adds the missing columns and logs
`Database schema upgraded in place`. To do it explicitly (always back up first):

```bash
python scripts/upgrade_db.py --check   # list missing columns
python scripts/upgrade_db.py           # add them and record the schema as the newest migration
```

With `CIP_ENVIRONMENT=production` the server never changes the schema itself; it refuses to start and
names the missing columns, so run `alembic upgrade head` (or `scripts/upgrade_db.py`) first.

### When a client's website cannot be researched

If the Client tab says "No page of the client's website could be retrieved", check from the server, as the
user and with the environment the API runs with:

```bash
cd backend
python scripts/check_web.py https://client-domain.com
```

It fetches the site exactly as an analysis does and names the cause, with a hint:

| Cause | Usual fix |
|---|---|
| `tls certificate` | Update the server's CA certificates (`ca-certificates`, `pip install -U certifi`), or set `SSL_CERT_FILE` to your proxy's CA bundle |
| `network unreachable`, `no route to host`, `connection error`, `timeout` | Allow outbound HTTPS (port 443) from the server, or set `HTTPS_PROXY` / `NO_PROXY` for the API process |
| `proxy error` | Check the proxy address and that it allows the site |
| `unresolvable`, `dns error` | Fix the server's DNS, or behind an egress-only proxy set `CIP_CRAWLER_PROXY_RESOLVES_DNS=true` |
| `http 403`, `connection reset` | The site's bot protection blocks the crawler: try a browser-like `CIP_CRAWLER_USER_AGENT`, or analyse from another network |
| `robots disallowed` | The site asks crawlers not to fetch it; the platform respects that |

The same reasons appear per URL under **Fetch failures** on the run's Pipeline tab and in the server log.
Then start a new analysis of the project.

## C. Full stack with Docker

This runs PostgreSQL, Redis, the API (which applies migrations on start), a Celery worker, and the
UI behind nginx.

```bash
cp .env.example .env
```

Edit `.env`:

```bash
CIP_ENVIRONMENT=production                 # refuses to start with default secrets
CIP_JWT_SECRET=<generated>
CIP_TOKEN_ENCRYPTION_KEY=<generated>
CIP_PUBLIC_URL=http://localhost:8080       # your public URL on a server
POSTGRES_PASSWORD=<strong password>
CIP_OPENROUTER_API_KEY=<your key>
CIP_SEARCH_PROVIDER=tavily
CIP_TAVILY_API_KEY=<your key>
```

Then start it:

```bash
docker compose up -d --build
docker compose ps                          # db, redis, api, worker, web all running
curl http://localhost:8080/api/health
```

Open http://localhost:8080 and create the organization. Then set `CIP_ALLOW_REGISTRATION=false`
and run `docker compose up -d`, so nobody else can create an organization.

For a smaller image without Chromium, build with `--build-arg WITH_BROWSER=0`. You then lose
JavaScript rendering, the UX browser checks and PDF export.

For HTTPS, systemd, nginx, backups and updates on a real server, see
[deployment.md](deployment.md).

---

## Where to go next

- Test everything: [testing.md](testing.md).
- How it works: [architecture.md](architecture.md), [agents.md](agents.md),
  [connectors.md](connectors.md).
- Security model: [security.md](security.md). API reference: [api.md](api.md).
