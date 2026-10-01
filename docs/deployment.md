# Deployment

Two supported layouts on a single Linux server:

- **A. Docker Compose**: PostgreSQL, the API and the web UI run as containers. An HTTPS proxy runs in
  front of them.
- **B. Without Docker**: Python, PostgreSQL and nginx are installed directly on the server, and
  systemd runs the API.

> **Where analyses run (`CIP_RUN_EXECUTOR`).**
>
> - **`inline`** (the default for a plain install): analyses run inside the API process. Keep the API
>   at one process (`--workers 1`), because run coordination and login rate limits are kept in
>   memory.
> - **`celery`** (what Docker Compose uses): analyses run in Celery workers through Redis. A Redis
>   lock guarantees one executor per run, and approvals granted mid-run trigger a re-run. Login rate
>   limits are shared in Redis. You can run several API processes (`CIP_API_WORKERS`) and scale the
>   workers (`docker compose up -d --scale worker=3`).
>
> **Restarts and crashes.** Analyses interrupted by a deploy or restart are resumed automatically.
> Completed agents are kept, and the agent that was running is re-run. The API does this on start-up
> (`CIP_RESUME_RUNS_ON_STARTUP`). In celery mode a worker does it on start-up too. The run lock
> expires 5 minutes after its worker dies, and a crashed worker process's task is redelivered.

The server needs outbound HTTPS access to `openrouter.ai`, `github.com` / `gitlab.com`, your search
API (Tavily or Brave), and the client and competitor websites.

## Required settings

Generate the secrets and keep a copy of `CIP_TOKEN_ENCRYPTION_KEY`. Losing it makes the stored
GitHub/GitLab tokens unreadable.

```bash
openssl rand -hex 32                                                    # CIP_JWT_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # CIP_TOKEN_ENCRYPTION_KEY
```

| Variable | Value |
|---|---|
| `CIP_ENVIRONMENT` | `production`. The API refuses to start with default secrets, and Alembic manages the schema. |
| `CIP_JWT_SECRET`, `CIP_TOKEN_ENCRYPTION_KEY` | The generated secrets above |
| `CIP_OPENROUTER_API_KEY`, `CIP_OPENROUTER_MODEL` | Your OpenRouter key and model slug. Check the slug at openrouter.ai/models. |
| `CIP_SEARCH_PROVIDER` + `CIP_TAVILY_API_KEY` / `CIP_BRAVE_API_KEY` | Needed for competitor discovery |
| `CIP_ALLOW_REGISTRATION` | `true` until the first admin signs up, then `false` |

See `.env.example` for the full list, including the login-protection limits.

---

## A. Docker Compose

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER   # then log in again
git clone https://github.com/akeelahmedqureshi/Client-Gap-Analysis-Agent.git && cd Client-Gap-Analysis-Agent
cp .env.example .env
```

Edit `.env`. Besides the required settings above, set these values, which `docker-compose.yml` reads:

```bash
CIP_PUBLIC_URL=https://intel.yourdomain.com
POSTGRES_PASSWORD=<strong password>
WEB_BIND=127.0.0.1:8080          # only reachable through the HTTPS proxy below
```

Start the stack:

```bash
docker compose up -d --build
docker compose logs -f api                 # migrations run automatically
curl http://127.0.0.1:8080/api/health
```

Add HTTPS with Caddy. Point the domain's DNS at the server first, and open ports 80 and 443.

```bash
sudo apt install -y caddy
printf 'intel.yourdomain.com {\n  reverse_proxy 127.0.0.1:8080\n}\n' | sudo tee /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Update to a new version:

```bash
git pull && docker compose up -d --build
```

Back up the database and storage:

```bash
docker compose exec db pg_dump -U cip cip > backup_$(date +%F).sql
docker run --rm -v client-gap-analysis-agent_storage:/s -v $PWD:/b alpine tar czf /b/storage_$(date +%F).tgz -C /s .
```

---

## B. Without Docker (Ubuntu 24.04)

These steps need Python ≥ 3.11. Ubuntu 22.04 ships Python 3.10, which is too old.

### 1. Install packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv postgresql nginx git certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

### 2. Create the database

```bash
sudo -u postgres psql -c "CREATE USER cip WITH PASSWORD 'STRONG_DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE cip OWNER cip;"
```

### 3. Create the app user, then install the code and dependencies

```bash
sudo useradd --system --create-home --home-dir /opt/cip --shell /bin/bash cip
sudo chmod 755 /opt/cip
sudo -iu cip
git clone https://github.com/akeelahmedqureshi/Client-Gap-Analysis-Agent.git app
mkdir -p /opt/cip/storage
python3 -m venv /opt/cip/venv
/opt/cip/venv/bin/pip install --upgrade pip
/opt/cip/venv/bin/pip install "/opt/cip/app/backend[browser]"
/opt/cip/venv/bin/playwright install chromium        # headless browser for JavaScript-heavy sites
exit
sudo /opt/cip/venv/bin/playwright install-deps chromium   # system libraries Chromium needs
sudo -iu cip
```

The browser is optional. Without it, set `CIP_BROWSER_RENDERING=never` and the crawler uses plain
HTTP only, which misses content on sites that render with JavaScript.

### 4. Configure `/opt/cip/app/backend/.env`

The API reads `.env` from its working directory.

```bash
CIP_ENVIRONMENT=production
CIP_DATABASE_URL=postgresql+asyncpg://cip:STRONG_DB_PASSWORD@localhost:5432/cip
CIP_STORAGE_DIR=/opt/cip/storage
CIP_CORS_ORIGINS=["https://intel.yourdomain.com"]
CIP_OAUTH_REDIRECT_BASE=https://intel.yourdomain.com
CIP_JWT_SECRET=...
CIP_TOKEN_ENCRYPTION_KEY=...
CIP_OPENROUTER_API_KEY=...
CIP_OPENROUTER_MODEL=openai/gpt-6-luna
CIP_SEARCH_PROVIDER=tavily
CIP_TAVILY_API_KEY=...
CIP_ALLOW_REGISTRATION=true
```

Lock the file down, run the migrations, and build the UI:

```bash
chmod 600 /opt/cip/app/backend/.env
cd /opt/cip/app/backend && /opt/cip/venv/bin/alembic upgrade head
cd /opt/cip/app/frontend && npm ci && npm run build
exit
```

### 5. Create the systemd service

Create `/etc/systemd/system/cip-api.service`:

```ini
[Unit]
Description=Client Intelligence Platform API
After=network.target postgresql.service

[Service]
User=cip
WorkingDirectory=/opt/cip/app/backend
ExecStart=/opt/cip/venv/bin/uvicorn cip.api.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start it and check that it responds:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now cip-api
curl http://127.0.0.1:8000/api/health
```

uvicorn trusts proxy headers only from `127.0.0.1` (the local nginx), so the client IPs used for
login rate limiting and the audit log can't be spoofed.

### 6. Configure nginx and HTTPS

Create `/etc/nginx/sites-available/cip`:

```nginx
server {
    listen 80;
    server_name intel.yourdomain.com;
    root /opt/cip/app/frontend/dist;
    client_max_body_size 12m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }

    location / {
        try_files $uri /index.html;
    }
}
```

Enable the site and add the certificate:

```bash
sudo ln -s /etc/nginx/sites-available/cip /etc/nginx/sites-enabled/ && sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d intel.yourdomain.com
```

### 7. Update to a new version

```bash
sudo -iu cip
cd /opt/cip/app && git pull
/opt/cip/venv/bin/pip install "./backend[browser]"
cd backend && /opt/cip/venv/bin/alembic upgrade head
cd ../frontend && npm ci && npm run build
exit
sudo systemctl restart cip-api      # interrupted analyses resume automatically
```

### Optional: background workers (celery mode)

Use this for more parallel analyses or several API processes.

```bash
sudo apt install -y redis-server
sudo -iu cip /opt/cip/venv/bin/pip install "/opt/cip/app/backend[browser,worker]"
```

Add to `.env`:

```bash
CIP_RUN_EXECUTOR=celery
CIP_REDIS_URL=redis://localhost:6379/0
```

Create `/etc/systemd/system/cip-worker.service`:

```ini
[Unit]
Description=Client Intelligence Platform worker
After=network.target redis-server.service postgresql.service

[Service]
User=cip
WorkingDirectory=/opt/cip/app/backend
ExecStart=/opt/cip/venv/bin/celery -A cip.workers.celery_app worker --loglevel=info --concurrency=2
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable the worker and restart the API:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now cip-worker && sudo systemctl restart cip-api
```

In this mode the API's `ExecStart` may use `--workers 2` or more.

### 8. Backups

Back up the database, `/opt/cip/storage` and `.env` daily:

```bash
sudo -u postgres pg_dump cip > /var/backups/cip_$(date +%F).sql
```

---

## After the first deploy

1. Open the site and choose **Create an organization**. You become the admin.
2. Set `CIP_ALLOW_REGISTRATION=false` and restart the API.
3. Add teammates under **Settings → Team members**.
   - Roles: viewer (read-only), analyst (upload, run analyses, approve steps), admin (everything).
   - Mark sensitive projects as **restricted** on the Projects page. Only admins and the listed members
     can see them.
4. Optional: connect GitHub/GitLab for private repositories, under Settings.
   - Personal access token: preferably fine-grained and read-only.
   - OAuth app: use the callback URL `https://intel.yourdomain.com/api/connections/github/callback`
     (or `/gitlab/`).
5. Review **Audit Log** (admin only) for sign-ins, lockouts, user and role changes, uploads, runs,
   approvals, connections and access changes.

## Security defaults

- After 5 failed sign-ins the account is locked for 15 minutes. An admin can unlock it, or reset the
  password, under Settings.
- Each IP address gets at most 20 sign-in/registration attempts per 5 minutes.
- Changing a password, resetting it, changing a role or deactivating a user signs that user out of
  all existing sessions.
- The organization must always keep at least one active admin.
