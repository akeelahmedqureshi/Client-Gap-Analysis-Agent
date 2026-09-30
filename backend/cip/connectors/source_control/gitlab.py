"""GitLab REST API (v4) provider + OAuth helpers."""

from __future__ import annotations

from urllib.parse import quote, urlencode

import httpx

from cip.config import get_settings
from cip.connectors.source_control.base import (
    ActivityItem,
    RepoMetadata,
    RepoRef,
    RepositoryAccessDenied,
    SourceControlError,
    SourceControlProvider,
    TreeEntry,
)

GITLAB_SCOPES = "read_api read_repository read_user"
MAX_TREE_PAGES = 100


class GitLabProvider(SourceControlProvider):
    name = "gitlab"

    def __init__(self, token: str | None = None, base_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._base = (base_url or get_settings().gitlab_url).rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._token:
            # GitLab accepts both OAuth tokens and PATs as Bearer tokens.
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=f"{self._base}/api/v4", headers=headers, timeout=30.0,
                                 transport=self._transport, follow_redirects=True)

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        async with self._client() as c:
            resp = await c.get(path, params=params)
        if resp.status_code in (401, 403, 404):
            raise RepositoryAccessDenied(f"GitLab returned {resp.status_code} for {path}")
        if resp.status_code >= 400:
            raise SourceControlError(f"GitLab API error {resp.status_code}: {resp.text[:200]}")
        return resp

    @staticmethod
    def _pid(ref: RepoRef) -> str:
        return quote(ref.full_name, safe="")

    def _meta(self, ref: RepoRef, d: dict) -> RepoMetadata:
        return RepoMetadata(
            ref=ref,
            default_branch=d.get("default_branch"),
            private=(d.get("visibility") != "public") if d.get("visibility") else None,
            description=d.get("description"),
            stars=d.get("star_count"),
            topics=d.get("topics") or d.get("tag_list") or [],
            pushed_at=d.get("last_activity_at"),
            archived=bool(d.get("archived")),
        )

    async def get_repository(self, ref: RepoRef) -> RepoMetadata:
        d = (await self._get(f"/projects/{self._pid(ref)}")).json()
        return self._meta(ref, d)

    async def list_repositories(self, limit: int = 100) -> list[RepoMetadata]:
        if not self._token:
            return []
        resp = await self._get("/projects", params={"membership": "true", "per_page": min(limit, 100),
                                                    "order_by": "last_activity_at"})
        host = self._base.split("://", 1)[-1]
        return [self._meta(RepoRef("gitlab", host, d["path_with_namespace"]), d) for d in resp.json()]

    async def get_languages(self, ref: RepoRef) -> dict[str, int]:
        # GitLab returns percentages; scale to pseudo-bytes for parity with GitHub.
        d = (await self._get(f"/projects/{self._pid(ref)}/languages")).json()
        return {k: int(float(v) * 1000) for k, v in d.items()}

    async def get_tree(self, ref: RepoRef, branch: str) -> list[TreeEntry]:
        entries: list[TreeEntry] = []
        page = 1
        while page and page <= MAX_TREE_PAGES:
            async with self._client() as c:
                resp = await c.get(f"/projects/{self._pid(ref)}/repository/tree",
                                   params={"recursive": "true", "per_page": 100, "ref": branch, "page": page})
            if resp.status_code in (401, 403, 404):
                raise RepositoryAccessDenied(f"GitLab returned {resp.status_code} for tree")
            resp.raise_for_status()
            entries.extend(TreeEntry(t["path"]) for t in resp.json() if t.get("type") == "blob")
            nxt = resp.headers.get("x-next-page")
            page = int(nxt) if nxt else 0
        return entries

    async def get_file(self, ref: RepoRef, path: str, branch: str) -> str | None:
        try:
            resp = await self._get(f"/projects/{self._pid(ref)}/repository/files/{quote(path, safe='')}/raw",
                                   params={"ref": branch})
        except RepositoryAccessDenied:
            return None
        return resp.text

    async def list_activity(self, ref: RepoRef, limit: int = 10) -> list[ActivityItem]:
        items: list[ActivityItem] = []
        pid = self._pid(ref)
        for path, kind in ((f"/projects/{pid}/repository/commits", "commit"),
                           (f"/projects/{pid}/merge_requests", "merge_request"),
                           (f"/projects/{pid}/releases", "release")):
            try:
                data = (await self._get(path, params={"per_page": limit})).json()
            except SourceControlError:
                continue
            for d in data[:limit]:
                items.append(ActivityItem(
                    kind, d.get("title") or d.get("name") or d.get("tag_name") or "",
                    d.get("web_url") or (d.get("_links") or {}).get("self"),
                    d.get("state"), d.get("created_at"),
                ))
        return items

    def file_url(self, ref: RepoRef, path: str, branch: str, line_range: str | None = None) -> str:
        url = f"https://{ref.host}/{ref.full_name}/-/blob/{branch}/{path}"
        if line_range:
            url += f"#L{line_range}"
        return url

    # ---------------------------------------------------------------- OAuth
    @staticmethod
    def authorize_url(state: str, redirect_uri: str) -> str:
        s = get_settings()
        if not s.gitlab_client_id:
            raise SourceControlError("GitLab OAuth is not configured (CIP_GITLAB_CLIENT_ID)")
        q = urlencode({"client_id": s.gitlab_client_id, "redirect_uri": redirect_uri,
                       "response_type": "code", "scope": GITLAB_SCOPES, "state": state})
        return f"{s.gitlab_url.rstrip('/')}/oauth/authorize?{q}"

    @staticmethod
    async def exchange_code(code: str, redirect_uri: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.post(f"{s.gitlab_url.rstrip('/')}/oauth/token", data={
                "client_id": s.gitlab_client_id, "client_secret": s.gitlab_client_secret,
                "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri,
            })
        data = resp.json()
        if "access_token" not in data:
            raise SourceControlError(f"GitLab OAuth exchange failed: {data.get('error_description') or data}")
        return data
