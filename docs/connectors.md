# Connectors

## Source control: `SourceControlProvider`

```
SourceControlProvider
   ├── GitHubProvider   (REST v3; github.com or GitHub Enterprise at https://host/api/v3)
   └── GitLabProvider   (REST v4; gitlab.com or self-managed)
```

Methods:

- `get_repository`
- `list_repositories`
- `get_languages`
- `get_tree`
- `get_file`
- `list_activity` (commits, PRs/MRs, releases)
- `file_url` (a deep link with a line anchor)

`parse_repo_url` accepts HTTPS, SSH (`git@host:owner/repo.git`), `/tree/...` paths and GitLab
subgroups. GitHub Pages hosts are rejected.

### Authentication

- **OAuth.** Call `GET /api/connections/{github|gitlab}/authorize` to get the provider URL. The
  callback verifies the CSRF `state` (10-minute TTL, bound to the organization and user), exchanges the
  code, and stores the token encrypted. Scopes are GitHub `read:user read:org repo` and GitLab
  `read_api read_repository read_user`.
- **Personal access token.** `POST /api/connections/token`. Prefer fine-grained, read-only tokens.
- Tokens are resolved lazily per API call through `RunContext.token_resolver` and never placed in
  agent state, evidence or prompts.
- Expiring OAuth tokens (GitLab's last about 2 hours) are renewed automatically with the stored
  refresh token, once per connection even when several agents need the token at the same time.

A private repository without a connection doesn't fail the run. The repository agent records "private
or missing — connect GitHub", code analysis is skipped, and the rest of the analysis continues.

## Web: `WebFetcher`

The fetcher sends a fixed user agent, uses timeouts, honours robots.txt, and caps response size. Its
SSRF guard only fetches http(s) URLs that resolve to public IPs, and it re-checks the target after
redirects.

`crawl()` fetches priority paths first, then follows same-site links. Discovered links that look like
priority pages are moved to the front of the queue.

### JavaScript rendering: `BrowserRenderer`

Many product sites are single-page apps whose HTML is an empty shell until scripts run. The fetcher
always does a plain HTTP fetch first, and `CIP_BROWSER_RENDERING` decides when headless Chromium
(Playwright) is used:

- `auto` (default): render only pages that look script-rendered, meaning little visible text plus
  scripts or a known SPA mount point (`#root`, `#__next`, `ng-app`, "enable JavaScript").
- `always`: render every HTML page.
- `never`: plain HTTP only.

Links that only exist after rendering are followed. Rendered pages are marked `rendered=True`.

Safety inside the browser:

- Every request the page makes is intercepted: navigations, scripts, XHR/fetch and frames.
- Non-http(s) schemes and hosts that resolve to private or internal addresses are aborted (SSRF
  guard).
- Images, fonts and media aren't downloaded.
- Downloads and service workers are disabled, and each page gets a fresh browser context.

If Playwright isn't installed, or the browser can't start, the crawler logs it and falls back to plain
HTTP.

## Search: `SearchProvider`

Implementations: `TavilySearchProvider`, `BraveSearchProvider` and `NullSearchProvider`. Select one
with `CIP_SEARCH_PROVIDER`.

## App stores: `connectors/research/appstore.py`

- `apple_lookup`, `apple_search`, `apple_reviews`: the public iTunes Search/Lookup API and the App
  Store customer-review feed.
- `play_listing`: the public Google Play page, fetched by `WebFetcher` (robots.txt respected). Its
  schema.org `SoftwareApplication` data supplies the name, developer, rating and rating count. Play
  reviews have no public API and are not collected.
- `ownership()` decides whether a listing belongs to a company.
- `analyze_reviews()` produces the deterministic themes and feature requests.

Settings: `CIP_APP_STORE_ENABLED`, `CIP_APP_STORE_COUNTRY` (storefront, default `us`) and
`CIP_APP_STORE_COMPETITOR_REVIEWS` (how many competitors' reviews are analysed).

## Company research: `CompanyResearchProvider`

```python
class CompanyResearchProvider:
    async def search_company(self, company_name, domain=None) -> CompanyResult | None
    async def get_products(self, company) -> list[ProductCandidate]
    async def get_people(self, company) -> list[Person]
```

`CompositeCompanyResearch` queries providers in order and isolates each provider's failures.

- `WebsiteCompanyProvider` reads the company's own website.
- `SearchCompanyProvider` uses web search. When the client's domain is known, it never adopts a
  different domain.
- `LinkedInCompanyProvider` works **only** through an officially licensed `LinkedInAdapter` (a partner
  API or data connector) and never scrapes. It is a no-op until an adapter is configured, so a change
  in LinkedIn access can't break the platform.
