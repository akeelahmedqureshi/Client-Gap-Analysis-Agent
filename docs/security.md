# Security

## Authentication and authorization

- Access tokens are JWTs (HS256) that carry `sub`, `org` and `role`. Passwords are hashed with bcrypt.
- Roles:
  - `viewer`: read-only;
  - `analyst`: upload, start runs, approve gates;
  - `admin`: everything above, plus source-control connections and users.
- Self-service registration creates a new organization. Disable it with `CIP_ALLOW_REGISTRATION=false`.
- In production the app refuses to start with the default `CIP_JWT_SECRET`, and a
  `CIP_TOKEN_ENCRYPTION_KEY` is required.

## Data isolation

`Organization → Client → Project → Analysis`. Every row owned by a tenant carries `org_id`, and every
endpoint checks it. A resource that belongs to another organization returns 404, the same response as
a missing one, so its existence isn't leaked.

Inside an organization, an admin can mark a project **restricted**. Only admins and the listed project
members can then see it, its runs, evidence and reports, and its client. Everyone else gets 404. All
checks go through `cip/services/access.py`.

## Login protection and sessions

- After `CIP_LOGIN_MAX_FAILURES` (default 5) consecutive failed sign-ins, the account is locked for
  `CIP_LOGIN_LOCKOUT_MINUTES` (default 15).
- Each IP gets `CIP_LOGIN_IP_LIMIT` attempts per `CIP_LOGIN_IP_WINDOW_SECONDS` (default 20 per 5
  minutes).
- Unknown emails are checked against a dummy password hash, so response timing doesn't reveal which
  emails are registered.
- JWTs carry a `ver` claim matching `users.token_version`. A password change or reset, a role change
  or a deactivation bumps the version, which signs that user out everywhere.
- An organization always keeps at least one active admin.
- Client IPs come from proxy headers only when the request arrives from a trusted proxy: localhost
  for a plain server, private networks in Docker. Clients therefore can't spoof the IP used for rate
  limits and audit entries.

## Audit log

`audit_logs` is an append-only table. It records:

- sign-ins, failed sign-ins and lockouts;
- organization registration;
- user creation, role and status changes, password changes and resets, unlocks;
- uploads and imports;
- analysis starts and resumes, and approval decisions;
- source-control connection changes;
- project access changes.

Each entry stores the actor, target, details and IP. Admins can view it at `GET /api/audit` and on the
Audit Log page.

## Repository credentials

- OAuth and personal access tokens are encrypted at rest with Fernet (`CIP_TOKEN_ENCRYPTION_KEY`).
- The API never returns them.
- They are decrypted only inside `token_resolver` at call time and never stored in agent results,
  evidence or prompts.
- OAuth `state` is single-use, expires after 10 minutes, and is bound to the organization and user.
- Token expiry (`expires_in`) and the OAuth **refresh token** are stored, the refresh token encrypted
  too. Within 2 minutes of expiry, the token is refreshed automatically just before use
  (`cip/services/tokens.py`).
- Refreshes are serialized per connection, because GitLab refresh tokens are single-use.
- Refreshes and refresh failures are written to the audit log. If a refresh fails, the connection
  stops being used until it is reconnected.

## Source code and LLM safety

1. **Sensitive files are never fetched.** This covers `.env*` (templates excepted), private keys,
   `*.pem`/`*.key`/`*.p12`, credential JSON/YAML, Terraform state and tfvars, kubeconfig, and similar.
   They are listed as a security finding instead ("sensitive files committed").
2. **Fetched files are secret-scanned and redacted before storage.** The scanner covers AWS keys,
   GitHub/GitLab/Slack/Stripe/OpenAI-style tokens, Google API keys, JWTs, private-key blocks,
   connection strings with credentials, and high-entropy `secret=` / `password=` / `api_key=`
   assignments. Placeholders such as `${VAR}` and `process.env.X` are left alone.
3. **Every LLM prompt is redacted again** in `OpenRouterClient` as a last line of defense.
4. Only selected key files are sent to the LLM (never the whole repository), with a per-file size cap.

## Web research

- SSRF guard: only http(s), only public IP addresses (checked again after redirects), and no
  `file://` URLs.
- robots.txt is honoured and response sizes are capped.
- **Privacy:** only role-based business contacts are kept (support@, sales@, …). Personal emails and
  LinkedIn personal profiles are discarded, and the number discarded is reported. Leadership names are
  collected only from the company's own team or about pages.

## Human approval gates

These steps pause until someone approves them, and each request states what will be accessed, why, the
target, and what data is analyzed:

- external research;
- repository access, including private repositories;
- large repository scans;
- client-facing report generation.

Decisions, including pre-approvals given when a run starts, are stored with the deciding user and a
timestamp.

## Passive security review of client projects

The `security_review` agent only does what a normal visitor or a public database query does.

- **Live site.** It checks response headers (HSTS, CSP, clickjacking protection, `nosniff`,
  Referrer-Policy and Permissions-Policy), cookie flags, the HTTP→HTTPS redirect, server version
  disclosure, and `/.well-known/security.txt`. These checks run only when external research was
  approved for the run.
- **Dependencies.** Declared package names and versions are sent to **OSV.dev**, a public
  vulnerability database; code is never sent. Versions taken from version ranges are lower bounds and
  are marked "confirm with the lockfile". The repository-access approval mentions this. Turn it off
  with `CIP_OSV_ENABLED=false`, or disable the whole review with `CIP_SECURITY_REVIEW_ENABLED=false`.
- **Code.** The files already selected for review are scanned for insecure patterns: disabled TLS or
  JWT verification, wildcard CORS, debug mode, MD5/SHA-1, `eval`, and SQL built from strings.
- **Score.** 100 minus per-issue penalties (critical 25, high 12, medium 5, low 2), mapped to grades
  A–F. The same advisory found in several repositories counts once.

There is **no** port scanning, fuzzing, credential testing or exploitation. Those need an explicit
security-testing agreement with the client and are out of scope for this platform.

## App-store data

Only public listings and reviews are read, and only after external research was approved. The
approval text says so. Reviewer names are never stored. E-mail addresses and phone numbers in review
text are masked before the text becomes evidence. Turn the feature off with
`CIP_APP_STORE_ENABLED=false`.

## Monitoring and notifications

- **Standing approvals** are explicit, per project and recorded in the audit log under the user who
  saved them (`monitor.created` / `monitor.updated`; each scheduled run logs `monitor.run_started` with the
  gates applied). They lapse automatically when that user is deactivated, loses the analyst role or
  loses access to a restricted project. The run then pauses and raises an "approval needed" alert.
- **Webhook URLs are credentials.** They are Fernet-encrypted at rest, returned only masked
  (`https://host/…`), and never written to logs, audit entries or delivery results. Only `https://` URLs
  without embedded credentials are accepted. Deliveries go through the same public-address check as the
  crawler and don't follow redirects.
- **Scraped text in alerts.** Competitor names and announcement titles come from third-party sites, so
  `<`, `>` and `&` are escaped in webhook messages. Text such as `<!channel>` can't ping a Slack channel
  or disguise a link.
- **Alerts follow project visibility.** Alerts for a restricted project are visible only to admins and
  its members. Other organizations get 404 for every monitor and alert endpoint.

## Report export

PDFs are rendered from the report's HTML with JavaScript disabled and **every network request
blocked**. Markup that reached the report through scraped website text can't execute or fetch
anything; a test covers an injected `<script>`, an image beacon and a stylesheet link. Every export is
written to the audit log.

## Uploads

CSV files are limited to 10 MB and 5,000 rows. Filenames are sanitized, and the storage-key path is
checked so it can't escape the storage directory.
