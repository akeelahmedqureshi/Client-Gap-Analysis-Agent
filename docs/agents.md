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
| `industry_market` | after client_research, product_features | (search covered by `external_research`) | industry, market segment, product category, customer segment, business model, geography (each marked evidence or inferred), `trends[]` (trend / technology / AI adoption / automation, each quoted from a source), `keywords` |
| `competitor_research` | after client_research, product_features, industry_market | (covered by `external_research`) | `landscape[]` (Top 10 ranked by relevance: nine factor scores, reason for inclusion, `deep`), `competitors[]` (the deep-analysed Top 3: classified, feature observations, pricing, pages analysed), `rejected[]`, `ranking` |
| `pricing_analysis` | after client_research, competitor_research | — | `client` and per-competitor pricing (plans, monthly prices, models, trial, free tier, annual discount, enterprise tier), `market` statistics, client `position`, pricing `gaps` |
| `app_store` | after client_research, competitor_research | (covered by `external_research`) | `client_apps[]` / `competitor_apps[]` (verified store listings: rating, ratings count, version, last release), `client_reviews` (sentiment, themes with quotes), `requests[]` (features asked for in reviews), `competitor_review_themes[]`, `market`, app `gaps` |
| `ux_review` | after client_research, competitor_research | (covered by `external_research`) | `client` and `companies[]` (pages audited, scores per category, website practices), `issues[]` (merged across pages, WCAG reference, pages, markup evidence), `market`, UX `gaps`, `notes` |
| `business_process` | after client_research, product_features, app_store | — | `opportunities[]` (process, observed signals + evidence, assumed current process and inefficiency, BRS 7.12/7.13 fields, `ai`, `automation`, confidence), `in_place[]`, `ai_opportunities[]`, `automation_opportunities[]` |
| `feature_comparison` | after product_features, competitor_research | — | `rows[]` (client and Top-3 statuses, Top-3 and Top-10 frequency, `market_class`, `must_have`), `market` (AI and automation adoption, industry standards, emerging, differentiators, the client's coverage of standards) |
| `gap_analysis` | after feature_comparison, code_analysis, pricing_analysis, security_review, app_store, ux_review, business_process | — | `gaps[]` (missing / partial / technology / ux / ai / pricing / security / process), `existing[]` |
| `opportunity_prioritization` | after gap_analysis | — | `opportunities[]` (factors, score breakdown with evidence-confidence factor, `priority`, `business_category`, `attributes`), `recommendations[]` (top N with phase and priority), `roadmap` |
| `enhancement_planning` | after opportunity_prioritization | — | `plans[]` (ImplementationPlan) |
| `capability_matching` | after opportunity_prioritization | — | `matches[]` (per recommendation: up to 3 knowledge-base records with confidence, reasons, `client_facing`, `reference_allowed`), `by_record[]` (demand per record), `unmatched[]`, `knowledge_base` (record ids + versions used) |
| `sales_intelligence` | after capability_matching | — | `pain_points[]` (source, evidence, `internal_only`), `top_gaps[]` (claim-safe, with competitors and coverage), `top_improvements[]`, `ai_opportunity`, `automation_opportunity`, `cost_saving_opportunity`, `revenue_opportunity`, `conversation_angle`, `relevant_capabilities[]` / `internal_capabilities[]` / `case_studies[]`, `contact`, `next_step`, `claim_safety`, `outreach_input` |
| `outreach` | **requires** sales_intelligence | — | `to`, `subject`, `body`, `generated_by` (llm / template), `problems[]` (claim check), `notes[]`, `facts[]` |
| `quality_assurance` | after outreach and every analysis stage | — | `state` (complete / complete_with_warnings / partial / needs_review), `issues[]` (blocking / warning / info), `conflicts[]`, `metrics` (evidence coverage, low-confidence, inferred and assumption counts, source freshness and types, competitor and comparison coverage, stages completed / missing), `reproducibility` |
| `report` | after enhancement_planning, quality_assurance | `client_report` | `report` (structured JSON, 15 BRS sections + appendices), `markdown` |

## Notes by agent

**Sales Intelligence.** Builds the sales-ready summary (BRS 7.20) deterministically from the same gaps,
scores and matches as the report. Claim safety:

- Only gaps with confidence ≥ 0.5, competitor evidence and a client-facing type (missing, partial, AI,
  UX, pricing) lead; weaker ones are listed under `claim_safety.excluded_gaps`. Each is phrased as *not
  publicly identified*.
- Pain points come from public app-store reviews, review feature requests and high-severity conversion
  or mobile UX issues. Pricing opinions and security findings are marked `internal_only`.
- Client-facing and internal-only knowledge-base matches are kept apart; a case-study customer is named
  only when its record allows references.

**Outreach.** Writes the email (BRS 7.19) from `outreach_input.facts` only: the top two gaps, customer
pain, one AI or automation idea (labelled as an idea), and one approved client-facing capability or
case study (`core/outreach.py`). With an LLM, the draft is claim-checked; a failing draft is retried
once with the problems, then replaced by the deterministic template. The claim check flags internal-only
titles and non-referenceable customers, competitors not in the facts, security topics, and numbers
that are not in the facts. Internal-only knowledge is never in the prompt. People then edit, regenerate
(with instructions) and approve the email in the **Sales** tab; approval is refused while internal-only
or security content remains, and other warnings must be acknowledged.

**Human review** (`core/review.py`, BRS 17). Reviewers' overrides of a run are applied by the
orchestrator to every agent's output (capability statuses in the inventory, excluded competitors, rejected
gaps and their decisions, recommendation priority / phase / category / impact), keyed by stable labels so
they also apply after any refresh. The quality check counts them and flags gaps sent back for rework.

**Quality Assurance** (BRS 32, 41, 28, 31). Checks the run before the report and never changes findings:

- **Data:** duplicate capabilities, gaps that reference unknown competitors, scores or confidences out
  of range (blocking).
- **Evidence:** coverage of significant findings (gaps, recommendations, competitors; warning below
  70%), references that do not resolve (blocking), unsupported findings, low-confidence (< 45%) and
  inferred / assumption-based findings, source freshness (fresh ≤ 30 days, stale > 180 days;
  `CIP_FRESHNESS_*_DAYS`) and conflicts: CSV vs website industry, a mobile app mentioned on the website
  without a verified store listing, CSV feature lists without public evidence.
- **Recommendations:** each needs a business justification, priority, complexity and evidence; one that
  rests only on thin evidence of absence is flagged; a High priority on low confidence is blocking.
- **Outputs:** the sales summary must match the recommendations; outreach claim-check problems carry
  over (internal-only or security content is blocking).
- **State:** *needs review* with any blocking issue, otherwise *partial* when a mandatory stage did not
  complete, *complete with warnings* or *complete*. The run page and the report show it prominently.
- **Reproducibility:** model and temperature, a fingerprint of every agent prompt, taxonomy and process
  catalog versions, scoring weights, settings and the research time window.

**Report** (BRS 7.23). Built only from the structured state, in the BRS order: 1 Executive Summary
(with the analysis status and priority counts), 2 Client Overview, 3 Client Website & Product Analysis
(capability inventory, repositories, security, UX, app stores), 4 Industry & Market Analysis (profile,
sourced trends, adoption, pricing), 5 Competitor Landscape (Top 10 with relevance), 6 Top 3 Competitor
Deep Analysis, 7 Feature Comparison Matrix (with Top-3/Top-10 frequency and market class), 8 Feature
Gap Analysis (category and priority), 9 Common Competitor Features, 10 Business Cost-Reduction
Opportunities (assumptions labelled), 11 AI & Automation Opportunities, 12 Prioritized Recommendations
(High / Medium / Low), 13 Quick Wins, 14 Strategic Roadmap (short / medium / long term, architecture),
15 Business Impact Summary; then appendices A Technical Patch Plan, B Analysis Quality &
Reproducibility, C Evidence.

**Capability Matching.** Maps each recommendation to the organization's *approved* knowledge-base
records (BRS 7.18): gap → required capability → internal capability → technology → previous project →
case study. Matching is deterministic (`core/matching.py`) and local; internal knowledge is never sent
to the LLM or written to the evidence ledger.

- A record must be relevant to the capability itself: tagged with the taxonomy feature (strongest) or
  its category, or sharing keywords with the need. Industry or technology overlap alone never matches.
- Same industry, the client's technologies, AI/automation flags and documented case-study outcomes
  raise the confidence. Every match lists its reasons. Matches below 35% are dropped.
- Draft, in-review, restricted and archived records are never matched. `client_facing` is set only for
  approved records with client-facing visibility; a case study's customer may be named only when its
  record also allows references.
- The result stores the record ids and versions it used, so it stays reproducible after records change.

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

**Industry & Market** (`industry_market`, BRS 7.4). Industry, market segment, product category,
customer segment (who the client says its product is *for*), business model (from pricing signals when
not stated), and geography. With an LLM these are grounded in the client's pages; otherwise they are
derived and marked *inferred*. With the external-research approval it searches for industry trends, AI
adoption and automation trends; every trend is a sentence from a quoted source. Its segment, customers
and industry vocabulary shape competitor discovery and ranking.

**Competitor Research** (BRS 7.5–7.7). Search results are never assumed to be competitors:

1. Review and directory sites are excluded.
2. **Light verification:** each candidate's own website (1–2 pages) must be reachable, or the
   candidate is marked unverified and excluded.
3. **Relevance ranking** (`core/relevance.py`): nine factors from 0 to 1 — feature overlap, product
   similarity, industry (with an industry vocabulary: *healthcare* also matches clinics, patients,
   HIPAA), customers, geography, business model, market presence, product maturity and evidence. Market
   presence has a small weight, so the ranking favours fit over size. Candidates that resemble neither
   the client's product nor its capabilities are rejected. A candidate whose customers differ (salons vs
   clinics) is *adjacent*.
4. **Top 10** (`CIP_MAX_COMPETITORS`) form the landscape, each with its rank, factor scores and reason
   for inclusion.
5. **Top 3 deep analysis** (`CIP_DEEP_COMPETITORS`): up to 10 pages each, preferring pricing, features,
   integrations, docs, help, customers, case studies, blog, news and changelog pages, plus the LLM profile
   with grounded quotes. Each deep analysis is retried on its own; if it still fails, the competitor
   keeps its light profile and the run notes the failure. A competitor the LLM judges not comparable is
   dropped and the next in the ranking takes its place.

**Feature Comparison** (BRS 7.9, 7.11). The matrix compares the client with the Top 3. Every capability
also gets its Top-3 and Top-10 frequency and a market class: *industry standard* (≥ 60% of the
landscape, a must-have), *emerging expectation* (30–60%, or an AI capability at ≥ 20%), *differentiator*
(several, under 30%), *niche* (one competitor) or *unique to client*. Gaps carry the class and the
landscape share; industry standards raise the competitive-gap factor and can make a gap *critical*.

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
3. Score = (Σ wᵢ·benefitᵢ) × (1 − 0.4 + 0.4·evidence confidence) − w_c·complexity − w_r·risk, with
   configurable weights (BRS 15). Benefits include the BRS 7.16 dimensions: competitive importance,
   customer value, revenue, **cost saving**, **productivity**, **time to value** and strategic importance.
   Cost saving and productivity come from the taxonomy category (with feature overrides) or the process
   catalog; time to value from complexity.
4. Priority (BRS 7.14): **High** at normalized score ≥ 0.58, **Medium** ≥ 0.46, otherwise **Low**. A
   finding with confidence below 0.5 is never High, so weak evidence cannot drive the top
   recommendations.
5. Business category (BRS 7.10, `core/categories.py`): critical competitive gap (most competitors have
   it and it is High), AI opportunity, automation opportunity, strategic / long-term (Phase 4), revenue
   opportunity, customer experience gap, operational efficiency gap or high-value product gap; security
   findings are a separate risk & compliance gap. Each opportunity also gets Low/Medium/High labels for
   business relevance, customer value, competitive importance, revenue impact, efficiency impact and time
   to value.
6. Phases: complexity ≤ 2 → Phase 1; ≤ 3 → Phase 2; ≤ 4 and not a large AI bet → Phase 3; otherwise
   Phase 4.

**Business Process** (`business_process`). Cost-reduction, automation and AI opportunities in the
client's business processes (BRS 7.12–7.13), from the catalog in `core/processes.yaml` (scheduling,
customer support, lead processing, sales follow-ups, onboarding, invoicing and reconciliation, document
processing, data entry, reporting, communication, content and approvals).

- **Observed vs assumed.** A process counts only when public evidence shows it exists: the client's
  pages mention it (quoted in the ledger), it offers a capability that implies it, or its app reviews
  complain about it. How the process runs today and where it is inefficient are *assumptions* from the
  catalog, labelled as such, with basis `estimate`.
- **Not already in place.** If the client publicly offers every capability of the improvement, the
  process is listed as *in place* instead.
- **Fields.** Each opportunity has the BRS 7.12 and 7.13 fields: likely current process, inefficiency,
  business problem (plus review evidence), proposed solution, how it works, resource saving,
  processing-time and error/rework reduction (qualitative levels, never invented percentages),
  productivity, customer impact, revenue opportunity, complexity and confidence.
- **AI only with a business reason.** AI is suggested only as the improvement to an observed process.
  Opportunities become `process` gaps and are scored and prioritised like every other gap.

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
