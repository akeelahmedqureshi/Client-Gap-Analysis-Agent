from cip.connectors.source_control.base import (
    RepoRef,
    RepositoryAccessDenied,
    SourceControlError,
    SourceControlProvider,
    parse_repo_url,
)
from cip.connectors.source_control.github import GitHubProvider
from cip.connectors.source_control.gitlab import GitLabProvider


def provider_for(ref: RepoRef, token: str | None = None) -> SourceControlProvider:
    if ref.provider == "github":
        api = None if ref.host == "github.com" else f"https://{ref.host}/api/v3"
        return GitHubProvider(token=token, api_url=api)
    return GitLabProvider(token=token, base_url=f"https://{ref.host}")


__all__ = [
    "GitHubProvider",
    "GitLabProvider",
    "RepoRef",
    "RepositoryAccessDenied",
    "SourceControlError",
    "SourceControlProvider",
    "parse_repo_url",
    "provider_for",
]
