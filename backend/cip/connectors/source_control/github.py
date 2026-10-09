"""GitHub REST API provider + OAuth helpers."""

from __future__ import annotations

import base64
from urllib.parse import quote, urlencode

import httpx

from cip.config import get_settings
from cip.core.net import api_verify
from cip.connectors.source_control.base import (
    ActivityItem,
    RepoMetadata,
    RepoRef,
    RepositoryAccessDenied,
    SourceControlError,
    SourceControlProvider,
    TreeEntry,
)

GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"
# Read-only scopes. "repo" is required for private repositories; GitHub has no
# finer-grained read-only OAuth scope for private code (use a GitHub App or
# fine-grained PAT for tighter scoping).
GITHUB_SCOPES = "read:user read:org repo"


class GitHubProvider(SourceControlProvider):
    name = "github"

    def __init__(self, token: str | None = None, api_url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._api_url = (api_url or get_settings().github_api_url).rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._api_url, headers=headers, timeout=30.0,
                                 transport=self._transport, follow_redirects=True,
                                 verify=api_verify())

    async def _get(self, path: str, params: dict | None = None, accept: str | None = None) -> httpx.Response:
        async with self._client() as c:
            resp = await c.get(path, params=params, headers={"Accept": accept} if accept else None)
        if resp.status_code in (401, 403, 404):
            if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                raise SourceControlError("GitHub API rate limit exceeded")
            raise RepositoryAccessDenied(f"GitHub returned {resp.status_code} for {path}")
        if resp.status_code >= 400:
            raise SourceControlError(f"GitHub API error {resp.status_code}: {resp.text[:200]}")
        return resp

    @staticmethod
    def _meta(ref: RepoRef, d: dict) -> RepoMetadata:
        return RepoMetadata(
            ref=ref,
            default_branch=d.get("default_branch"),
            private=d.get("private"),
            description=d.get("description"),
            stars=d.get("stargazers_count"),
            topics=d.get("topics") or [],
            pushed_at=d.get("pushed_at"),
            archived=bool(d.get("archived")),
        )

    async def get_repository(self, ref: RepoRef) -> RepoMetadata:
        d = (await self._get(f"/repos/{ref.full_name}")).json()
        return self._meta(ref, d)

    async def list_repositories(self, limit: int = 100) -> list[RepoMetadata]:
        if not self._token:
            return []
        resp = await self._get("/user/repos", params={"per_page": min(limit, 100), "sort": "pushed"})
        out = []
        for d in resp.json():
            host = (d.get("html_url") or "https://github.com/").split("/")[2]
            ref = RepoRef("github", host, d["full_name"])
            out.append(self._meta(ref, d))
        return out

    async def get_languages(self, ref: RepoRef) -> dict[str, int]:
        return (await self._get(f"/repos/{ref.full_name}/languages")).json()

    async def get_tree(self, ref: RepoRef, branch: str) -> list[TreeEntry]:
        d = (await self._get(f"/repos/{ref.full_name}/git/trees/{quote(branch, safe='')}",
                             params={"recursive": "1"})).json()
        return [TreeEntry(t["path"], t.get("size")) for t in d.get("tree", []) if t.get("type") == "blob"]

    async def get_file(self, ref: RepoRef, path: str, branch: str) -> str | None:
        try:
            resp = await self._get(f"/repos/{ref.full_name}/contents/{quote(path)}", params={"ref": branch})
        except RepositoryAccessDenied:
            return None
        d = resp.json()
        if isinstance(d, dict) and d.get("encoding") == "base64" and d.get("content") is not None:
            try:
                return base64.b64decode(d["content"]).decode("utf-8", errors="replace")
            except ValueError:
                return None
        return None

    async def list_activity(self, ref: RepoRef, limit: int = 10) -> list[ActivityItem]:
        items: list[ActivityItem] = []
        for path, kind in (
            (f"/repos/{ref.full_name}/commits", "commit"),
            (f"/repos/{ref.full_name}/pulls", "pull_request"),
            (f"/repos/{ref.full_name}/releases", "release"),
        ):
            try:
                data = (await self._get(path, params={"per_page": limit, "state": "all"})).json()
            except SourceControlError:
                continue
            for d in data[:limit]:
                if kind == "commit":
                    msg = (d.get("commit", {}).get("message") or "").splitlines()[0:1]
                    items.append(ActivityItem("commit", msg[0] if msg else d.get("sha", "")[:7],
                                              d.get("html_url"), None,
                                              d.get("commit", {}).get("author", {}).get("date")))
                else:
                    items.append(ActivityItem(kind, d.get("title") or d.get("name") or d.get("tag_name") or "",
                                              d.get("html_url"), d.get("state"), d.get("created_at")))
        return items

    def file_url(self, ref: RepoRef, path: str, branch: str, line_range: str | None = None) -> str:
        url = f"https://{ref.host}/{ref.full_name}/blob/{branch}/{path}"
        if line_range:
            a, _, b = line_range.partition("-")
            url += f"#L{a}" + (f"-L{b}" if b else "")
        return url

    # ---------------------------------------------------------------- OAuth
    @staticmethod
    def authorize_url(state: str, redirect_uri: str) -> str:
        s = get_settings()
        if not s.github_client_id:
            raise SourceControlError("GitHub OAuth is not configured (CIP_GITHUB_CLIENT_ID)")
        q = urlencode({"client_id": s.github_client_id, "redirect_uri": redirect_uri,
                       "scope": GITHUB_SCOPES, "state": state, "allow_signup": "false"})
        return f"{GITHUB_OAUTH_AUTHORIZE}?{q}"

    @staticmethod
    async def refresh_token(refresh_token: str) -> dict:
        """Only used when the OAuth app has expiring user tokens enabled."""
        s = get_settings()
        async with httpx.AsyncClient(timeout=30.0, verify=api_verify()) as c:
            resp = await c.post(GITHUB_OAUTH_TOKEN, headers={"Accept": "application/json"}, data={
                "client_id": s.github_client_id, "client_secret": s.github_client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token",
            })
        data = resp.json()
        if "access_token" not in data:
            raise SourceControlError(f"GitHub token refresh failed: {data.get('error_description') or data}")
        return data

    @staticmethod
    async def exchange_code(code: str, redirect_uri: str) -> dict:
        s = get_settings()
        async with httpx.AsyncClient(timeout=30.0, verify=api_verify()) as c:
            resp = await c.post(GITHUB_OAUTH_TOKEN, headers={"Accept": "application/json"}, data={
                "client_id": s.github_client_id, "client_secret": s.github_client_secret,
                "code": code, "redirect_uri": redirect_uri,
            })
        data = resp.json()
        if "access_token" not in data:
            raise SourceControlError(f"GitHub OAuth exchange failed: {data.get('error_description') or data}")
        return data
