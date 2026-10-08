# Testing the complete system

Test in four layers, from fastest to most realistic. Each layer builds confidence the next one
can't: automated tests prove the rules, the offline demo proves the product flow, the API smoke
test proves a deployment, and real runs prove the quality of results on real companies.

| Layer | What it proves | Needs | Time |
|---|---|---|---|
| [1. Automated tests](#1-automated-tests) | Every agent, rule, security control and API endpoint behaves as specified | Python (+ optional Chromium, Redis) | 1–2 min |
| [2. API smoke test](#2-api-smoke-test) | A running instance works end to end: upload → analysis → report | A running API | 1–15 min |
| [3. Offline demo walkthrough](#3-offline-demo-walkthrough) | The whole UI: every tab, approvals, roles, monitoring and alerts | The demo server | 30 min |
| [4. Real-world acceptance test](#4-real-world-acceptance-test) | Result quality on real clients, real repos, real LLM | API keys, a deployment | 1–2 h |

Setup for each environment is in [getting-started.md](getting-started.md).

---

## 1. Automated tests

```bash
cd backend && source .venv/bin/activate
python -m pytest                              # all tests, fully offline
ruff check cip tests --select F,E9            # lint (what CI runs)
cd ../frontend && npm run build               # TypeScript type-check + production build
```

### Running parts of the suite

```bash
python -m pytest tests/test_pipeline.py -v                 # one file
python -m pytest -k "monitor or alert" -v                  # by name
python -m pytest -x --lf                                   # stop at first failure / re-run last failures
python -m pytest -rs                                       # show why tests were skipped
```

### Tests that need optional pieces

These tests skip themselves, with a reason, when their dependency is missing:

| Tests | Need | Install |
|---|---|---|
| JavaScript rendering, PDF export, UX browser audit | Chromium | `pip install -e ".[browser]" && python -m playwright install chromium` |
| Distributed run lock, shared rate limits | Redis | `redis-server` on the PATH, or `CIP_TEST_REDIS_URL=redis://…` |
| Celery dispatch | Celery | `pip install -e ".[worker]"` |

CI installs all three, so nothing is skipped there.

### Database migrations

```bash
cd backend
CIP_DATABASE_URL=sqlite+aiosqlite:///./migration-check.db alembic upgrade head
CIP_DATABASE_URL=sqlite+aiosqlite:///./migration-check.db alembic downgrade base
CIP_DATABASE_URL=sqlite+aiosqlite:///./migration-check.db alembic upgrade head
CIP_DATABASE_URL=sqlite+aiosqlite:///./migration-check.db alembic check     # models and migrations agree
rm migration-check.db
```

For PostgreSQL, run the same commands with
`CIP_DATABASE_URL=postgresql+asyncpg://cip:cip@localhost:5432/cip_test`.

### What the suite covers

| Area | Test files |
|---|---|
| CSV intake: column detection, normalisation, validation, duplicates | `test_csv_intake.py` |
| Orchestrator: DAG, approvals, retries, resume, agent contract, full pipeline | `test_pipeline.py` |
| Web crawler, SSRF guard, robots.txt, search, source control | `test_connectors.py` |
| JavaScript rendering, with in-browser SSRF blocking | `test_browser.py` |
| Code scanner, dependency parsing, secret scanning and redaction | `test_code_scanner.py`, `test_security.py` |
| LLM client, grounding (source URL + verbatim quote) | `test_llm.py` |
| Scoring and taxonomy | `test_scoring_and_taxonomy.py` |
| Company enrichment, hiring, announcements | `test_enrichment.py` |
| Competitor sources, pricing | `test_competitor_sources.py`, `test_pricing.py` |
| Security review | `test_security_review.py` |
| App stores | `test_appstore.py` |
| UX review, including real-Chromium contrast, layout and timing checks | `test_ux.py` |
| Architecture diagrams, PDF export | `test_architecture.py`, `test_report_pdf.py` |
| API end to end: auth, tenant isolation, RBAC, upload → run → report | `test_api.py`, `test_projects_api.py` |
| Login lockout, sessions, users, project access, audit log | `test_security_api.py` |
| Token encryption and OAuth refresh | `test_tokens.py` |
| Redis run lock, Celery mode, API start-up without Redis | `test_runlock.py` |
| Monitoring: change detection, scheduler claims, standing approvals, alerts, notifications | `test_monitoring.py` |

---

## 2. API smoke test

`backend/scripts/api_smoke_test.sh` drives a running instance through the whole flow over HTTP:
1. health check;
2. register a new organization;
3. upload and import the CSV;
4. preview approvals and start an analysis with every approval granted;
5. wait for it to finish;
6. check gaps, recommendations, evidence and changes, and download the Markdown and PDF reports.

```bash
cd backend
# against the offline demo or a local dev API
BASE=http://localhost:8000 ./scripts/api_smoke_test.sh
# against a deployment, with your own CSV and an existing analyst account
BASE=https://intel.yourdomain.com CSV=my-clients.csv PROJECT="My Project" \
  LOGIN=1 EMAIL=analyst@yourco.com PASSWORD='…' ./scripts/api_smoke_test.sh
```

Without `LOGIN=1` the script creates a new organization, so only use that mode on test instances
that allow registration. Expected end: `== OK — run completed`, at least one gap and
recommendation, and every evidence record with a source URL. On the offline demo the PDF line says
"unavailable" unless Chromium is installed.

---

## 3. Offline demo walkthrough

Start the demo (see [getting-started.md § A](getting-started.md#a-offline-demo-no-keys-no-internet)),
open http://localhost:8000 and work through each step. Expected values below are for
`scripts/demo_server.py` without `--client-app`, right after start (before `/demo/change`).

### 3.1 Onboarding and upload

- [ ] **Create an organization**: you land on the Dashboard as admin.
- [ ] **Upload** `examples/clients.csv`, then **Validate**: 3 valid records, no errors, and the
      detected column mapping is shown.
- [ ] **Import**: 3 projects created. **Clients** lists ABC Healthcare, Northwind Logistics and
      Contoso Retail.

### 3.2 Approvals (human gates)

- [ ] **Projects → ABC Patient Management → Analyze**: the dialog lists three gates, each with what,
      why, target and data:
      - **Start external research**;
      - **Access source code repositories** (it mentions OSV.dev);
      - **Generate client-facing report**.
- [ ] Start **without** ticking anything. The run pauses at *awaiting approval* for external research
      and repository access.
- [ ] **Reject** external research and **approve** repository access.
      - Client research, competitor research, apps and UX show as *skipped*, not failed. No web
        search is made and no client or competitor website is contacted.
      - Code analysis and the dependency and code part of the security review still run.
- [ ] The report gate then asks for approval. Approve it: the run completes and the Report tab
      fills in.

### 3.3 A full run (all gates approved) — check every tab

Start a second analysis of ABC Patient Management with all three gates ticked.

| Tab | Expected (demo data) |
|---|---|
| Pipeline | 15 agents completed, each with confidence, findings and evidence counts |
| Changes | Compares with the earlier run |
| Client | Legal name *ABC Healthcare Holdings Inc.*, HQ Austin TX, founded 2012, leadership (CEO, CTO), 4 open roles with an *AI / Machine learning* hiring signal, product announcement *Introducing SMS reminders* |
| Project | React, Express, PostgreSQL, Stripe…, an architecture style, technical-debt indicators, and the committed secret detected and redacted |
| Security | **Grade D (42/100)**: HTTP not redirected to HTTPS, missing headers, a cookie without flags, the `express` advisory GHSA-test-0001 (CVE-2099-0001), insecure code patterns with file/line links |
| UX | Client **80/100** vs competitors **99**. Issues: no call to action, no mobile viewport (4 pages), missing `lang`, 2 images without alt text, 1 unlabelled field. Practice matrix: MediBook has live chat. Note: "Browser checks … did not run: the web is simulated" |
| Competitors | MediBook and ClinicFlow verified; *Random News* rejected (feature overlap 0%) |
| Pricing | Client 79 USD vs market median 39 USD, so **above** market; gaps *Free trial*, *Entry price above market*… |
| Apps | No client app (the unrelated "ABC Kids Learning" is not attributed). MediBook iOS 4.6★ and Android 4.4★, ClinicFlow iOS 4.2★ |
| Comparison | Feature matrix (✅ / 🟡 / ❌) for client vs competitors |
| Gaps | About 30 gaps of every type (missing, partial, ux, ai, technology, pricing, security), each with confidence and evidence |
| Opportunities | Scores with the factor breakdown. Estimates are labelled *ESTIMATE*. Changing the weights in the start dialog changes the order |
| Roadmap | Phases 1–4; patch plans; architecture diagram with a current/target toggle |
| Evidence | Every claim has a source URL (website, GitHub file + line, App Store, advisory…). Search and filter work |
| Report | Full Markdown report. **Download PDF** (needs Chromium) and **Markdown** both work |

- [ ] Click a few **“N sources”** buttons on different tabs. Each opens the claim, source and quoted
      text.
- [ ] Restart with `python scripts/demo_server.py --client-app`, then re-run:
      - the **Apps** tab shows the client's 3.1★ app, a *Crashes & bugs* theme with quotes in which
        phone numbers and emails are masked, and a request for *SMS notifications* (2 reviews);
      - **Gaps** no longer has *Native mobile app*, but has *Mobile app stability (crashes & bugs)*.

### 3.4 Knowledge base and capability matching

- [ ] **Knowledge Base → Add record**: type *Reusable solution*, title *Workflow automation platform*,
      industry *Healthcare*, technologies *React, Node.js*, capability *AI workflow automation / agents*,
      tick *Automation capability* and *May be used in client-facing output*, then **Save and approve**.
      The record shows *approved* and *Usable in client-facing output*.
- [ ] Add a second record as a **draft** tagged *AI assistant / chatbot*. As an analyst, **Submit for
      review**: there is no **Approve** button. Edit an approved record as the analyst: it goes back to
      *in review*. **Show history** lists every version with who and when.
- [ ] Re-run ABC Patient Management. The **Our Fit** tab matches *AI workflow automation / agents* to
      the approved record with reasons (required capability, same industry, the client's technology). The
      draft record is never matched; *AI assistant / chatbot* is under *No internal match*.
- [ ] **Restrict** a record as admin: analysts and viewers no longer see it, and new runs ignore it.
- [ ] **Sales** tab: pain points (app reviews and website issues; pricing and security marked *internal
      only*), top 3 competitive gaps with sources, top 3 improvements, AI / automation / cost / revenue
      opportunities, conversation angle, your capabilities and next step. **Edit**, **Approve**, **Export**.
- [ ] The outreach email names only analysed competitors, says *could not find publicly* rather than
      *you lack*, and uses only client-facing records. Add “we cut costs by 45%” and **Save edits**: a
      warning appears and approval asks you to confirm. Add an internal-only record's title: **Approve**
      is disabled. **Regenerate** with an instruction, then **Approve** and **Download .eml**.

### 3.5 Monitoring and alerts

- [ ] **Projects → ABC Patient Management → Monitor**:
      1. tick all standing approvals;
      2. webhook `https://hooks.slack.test/services/T0/B0/x`;
      3. notify for *every change*;
      4. **Start monitoring**.
      The row shows *⟳ monitored weekly*. **Monitoring** lists the project with its next run and
      the webhook masked as `https://hooks.slack.test/…`.
- [ ] Change the world and make the monitor due:
      ```bash
      curl -X POST localhost:8000/demo/change
      curl -X POST localhost:8000/demo/due
      ```
- [ ] Within ~10 s the **Monitoring** menu shows an unread badge. The alert reads *“5 change(s)
      since the last analysis”*:
      - ClinicFlow cut its entry price 49 → 39 USD;
      - MediBook new evidence of Invoicing;
      - New gap: Invoicing;
      - New product announcement: Introducing video visits;
      - MediBook new evidence of Online payments.
- [ ] Open the run: it has a *⟳ scheduled* badge. Its **Changes** tab lists the same changes, each
      with sources.
- [ ] `curl localhost:8000/demo/webhooks` shows the captured Slack-format message.
- [ ] Edit the monitor and untick *Generate client-facing report*, then run `/demo/due` again. The
      scheduled run pauses and raises an *approval needed* alert. **Run now** returns *409* while that
      run waits.

### 3.6 Roles, isolation and audit

- [ ] **Settings → Add team member**: add a *viewer* and an *analyst*. In a private window, sign in
      as the **viewer**: everything is visible, but there are no Analyze, Repos or Monitor buttons.
      Pending approvals say *Waiting for an analyst or admin to decide*.
- [ ] As admin, **Projects → Access**: restrict ABC Patient Management to the analyst only. The
      viewer no longer sees it, and opening one of its run URLs directly shows *not found*.
- [ ] Sign out and create a **second organization**: it sees no clients, projects, runs, monitors or
      alerts from the first. Pasting a run URL from the first organization shows *not found*.
- [ ] Sign in with a wrong password 5 times: the account is locked for 15 minutes. An admin can
      **Unlock** it in Settings.
- [ ] **Audit Log** (admin) shows the sign-ins, the lockout, the upload, the run starts, approvals and
      rejections, access changes, monitor changes and `monitor.run_started`.
- [ ] As a user, change your password: your other sessions are signed out.

---

## 4. Real-world acceptance test

Run this on a deployment configured with your OpenRouter key and a search provider ([getting-started
§ B or C](getting-started.md)).

### 4.1 Pre-flight

```bash
curl -s https://<your-host>/api/health
# {"status":"ok","llm":{"provider":"openrouter","model":"…","enabled":true},"search_provider":"tavily"}
```

- `llm.enabled` must be `true`, and the model must be the slug you expect.
- If the first run's LLM steps fail with *model not found*, fix `CIP_OPENROUTER_MODEL` and use
  **Retry failed agents**. Completed agents are not re-run.

### 4.2 Prepare a test CSV

Use 2–3 **clients you know well**, so you can judge the results:
- one with a public website only;
- one with a public GitHub repository;
- if possible, one with a private repository.

Required: **Client Name** and **Project Name**, plus **Project URL** or a client email domain.
Recommended: Description, Industry, Technology, Repository URL and Existing Features. Column names
are detected automatically (see `examples/clients.csv`).

### 4.3 Acceptance checklist

For each project, start an analysis with all gates approved and check:

| # | Check | Pass when |
|---|---|---|
| 1 | Run completes | Status *completed*, or *completed_with_errors* with an explained agent error, in roughly 2–10 min |
| 2 | Company facts | Description, industry, HQ and products match what you know. Wrong facts are traceable to a source you can open |
| 3 | Grounding | Open 10 random evidence items across tabs. Each source URL loads, and the quoted text appears on that page or in that file |
| 4 | Competitors | At least 2 relevant, verified competitors. Irrelevant candidates appear under *rejected* with a reason |
| 5 | Repository | Technology stack and architecture match the code. No `.env` or key files were fetched (*skipped sensitive files*). Any secret found shows as redacted |
| 6 | Gaps | You agree with most of the top 10 gaps. Every gap cites evidence |
| 7 | Prioritization | The score breakdown explains the order. LLM adjustments are at most ±1 per factor and labelled *estimate* |
| 8 | Security / UX / Apps / Pricing tabs | Findings are plausible for the real site. UX browser checks ran (no "did not run" note) |
| 9 | Report | The PDF downloads, reads as client-ready, and its citations [E#] resolve in the appendix |
| 10 | Private repo | After adding a token in **Settings → Source control connections** and picking the repo with **Repos**, a new run analyses it |
| 11 | Monitoring | **Send test** reaches your Slack webhook or email. **Run now** produces a second run whose **Changes** tab compares it with the first |
| 12 | Resilience (Celery mode) | `docker compose restart worker` during a run: the run continues and completed agents are not repeated |
| 13 | Isolation | Repeat § 3.6 on the deployment |

Record where results were wrong, and why. Those cases become the LLM evaluation set (roadmap item
#52).

### 4.4 Useful commands while testing

```bash
docker compose logs -f api worker                # Docker: follow the API and the worker
docker compose exec db psql -U cip -c "select id, status, error from analysis_runs order by created_at desc limit 5;"
journalctl -u cip-api -f                         # without Docker (systemd service from deployment.md)
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `llm.enabled: false` in `/api/health` | `CIP_OPENROUTER_API_KEY` is not set in the environment the API runs in (`backend/.env` for local runs, the root `.env` for Docker) |
| Few or no competitors | Set `CIP_SEARCH_PROVIDER` and its key; add competitor names in the CSV notes |
| Run stuck in *queued* (Celery mode) | No worker running, or `CIP_REDIS_URL` differs between API and worker |
| Run stuck in *awaiting approval* | Open the run and decide the pending approval. For monitors, check the standing approvals and that the approver is still active |
| PDF export returns 503 | Install the browser extra and Chromium on the API host (Docker: build with `WITH_BROWSER=1`, the default) |
| UX note "browser checks did not run" | Same: Chromium missing, or `CIP_UX_BROWSER_CHECKS=false` |
| Private repository shows "private or does not exist" | Add a token with read access under Settings → Source control connections |
| API refuses to start in production | Set `CIP_JWT_SECRET` and `CIP_TOKEN_ENCRYPTION_KEY` to non-default values |
| Login says "locked" | Wait 15 minutes, or an admin unlocks the account in Settings |
| No alert emails | `CIP_SMTP_HOST` not set (the Monitoring page says *email not configured on server*) |
