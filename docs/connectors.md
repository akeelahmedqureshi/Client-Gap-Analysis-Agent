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

A private repository without a connection doesn't fail the run. The repository agent records "private
or missing — connect GitHub", code analysis is skipped, and the rest of the analysis continues.

## Web: `WebFetcher`

The fetcher sends a fixed user agent, uses timeouts, honours robots.txt, and caps response size. Its
SSRF guard only fetches http(s) URLs that resolve to public IPs, and it re-checks the target after
redirects.

`crawl()` fetches priority paths first, then follows same-site links. Discovered links that look like
priority pages are moved to the front of the queue.

## Search: `SearchProvider`

Implementations: `TavilySearchProvider`, `BraveSearchProvider` and `NullSearchProvider`. Select one
with `CIP_SEARCH_PROVIDER`.

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
