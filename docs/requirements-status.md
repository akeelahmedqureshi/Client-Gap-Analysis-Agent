# Requirements status

This file tracks how the build covers the requirements in [BRS.md](BRS.md) (business requirements) and
[PRD.md](PRD.md) (product requirements). The two documents describe the same product; the PRD is the
product-level version of the BRS.

The requirements are split into **128 trackable features**. Update this file in the same commit as the
feature.

✅ completed · 🟡 partially completed · ❌ incomplete

## Summary

| Status | Count |
|---|---|
| ✅ Completed | 71 |
| 🟡 Partially completed | 25 |
| ❌ Incomplete | 32 |
| **Total** | **128** |

_Baseline audit: 2026-10-08._

## 1. Access & security (10)

| # | Feature | Status | Notes |
|---|---|---|---|
| 1 | Login and logout | ✅ | |
| 2 | Session security: token expiry, account lockout, IP rate limit | ✅ | |
| 3 | Organisation isolation and per-project access | ✅ | |
| 4 | User management: add, change role, deactivate, reset, unlock | ✅ | |
| 5 | Audit log | ✅ | |
| 6 | HTTPS in transit; repository tokens encrypted at rest | ✅ | |
| 7 | Secrets redacted before anything reaches the LLM | ✅ | |
| 8 | Roles named in the BRS (Sales, BD, Product, Tech, Viewer) | 🟡 | Only admin, analyst and viewer exist |
| 9 | Export permissions by role | 🟡 | Every role can export |
| 10 | Data retention and deletion policy | ❌ | No delete for clients, projects or runs |

## 2. CSV intake (11)

| # | Feature | Status | Notes |
|---|---|---|---|
| 11 | Upload | ✅ | |
| 12 | File format validation | ✅ | |
| 13 | Required-column validation | ✅ | Client Name + Website URL is enough |
| 14 | Extract client names and URLs | ✅ | |
| 15 | Malformed-URL validation | ✅ | |
| 16 | Duplicate client and URL detection | ✅ | |
| 17 | Preview with per-row errors | ✅ | |
| 18 | Select all or individual rows | ✅ | |
| 19 | Invalid rows blocked from import | ✅ | |
| 20 | One analysis job per selected client | 🟡 | Runs start one project at a time; no "analyse all selected" |
| 21 | Check for unreachable, redirecting or parked domains | ❌ | |

## 3. Client business and product analysis (6)

| # | Feature | Status | Notes |
|---|---|---|---|
| 22 | Identity, industry, business model, revenue model, headquarters, size | ✅ | Grounded in sources |
| 23 | Products, services, target customers, geography | ✅ | |
| 24 | Capability inventory with evidence and confidence | ✅ | |
| 25 | Value proposition, use cases, customer problems | 🟡 | A description only |
| 26 | Four statuses and the "Not publicly identified" rule | ✅ | No signal is "not publicly identified"; "confirmed missing" needs proof |
| 27 | User, customer and business workflows | ❌ | |

## 4. Industry and market (3)

| # | Feature | Status | Notes |
|---|---|---|---|
| 28 | Industry and segment classification | 🟡 | Industry label only |
| 29 | Trends, emerging technology, AI adoption, automation trends | ❌ | |
| 30 | Industry profile that feeds competitor selection | ❌ | |

## 5. Competitors (8)

| # | Feature | Status | Notes |
|---|---|---|---|
| 31 | Discovery from search, LLM and marketplaces, validated before acceptance | ✅ | |
| 32 | Competitor profile: name, URL, type, description, market, reason for inclusion, evidence | ✅ | |
| 33 | Chosen for relevance rather than size | ✅ | |
| 34 | Top-10 list | 🟡 | 5 by default |
| 35 | Deep research sources (site, pricing, app stores, UX) | 🟡 | Docs, help centre and announcements not targeted |
| 36 | Retry a single failed competitor | 🟡 | Retry works per agent |
| 37 | Relevance score built from several factors, plus a rank | ❌ | |
| 38 | Deep analysis of the Top 3 | ❌ | |

## 6. Capability names and comparison matrix (5)

| # | Feature | Status | Notes |
|---|---|---|---|
| 39 | Central list of capability names, with categories and synonyms | ✅ | `core/taxonomy.yaml` |
| 40 | Mapping each found feature onto that list | ✅ | |
| 41 | Comparison matrix | ✅ | |
| 42 | Capability list editable in the UI, with versions | 🟡 | YAML file only |
| 43 | Matrix search, filter and export | 🟡 | Evidence links only |

## 7. Gaps and common capabilities (6)

| # | Feature | Status | Notes |
|---|---|---|---|
| 44 | Gap list with competitors that have it, count, evidence and confidence | ✅ | |
| 45 | How common each capability is among competitors | ✅ | |
| 46 | Business attributes per gap | ✅ | Relevance, customer value, competitive importance, revenue, efficiency, time to value, complexity |
| 47 | The 8 business gap categories | ✅ | Plus a separate risk & compliance category for security |
| 48 | Standard / emerging / differentiator / niche; must-have versus optional | 🟡 | Differentiators only |
| 49 | Frequency among the Top 3 compared with the Top 10 | ❌ | |

## 8. Opportunity analysis (4)

| # | Feature | Status | Notes |
|---|---|---|---|
| 50 | AI opportunity analysis | ✅ | AI only as the improvement to an observed process or with competitor evidence |
| 51 | Fields per opportunity (problem, solution, how it works, cost, productivity) | ✅ | Cost & AI tab |
| 52 | Business-process and cost-reduction analysis | ✅ | `business_process` agent; assumptions labelled |
| 53 | Automation opportunity analysis | ✅ | |

## 9. Scoring, prioritisation and roadmap (7)

| # | Feature | Status | Notes |
|---|---|---|---|
| 54 | Deterministic weighted score, with an explanation | ✅ | `core/scoring.py` |
| 55 | Weights adjustable for each run | ✅ | |
| 56 | Roadmap timeline plus patch plans | ✅ | |
| 57 | BRS factors: cost saving, productivity, time to value, evidence confidence | ✅ | Confidence scales the benefit part of the score |
| 58 | High / Medium / Low priority label | ✅ | |
| 59 | Saved, versioned scoring profiles | ❌ | |
| 60 | Low-confidence findings blocked from top priority | ✅ | Confidence < 0.5 is never High; sales leads need ≥ 0.5 |

## 10. Internal knowledge base and matching (4)

| # | Feature | Status | Notes |
|---|---|---|---|
| 61 | Create, edit and archive records: capabilities, projects, case studies, technologies, tags | ✅ | Knowledge Base page, `/api/knowledge` |
| 62 | Approval and client-facing states | ✅ | draft → in review → approved / restricted / archived; client-facing flag; case-study reference flag |
| 63 | Versions, change history, search and filter | ✅ | Snapshot per version with who/when/note |
| 64 | Matching from gap to our capability to a case study | ✅ | `capability_matching` agent, "Our Fit" tab |

## 11. Sales and outreach (4)

| # | Feature | Status | Notes |
|---|---|---|---|
| 65 | Sales-intelligence summary | ✅ | `sales_intelligence` agent, Sales tab, Markdown export |
| 66 | Personalised outreach email | ✅ | `outreach` agent: claim-checked LLM draft or template |
| 67 | Edit, regenerate and approve the email | ✅ | Versioned history; approval blocked on internal-only or security content |
| 68 | Export the email | ✅ | Copy, `.eml` draft |

## 12. Evidence and quality (10)

| # | Feature | Status | Notes |
|---|---|---|---|
| 69 | Evidence ledger: URL, type, access date, excerpt, confidence | ✅ | No source title |
| 70 | Fact / inferred / estimate labels | ✅ | |
| 71 | LLM claims must quote their source word for word | ✅ | `core/grounding.py` |
| 72 | Evidence view with filters | ✅ | |
| 73 | Drill-down from recommendation to gap to evidence | 🟡 | "N sources" links only |
| 74 | Source-quality tiers | 🟡 | Source type only |
| 75 | Detection of conflicting sources | ❌ | |
| 76 | Freshness states (fresh / aging / stale) | ❌ | |
| 77 | Quality-check agent and quality metrics | ❌ | |
| 78 | Report blocked or flagged when quality is low | ❌ | |

## 13. Research layer (7)

| # | Feature | Status | Notes |
|---|---|---|---|
| 79 | Crawling, extraction, link discovery | ✅ | |
| 80 | Rendering JavaScript-heavy pages | ✅ | |
| 81 | Redirects, robots.txt, timeouts, internal-address blocking | ✅ | |
| 82 | No duplicate URLs or evidence | ✅ | |
| 83 | Per-domain rate limit, retries, recorded failure states | 🟡 | Failures logged and warned only |
| 84 | Research cache shared across agents and runs | ❌ | |
| 85 | PDF sources | ❌ | Skipped |

## 14. Orchestration and run lifecycle (13)

| # | Feature | Status | Notes |
|---|---|---|---|
| 86 | One job per project, run as a dependency graph of agents | ✅ | |
| 87 | Agents run in parallel | ✅ | |
| 88 | Agent retries with backoff | ✅ | |
| 89 | Retry only the failed stages | ✅ | |
| 90 | Runs survive restarts | ✅ | |
| 91 | LLM output checked against a schema, with a retry | ✅ | |
| 92 | Approval gates | ✅ | |
| 93 | Full status model (completed with warnings, partial, needs review, cancelled) | 🟡 | |
| 94 | Cancel a run | ❌ | |
| 95 | Pause and resume | ❌ | |
| 96 | Rerun selected stages; regenerate the report or email | ❌ | |
| 97 | Duplicate-run prevention | ❌ | |
| 98 | Token and cost budgets | ❌ | |

## 15. Human review (2)

| # | Feature | Status | Notes |
|---|---|---|---|
| 99 | Edit or override competitors, statuses, priorities, roadmap | ❌ | |
| 100 | Approve, reject or send back individual findings | ❌ | |

## 16. Versioning and reproducibility (4)

| # | Feature | Status | Notes |
|---|---|---|---|
| 101 | Run history and comparing versions | ✅ | |
| 102 | Tracking changed competitors, capabilities and prices | ✅ | |
| 103 | Recording how a run was produced (model, prompt and taxonomy versions) | 🟡 | Scoring weights only |
| 104 | Prompt and model versioning | ❌ | |

## 17. Configuration (1)

| # | Feature | Status | Notes |
|---|---|---|---|
| 105 | Admin-editable, versioned configuration | 🟡 | Environment variables only |

## 18. Report and export (6)

| # | Feature | Status | Notes |
|---|---|---|---|
| 106 | Report built from the stored analysis data | ✅ | |
| 107 | Markdown and PDF export | ✅ | |
| 108 | The 15 required sections | 🟡 | 10 sections |
| 109 | Section navigation, print view, version display | 🟡 | |
| 110 | Structured exports (matrix CSV, opportunities CSV, JSON) | 🟡 | Evidence JSON only |
| 111 | Completeness indicator and low-confidence warnings | ❌ | |

## 19. Dashboard and portfolio (5)

| # | Feature | Status | Notes |
|---|---|---|---|
| 112 | Client detail page with top opportunities | ✅ | |
| 113 | Portfolio dashboard: top gaps and AI opportunities, failed and needs-review runs | 🟡 | Counts, recent runs, approvals only |
| 114 | Client list with status, score and actions | 🟡 | |
| 115 | Search, filter and sort across clients, competitors and capabilities | ❌ | |
| 116 | Cross-client comparison and recurring gaps | ❌ | |

## 20. Notifications and observability (5)

| # | Feature | Status | Notes |
|---|---|---|---|
| 117 | Agent status, duration, attempts, errors | ✅ | |
| 118 | In-app notifications | 🟡 | Monitoring alerts only |
| 119 | Email notifications | 🟡 | Monitoring alerts only |
| 120 | Token, model and cost tracking | ❌ | |
| 121 | Web-request tracking | ❌ | |

## 21. Future enhancements — P2 (7)

| # | Feature | Status | Notes |
|---|---|---|---|
| 122 | Continuous competitor monitoring | ✅ | |
| 123 | Market-change alerts (Slack, email, in-app) | ✅ | |
| 124 | Historical competitor benchmarking | 🟡 | Run-to-run changes only |
| 125 | CRM integration and automatic opportunity creation | ❌ | |
| 126 | Email and marketing system integration | ❌ | |
| 127 | Multi-language analysis | ❌ | |
| 128 | Industry-wide benchmarking and predicted trends | ❌ | |

## Built beyond the BRS and PRD (not counted)

- Repository and code analysis, with an architecture view
- Passive security review and dependency vulnerability lookups (OSV.dev)
- Technical patch plans
- UX review: accessibility, mobile and speed checks
- App Store and Google Play analysis, including review themes
- Pricing analysis
- Private GitHub/GitLab connections
- Command-line tool

## Build order for the remaining work

1. "Not publicly identified" rule (#26)
2. Knowledge base and capability matching (#61–64)
3. Sales summary and outreach email (#65–68)
4. Cost-reduction, automation and AI opportunity analysis (#50–53)
5. Industry and market profile (#28–30)
6. Top-10 ranking and Top-3 deep analysis (#34, 37–38, 48–49)
7. Quality-check agent and report completeness (#77–78, 111)
8. The 15-section report (#108)
9. P1 items: human review, lifecycle controls, cost tracking, freshness, exports, portfolio view
