"""GitHub/GitLab Intelligence Agent: repository metadata, tree, activity, key files.

Talks to repositories only through ``SourceControlProvider`` and fetches only
the files selected by the deterministic scanner. Sensitive files are never
fetched; fetched content is secret-redacted before it is stored.
"""

from __future__ import annotations

import asyncio
import logging

from cip.agents.base import Agent, ApprovalRequest, AwaitingApproval, RunContext
from cip.connectors.source_control import (
    RepositoryAccessDenied,
    SourceControlError,
    parse_repo_url,
    provider_for,
)
from cip.core.code_scanner import classify, select_important_files
from cip.core.schemas import AgentResult, Finding
from cip.core.security.secrets import redact

log = logging.getLogger(__name__)

MAX_REPOS = 3
STORED_FILE_CHARS = 30_000
MAX_TREE_PATHS_STORED = 20_000


class RepositoryAgent(Agent):
    name = "repository"
    description = "Analyze GitHub/GitLab repositories (metadata, structure, activity, key files)"
    after = ("csv_intake",)

    def _refs(self, ctx: RunContext):
        urls = ctx.record.sources.github + ctx.record.sources.gitlab
        refs = []
        for u in urls:
            ref = parse_repo_url(u)
            if ref and ref not in refs:
                refs.append(ref)
        return refs[:MAX_REPOS]

    def approval_needed(self, ctx: RunContext) -> ApprovalRequest | None:
        refs = self._refs(ctx)
        if not refs or "repository_access" in ctx.approvals:
            return None
        return ApprovalRequest(
            gate="repository_access",
            title="Access source code repositories",
            what="Repository metadata, file tree, recent commits/PRs/releases, dependency manifests, CI/CD and "
                 f"infrastructure config, README and up to {ctx.settings.repo_max_files_fetched} key source files.",
            why="To determine the project's architecture, technology stack, integrations and implemented features.",
            target=", ".join(r.url for r in refs),
            data_analyzed="Selected files only. Secret-bearing files (.env, keys, credentials) are never fetched; "
                          "remaining content is secret-scanned and redacted before any LLM analysis. "
                          "Private repositories use the organization's connected, encrypted OAuth token.",
        )

    async def run(self, ctx: RunContext) -> AgentResult:
        refs = self._refs(ctx)
        if not refs:
            return AgentResult(status="skipped", confidence=1.0,
                               findings=[Finding(category="repository", title="No GitHub/GitLab repository provided",
                                                 confidence=1.0)],
                               data={"repositories": []})
        repos: list[dict] = []
        findings: list[Finding] = []
        errors: list[str] = []
        evidence = []
        for ref in refs:
            token = await ctx.token_resolver(ref.provider, ref.host)
            factory = ctx.source_control_factory or provider_for
            provider = factory(ref, token)
            try:
                meta = await provider.get_repository(ref)
            except RepositoryAccessDenied:
                msg = (f"{ref.url} is private or does not exist"
                       + ("" if token else f" — connect {ref.provider.title()} to grant access"))
                findings.append(Finding(category="repository", title=msg, confidence=0.9))
                repos.append({"url": ref.url, "provider": ref.provider, "full_name": ref.full_name,
                              "accessible": False, "reason": msg})
                continue
            except SourceControlError as exc:
                errors.append(f"{ref.url}: {exc}")
                continue

            branch = meta.default_branch or "main"
            tree, languages, activity = await asyncio.gather(
                provider.get_tree(ref, branch),
                provider.get_languages(ref),
                provider.list_activity(ref, limit=10),
                return_exceptions=True,
            )
            if isinstance(tree, BaseException):
                errors.append(f"{ref.url}: could not read file tree: {tree}")
                tree = []
            languages = {} if isinstance(languages, BaseException) else languages
            activity = [] if isinstance(activity, BaseException) else activity
            paths = [t.path for t in tree]

            gate = f"large_repository_scan:{ref.full_name}"
            if len(paths) > ctx.settings.large_repo_file_threshold and gate not in ctx.approvals:
                raise AwaitingApproval(ApprovalRequest(
                    gate=gate, title=f"Large repository scan: {ref.full_name}",
                    what=f"{len(paths):,} files detected; only the tree and up to "
                         f"{ctx.settings.repo_max_files_fetched} key files will be fetched.",
                    why="Large repositories take longer and consume more API quota.",
                    target=ref.url, data_analyzed="Same as repository access (secret-filtered selected files).",
                ))

            fc = classify(paths)
            selected = select_important_files(fc, ctx.settings.repo_max_files_fetched)
            contents = await asyncio.gather(*(provider.get_file(ref, p, branch) for p in selected),
                                            return_exceptions=True)
            files: dict[str, str] = {}
            redactions = 0
            for path, content in zip(selected, contents):
                if isinstance(content, BaseException) or content is None:
                    continue
                if len(content) > ctx.settings.repo_max_file_bytes:
                    content = content[: ctx.settings.repo_max_file_bytes]
                clean, n = redact(content)
                redactions += n
                files[path] = clean[:STORED_FILE_CHARS]

            repo_url = ref.url
            ev = ctx.ledger.add(f"Repository {ref.full_name} ({'private' if meta.private else 'public'}), "
                                f"default branch {branch}, {len(paths)} files", repo_url, ref.provider, 1.0,
                                extracted_text=meta.description)
            evidence.append(ev)
            if fc.sensitive:
                sev = ctx.ledger.add(
                    f"Potentially sensitive files committed to repository: {', '.join(fc.sensitive[:5])}",
                    repo_url, ref.provider, 0.9, repository_path=fc.sensitive[0])
                evidence.append(sev)
                findings.append(Finding(category="security", title="Sensitive files committed to repository",
                                        detail=f"{len(fc.sensitive)} file(s) such as {fc.sensitive[0]} look like "
                                               "secrets/credentials; they were not read. Rotate and remove them.",
                                        evidence_ids=[sev.id], confidence=0.8))
            if redactions:
                findings.append(Finding(category="security",
                                        title=f"{redactions} hard-coded secret-like value(s) found and redacted",
                                        detail="Values matching credential patterns were detected in source files.",
                                        confidence=0.7))
            if meta.archived:
                findings.append(Finding(category="repository", title=f"{ref.full_name} is archived",
                                        evidence_ids=[ev.id], confidence=1.0))

            blob_base = provider.file_url(ref, "", branch).rstrip("/")
            repos.append({
                "url": repo_url, "provider": ref.provider, "host": ref.host, "full_name": ref.full_name,
                "accessible": True, "default_branch": branch, "private": meta.private,
                "description": meta.description, "topics": meta.topics, "pushed_at": meta.pushed_at,
                "archived": meta.archived, "languages": languages, "file_count": len(paths),
                "paths": paths[:MAX_TREE_PATHS_STORED], "files": files, "blob_base": blob_base,
                "skipped_sensitive_files": fc.sensitive, "redactions": redactions,
                "activity": [a.__dict__ for a in activity],
            })
            findings.append(Finding(category="repository", title=f"Analyzed {ref.full_name}",
                                    detail=f"{len(paths)} files, {len(files)} key files fetched",
                                    evidence_ids=[ev.id], confidence=1.0))

        accessible = [r for r in repos if r.get("accessible")]
        return AgentResult(
            status="completed",
            findings=findings,
            evidence=evidence,
            errors=errors,
            confidence=1.0 if accessible else 0.3,
            data={"repositories": repos},
            next_actions=["code_analysis"] if accessible else ["connect_source_control"],
        )
