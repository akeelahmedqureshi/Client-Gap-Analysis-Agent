"""Source-control provider abstraction.

Agents depend only on ``SourceControlProvider``; GitHub/GitLab specifics stay in
their own modules. Access tokens are held by the provider instance and are
never placed into agent state, evidence or LLM prompts.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

ProviderName = Literal["github", "gitlab"]


class SourceControlError(RuntimeError):
    pass


class RepositoryAccessDenied(SourceControlError):
    """The repository is private (or missing) and no authorized token is available."""


@dataclass(frozen=True)
class RepoRef:
    provider: ProviderName
    host: str
    full_name: str  # owner/name (GitLab: group/subgroup/name)

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.full_name}"

    @property
    def owner(self) -> str:
        return self.full_name.rsplit("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.rsplit("/", 1)[-1]


@dataclass
class RepoMetadata:
    ref: RepoRef
    default_branch: str | None
    private: bool | None
    description: str | None = None
    stars: int | None = None
    topics: list[str] = field(default_factory=list)
    pushed_at: str | None = None
    archived: bool = False


@dataclass(frozen=True)
class TreeEntry:
    path: str
    size: int | None = None


@dataclass
class ActivityItem:
    kind: Literal["commit", "branch", "pull_request", "merge_request", "issue", "release"]
    title: str
    url: str | None = None
    state: str | None = None
    created_at: str | None = None


def parse_repo_url(url: str) -> RepoRef | None:
    """Parse a GitHub or GitLab repository URL into a ``RepoRef``."""
    if not url:
        return None
    if url.startswith("git@"):
        # git@github.com:owner/repo.git
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][:-4]
    if host.endswith((".github.io", ".gitlab.io")):
        return None
    if host == "github.com" or host.startswith("github."):
        if len(parts) < 2:
            return None
        return RepoRef("github", host, f"{parts[0]}/{parts[1]}")
    if host == "gitlab.com" or host.startswith("gitlab."):
        # GitLab paths may include subgroups; stop at the "/-/" separator.
        if "-" in parts:
            parts = parts[: parts.index("-")]
        if len(parts) < 2:
            return None
        return RepoRef("gitlab", host, "/".join(parts))
    return None


class SourceControlProvider(abc.ABC):
    name: ProviderName

    @abc.abstractmethod
    async def get_repository(self, ref: RepoRef) -> RepoMetadata: ...

    @abc.abstractmethod
    async def list_repositories(self, limit: int = 100) -> list[RepoMetadata]: ...

    @abc.abstractmethod
    async def get_languages(self, ref: RepoRef) -> dict[str, int]: ...

    @abc.abstractmethod
    async def get_tree(self, ref: RepoRef, branch: str) -> list[TreeEntry]: ...

    @abc.abstractmethod
    async def get_file(self, ref: RepoRef, path: str, branch: str) -> str | None: ...

    @abc.abstractmethod
    async def list_activity(self, ref: RepoRef, limit: int = 10) -> list[ActivityItem]:
        """Recent commits, open PRs/MRs, issues and releases."""

    def file_url(self, ref: RepoRef, path: str, branch: str, line_range: str | None = None) -> str:
        raise NotImplementedError
