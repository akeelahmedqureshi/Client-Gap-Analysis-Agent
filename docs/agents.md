# Agents

Every agent returns an `AgentResult`:

```json
{"status": "completed", "findings": [], "evidence": [], "confidence": 0.91, "next_actions": [], "data": {}, "errors": []}
```

Each `Finding` has a `basis`:

- `evidence`: observed directly in a source;
- `inferred`: derived deterministically from evidence;
- `estimate`: an AI hypothesis.

| Agent | Depends on | Gate | Output (`data`) |
|---|---|---|---|
| `csv_intake` | — | — | `record` (NormalizedRecord); CSV-row evidence |
| `client_research` | after csv_intake | `external_research` | `profile` (CompanyProfile: description, industry, HQ, founded, products, contacts), `leadership`, `pages`, `project_pages` |
| `repository` | after csv_intake | `repository_access`, `large_repository_scan:<repo>` (runtime) | `repositories[]`: metadata, languages, paths, redacted key files, activity, skipped sensitive files |
| `code_analysis` | **requires** repository | — | `profiles[]` (RepositoryProfile: technologies, architecture, tests/CI/Docker/IaC, debt), `feature_signals[]` |
| `product_features` | after client_research, code_analysis | — | `observations{feature_id: FeatureObservation}`, `inventory[]`, `coverage` |
| `competitor_research` | after client_research, product_features | (covered by `external_research`) | `competitors[]` (verified, classified, feature observations), `rejected[]` |
| `pricing_analysis` | after client_research, competitor_research | — | `client` and per-competitor pricing (plans, monthly prices, models, trial, free tier, annual discount, enterprise tier), `market` statistics, client `position`, pricing `gaps` |
| `app_store` | after client_research, competitor_research | (covered by `external_research`) | `client_apps[]` / `competitor_apps[]` (verified store listings: rating, ratings count, version, last release), `client_reviews` (sentiment, themes with quotes), `requests[]` (features asked for in reviews), `competitor_review_themes[]`, `market`, app `gaps` |
| `ux_review` | after client_research, competitor_research | (covered by `external_research`) | `client` and `companies[]` (pages audited, scores per category, website practices), `issues[]` (merged across pages, WCAG reference, pages, markup evidence), `market`, UX `gaps`, `notes` |
| `feature_comparison` | after product_features, competitor_research | — | `rows[]` (client status and each competitor's status per taxonomy feature, coverage) |
| `gap_analysis` | after feature_comparison, code_analysis, pricing_analysis, security_review, app_store, ux_review | — | `gaps[]` (missing / partial / technology / ux / ai / pricing / security), `existing[]` |
| `opportunity_prioritization` | after gap_analysis | — | `opportunities[]` (factors, score breakdown), `recommendations[]` (top N with phase), `roadmap` |
| `enhancement_planning` | after opportunity_prioritization | — | `plans[]` (ImplementationPlan) |
| `report` | after enhancement_planning | `client_report` | `report` (structured JSON), `markdown` |

## Notes by agent

**CSV Intake.** The agent detects columns by alias, including fuzzy matches such as "Company", "Repo"
and "Tech Stack". It sniffs the delimiter (`,` `;` tab `|`) and handles BOMs and several encodings.

- URLs are extracted from any cell and classified as GitHub, GitLab, LinkedIn, social or website.
- The client domain comes from a corporate email; free-mail domains such as gmail.com are ignored, and
  the project website is the fallback.
- Missing names are inferred from domains.
- Duplicate projects are flagged when they share a client and project name or share a project URL.
- Rows that refer to the same client produce a warning.

**Client/Website Research.** The crawler visits priority pages first (about, products, pricing,
contact, …), then discovered links. It honours robots.txt and refuses non-public addresses.

- The project's own domain is crawled too, when it differs from the company domain.
- Contacts are kept only when they are role-based (sales@, support@, …). Personal emails are counted
  and discarded, and LinkedIn `/in/` profiles are ignored.
- The LLM profile extraction must cite a source page and a verbatim quote for each fact.
- **Structured data:** schema.org JSON-LD supplies legal name, founding date, address, employee
  count, brands, subsidiaries, markets served and social profiles. These are the company's own
  machine-readable statements, so they outrank text extraction. The footer copyright line is a
  fallback for the legal name.
- **Hiring signals:** links from the careers page to Greenhouse, Lever or Ashby boards are read
  through the boards' official public JSON APIs, and JSON-LD `JobPosting` entries are used too. Open
  roles are grouped into areas such as AI/ML, data, mobile, DevOps and security. Prioritization adds
  +1 strategic alignment to gaps in an area the client is actively hiring for, and cites the job-board
  evidence.
- **Announcements:** posts under blog, news, press and changelog sections. Product launches
  ("introducing…", "launches…", "now available…") are flagged.

**Code Intelligence.** The whole repository is never sent to the LLM. The pipeline is:

tree → classification → important-file selection (README, manifests, CI, infra, API specs, DB schema,
routes) → dependency parsing → technology and architecture rules → a targeted LLM review of the
selected, redacted files.

It parses these manifests: `package.json`, `requirements*.txt`, `pyproject.toml`, `Pipfile`,
`setup.py`, `Gemfile`, `go.mod`, `pom.xml`, `build.gradle(.kts)`, `composer.json`, `Cargo.toml`,
`pubspec.yaml` and `*.csproj`.

Evidence links point to the exact file and line.

**Competitor Research.** Search results are never assumed to be competitors:

1. Review and directory sites are excluded.
2. Each candidate's own website must be reachable, or the candidate is marked unverified and excluded.
3. Candidates with less than 10% feature overlap with the client, or that the LLM judges not
   comparable, are rejected. The report shows rejected candidates.

Classes: direct, indirect, adjacent, open_source, enterprise, emerging.

Candidate sources:

- **General web search.**
- **G2, Capterra and Product Hunt search queries.** These pages serve only as a source of product
  names; they are never competitors, and their evidence is typed `marketplace`.
- **Open-source alternatives** from GitHub's search API: at least 50 stars, no forks. They are
  verified through the GitHub API (metadata and README), because GitHub's robots.txt disallows
  crawling repository pages.

**Pricing Analysis.** Pricing pages are read during client and competitor research; competitor
crawls visit `/pricing` and `/plans` first.

- **What's extracted:** plans, prices normalized to per month, the pricing model, trials, free tiers,
  annual discounts and "contact sales" tiers. Each plan price and practice becomes evidence. Amounts
  without a billing period (e.g. "$12M raised") are ignored.
- **Market comparison:** median and range of entry prices, using the dominant currency only (prices
  in different currencies are never mixed), and how common each practice is. The client is positioned
  above market (> 1.5× the median), below (< 0.5×) or within.
- **Pricing gaps:** raised when a practice is used by at least half of the priced competitors but not
  by the client, or when the client is priced far above market. They get commercial/billing patch
  plans.

**Gap Analysis.**

- Capability statuses follow BRS 7.9: *available*, *partially available*, *not publicly identified*
  (`unknown`) and *confirmed missing* (`missing`). A feature with no signal in the client's sources is
  always *not publicly identified*, never *confirmed missing*: absence of public evidence is not proof
  of absence. *Confirmed missing* needs positive evidence, such as a reviewer's override. How thoroughly
  the client was inspected sets the observation's confidence instead.
- A feature a competitor has that is not publicly identified (or confirmed missing) for the client is a
  *missing* gap. It becomes a *UX* gap for experience-category features and an *AI* gap for AI
  features. Its description and confidence say which case applies.
- A basic client version against a fuller competitor version is a *partial* gap.
- Tech-debt indicators become *technology* gaps: tests, CI/CD, observability, lockfiles, legacy
  frameworks, documentation, committed secrets, and deployment/containerization.
- If the client has no AI capability at all, the top AI opportunities are added as *emerging* AI gaps
  with basis `estimate`.

An absence claim ("no evidence of X found in the client's website, code, csv") is recorded as
low-confidence evidence.

**Prioritization.**

1. Baseline factors come from the taxonomy defaults, competitor coverage (market demand and
   competitive gap) and feasibility.
2. The LLM may adjust each factor by at most ±1 and writes the narratives, all labelled `estimate`.
3. Score = Σ wᵢ·benefitᵢ − w_c·complexity − w_r·risk, with configurable weights.
4. Phases: complexity ≤ 2 → Phase 1; ≤ 3 → Phase 2; ≤ 4 and not a large AI bet → Phase 3; otherwise
   Phase 4.

**Security Review** (`security_review`, after client research and code analysis). It is passive
only; see docs/security.md. It checks the live site's posture, looks up dependencies on OSV.dev and
scans reviewed files for insecure patterns. Output: a score and grade, issues with severity and
recommendations, and security gaps (HTTPS enforcement, header and cookie hardening, dependency
remediation, secure-coding fixes, disclosure policy). High and critical gaps get +1 business value.

**App Store** (`app_store`, after client research and competitor research). It runs only when external
research was approved.

- **Finding apps.** It follows App Store and Google Play links on each company's own website, then
  searches the App Store by name. A search hit is kept only when ownership is verified: the developer's
  website is on the company's domain, or the developer name matches the company name. "ABC Kids
  Learning" is not attributed to "ABC Healthcare".
- **Listings.** iOS listings come from Apple's public lookup and search API. Android listings come
  from the public Google Play page (schema.org data), which is crawled with robots.txt respected.
- **Reviews.** Up to 50 recent Apple customer reviews per app. Author names are dropped, and e-mail
  addresses and phone numbers are masked.
  - Each review is sorted into fixed themes: crashes and bugs, performance, login, notifications,
    sync, usability, support, and pricing.
  - Reviews that ask for something ("please add …", "would be nice …") are matched to the taxonomy.
  - Every quoted review is evidence.
- **Gaps.** No client app while competitors have one → "Native mobile app" (merged with the
  comparison's gap). A rating 0.3★ or more below the competitor median → "App store rating below
  competitors". A theme in at least 3 negative reviews and 20% of them → for example "Mobile app
  stability (crashes & bugs)". No release in 180 days → "Mobile app release cadence".
- **Effects elsewhere.**
  - A verified client listing removes a false "Native mobile app" gap.
  - Features requested in 2 or more reviews get +1 market demand in prioritization.
  - App-quality gaps get a mobile patch plan (crash reporting, staged rollouts, device tests).

**UX Review** (`ux_review`, after client research and competitor research). It is passive (it only
does what a visitor's browser does) and runs only when external research was approved.

- **Pages.** The client's homepage plus up to three key pages it found: pricing, sign-up or demo,
  product, and contact. For competitors, the homepage only.
- **Static checks** (always):
  - WCAG basics: page language (3.1.1), title (2.4.2), image alt text (1.1.1), form labels (1.3.1 /
    4.1.2), link and button names, vague link text, heading order, main landmark, and zoom not
    disabled (1.4.4);
  - mobile: the viewport (1.4.10);
  - conversion practices: self-serve or demo call to action, sign-in, help/FAQ, live chat, trust
    signals and site search.
- **Browser checks** (headless Chromium; `CIP_UX_BROWSER_CHECKS`):
  - colour contrast (1.4.3);
  - sideways scrolling, tap targets under 24×24px (2.5.8) and small text, in a 390px phone viewport;
  - lab page speed: LCP, page weight and request count.
  - Same in-browser SSRF guard as the crawler. Images and fonts load so the measurements are
    realistic.
- **Issues and scores.** The same check on several pages becomes one issue listing the pages. Each
  category scores 100 minus penalties, and the overall score is their average. Competitors are
  scored on the same checks.
- **Gaps.**
  - Quality gaps from the client's own issues: "Accessibility (WCAG 2.2 AA) fixes" (merged with
    `ux.accessibility`), "Mobile-friendly responsive layout", "Page speed (Core Web Vitals)" and
    "Clear primary call to action".
  - Practice gaps when at least half of the audited competitors show a practice the client lacks:
    live chat, help center, trust signals, site search, self-serve sign-up. Self-serve sign-up is
    skipped when the pricing "Free trial" gap or the call-to-action gap already covers it.
  - "UX quality below competitors" (10 or more points under the median) is reported as a finding,
    not a roadmap item.

**Architecture diagrams** (built by `enhancement_planning`, in `core/architecture.py`).

- **Current:** a layered model of users, client apps, application, data, external services and the
  platform band, built from the technologies code analysis detected. Without repository access it
  uses the technologies declared in the CSV.
- **Target:** the current model plus the components the roadmap introduces, highlighted. A component
  is skipped when an existing technology already fills that role; for example, Stripe already covers
  "Payments provider".
- **Rendering:** SVG for the UI and PDF (no JavaScript needed), and Mermaid blocks in the Markdown
  report. All labels are escaped.

## Adding an agent

1. Subclass `Agent` and set `name`, `description`, `requires` / `after`, and optionally
   `approval_needed`.
2. Return an `AgentResult`. Write facts to `ctx.ledger` and reference only ledger IDs.
3. Register the agent in `orchestrator.default_agents()`.
4. Add tests with the fakes in `backend/tests/fakes.py`.
