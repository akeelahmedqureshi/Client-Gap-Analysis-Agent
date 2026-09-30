"""Code Intelligence Agent.

Repository ─▶ scanner ─▶ file classification ─▶ architecture extraction ─▶
dependency extraction ─▶ important-file detection ─▶ *targeted* LLM analysis.

The whole repository is never sent to an LLM: only README/manifests/route
files already selected and secret-redacted by the Repository agent.
"""

from __future__ import annotations

import logging
from collections import Counter

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.core.code_scanner import (
    LEGACY_VERSIONS,
    classify,
    detect_architecture,
    detect_from_dependencies,
    detect_from_paths,
    find_line,
    parse_dependencies,
)
from cip.core.grounding import Grounder, SourceDoc, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult, Finding, RepositoryProfile, TechSignal

log = logging.getLogger(__name__)


def file_url(repo: dict, path: str, line: str | None = None) -> str:
    url = f"{repo['blob_base']}/{path}"
    return f"{url}#L{line}" if line else url


class _LLMCodeFeature(BaseModel):
    feature_id: str
    status: str = "available"  # available | partial
    source_path: str
    quote: str = ""


class _LLMCodeInsights(BaseModel):
    summary: str = ""
    architecture_notes: list[str] = Field(default_factory=list)
    features: list[_LLMCodeFeature] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a senior software architect reviewing selected files of a repository
(README, dependency manifests, CI/infra config, routes). Identify which product features are implemented.
Use ONLY feature ids from the provided taxonomy. For each feature cite the repository file path (exactly as
shown after 'SOURCE') and a short verbatim quote from that file. Mark status 'partial' when the implementation
looks basic or incomplete. Do not guess features that are not evidenced in the files."""


class CodeAnalysisAgent(Agent):
    name = "code_analysis"
    description = "Detect languages, frameworks, architecture, infrastructure, AI usage and technical debt"
    requires = ("repository",)

    async def run(self, ctx: RunContext) -> AgentResult:
        repos = [r for r in ctx.data("repository").get("repositories", []) if r.get("accessible")]
        if not repos:
            return AgentResult(status="skipped", confidence=1.0, data={"profiles": [], "feature_signals": []})

        ledger = ctx.ledger
        findings: list[Finding] = []
        profiles: list[dict] = []
        feature_signals: list[dict] = []
        errors: list[str] = []

        for repo in repos:
            src_type = repo["provider"]
            paths: list[str] = repo.get("paths", [])
            files: dict[str, str] = repo.get("files", {})
            fc = classify(paths)
            techs: dict[tuple[str, str], TechSignal] = {}

            def add_tech(category: str, name: str, path: str, token: str, confidence: float = 0.9) -> None:
                content = files.get(path, "")
                line = find_line(content, f'"{token}"') or find_line(content, token) if content else None
                ev = ledger.add(f"{name} detected ({category.replace('_', ' ')})", file_url(repo, path, line),
                                src_type, confidence, repository_path=path, line_range=line,
                                extracted_text=f"{token} in {path}")
                key = (category, name)
                if key not in techs:
                    techs[key] = TechSignal(category=category, name=name, evidence_ids=[ev.id], confidence=confidence)
                elif ev.id not in techs[key].evidence_ids:
                    techs[key].evidence_ids.append(ev.id)

            # Languages (provider stats, falling back to file extensions)
            langs = repo.get("languages") or dict(fc.languages)
            total = sum(langs.values()) or 1
            for lang, n in sorted(langs.items(), key=lambda kv: -kv[1])[:6]:
                if n / total >= 0.02:
                    ev = ledger.add(f"Language {lang} ({n / total:.0%} of code)", repo["url"], src_type, 0.95)
                    techs[("language", lang)] = TechSignal(category="language", name=lang, evidence_ids=[ev.id],
                                                           confidence=0.95)

            # Dependencies -> technologies and feature signals
            all_deps: list[tuple[str, str]] = []
            for path, content in files.items():
                deps = parse_dependencies(path, content)
                all_deps += [(path, d) for d in deps]
                for hit in detect_from_dependencies(path, deps):
                    add_tech(hit.category, hit.name, hit.path, hit.token)
            for hit in detect_from_paths(paths):
                add_tech(hit.category, hit.name, hit.path, hit.token, 0.85)

            for path, dep in all_deps:
                for fid in ctx.taxonomy.match_code_signal(dep):
                    line = find_line(files.get(path, ""), dep)
                    ev = ledger.add(f"Dependency '{dep}' indicates {ctx.taxonomy.get(fid).name}",
                                    file_url(repo, path, line), src_type, 0.75, repository_path=path, line_range=line,
                                    extracted_text=dep)
                    feature_signals.append({"feature_id": fid, "status": "available", "evidence_id": ev.id,
                                            "source": "dependency", "confidence": 0.75})
            path_hits: Counter = Counter()
            for path in paths:
                for fid in ctx.taxonomy.match_code_signal(path):
                    if path_hits[fid] >= 1:
                        continue
                    path_hits[fid] += 1
                    ev = ledger.add(f"Code path '{path}' indicates {ctx.taxonomy.get(fid).name}",
                                    file_url(repo, path), src_type, 0.55, repository_path=path)
                    feature_signals.append({"feature_id": fid, "status": "partial", "evidence_id": ev.id,
                                            "source": "path", "confidence": 0.55})

            tech_names = {n for (_, n) in techs}
            architecture = detect_architecture(fc, tech_names, paths)
            arch_ev = []
            for style, why in architecture:
                ev = ledger.add(f"Architecture: {style} — {why}", repo["url"], src_type, 0.7)
                arch_ev.append(ev.id)
                findings.append(Finding(category="architecture", title=style, detail=why, evidence_ids=[ev.id],
                                        confidence=0.7))

            # Technical-debt and security indicators ---------------------------
            debt: list[str] = []
            has_tests = bool(fc.tests) or any(t.category == "testing" for t in techs.values())
            if not has_tests:
                debt.append("No automated tests detected")
            if not fc.ci:
                debt.append("No CI/CD pipeline configuration detected")
            if fc.manifests and not fc.lockfiles:
                debt.append("Dependency lockfile missing (non-reproducible builds)")
            if not any(t.category == "observability" for t in techs.values()):
                debt.append("No error monitoring / observability tooling detected")
            if not any(d for d in fc.docs if d.lower().split("/")[-1].startswith("readme")):
                debt.append("No README documentation")
            for path, content in files.items():
                for _name, pattern, label in LEGACY_VERSIONS:
                    if pattern.search(content):
                        line = find_line(content, pattern.search(content).group(0)[:20])
                        ev = ledger.add(f"Legacy dependency: {label}", file_url(repo, path, line), src_type, 0.85,
                                        repository_path=path, line_range=line)
                        debt.append(f"Legacy dependency: {label}")
                        findings.append(Finding(category="technical_debt", title=f"Legacy dependency: {label}",
                                                evidence_ids=[ev.id], confidence=0.85))
            todo_count = sum(c.count("TODO") + c.count("FIXME") for c in files.values())
            if todo_count >= 10:
                debt.append(f"{todo_count} TODO/FIXME markers in key files")
            for d in debt:
                if not d.startswith("Legacy"):
                    ev = ledger.add(f"Technical debt indicator: {d}", repo["url"], src_type, 0.7)
                    findings.append(Finding(category="technical_debt", title=d, evidence_ids=[ev.id],
                                            confidence=0.7, basis="inferred"))

            profile = RepositoryProfile(
                provider=repo["provider"], url=repo["url"], full_name=repo["full_name"],
                default_branch=repo.get("default_branch"), private=repo.get("private"),
                description=repo.get("description"), languages=langs, file_count=repo.get("file_count", 0),
                architecture=[s for s, _ in architecture], technologies=list(techs.values()),
                has_tests=has_tests, has_ci=bool(fc.ci),
                has_docker=any(t.name.startswith("Docker") for t in techs.values()), has_iac=bool(fc.iac),
                technical_debt_indicators=debt, skipped_sensitive_files=repo.get("skipped_sensitive_files", []),
            )
            profiles.append(profile.model_dump())
            for t in techs.values():
                findings.append(Finding(category=f"technology.{t.category}", title=t.name,
                                        evidence_ids=t.evidence_ids, confidence=t.confidence))

            # Targeted LLM analysis over the selected, redacted files ----------
            docs = [SourceDoc(p, c, src_type, repository_path=p) for p, c in files.items()]
            if docs:
                grounder = Grounder(ledger, [SourceDoc(file_url(repo, d.url), d.text, src_type, d.url) for d in docs])
                try:
                    insights = await ctx.llm.complete_json(
                        SYSTEM_PROMPT,
                        f"Repository: {repo['full_name']}\nDetected technologies: {sorted(tech_names)}\n"
                        f"Top-level directories: {dict(fc.top_dirs.most_common(20))}\n"
                        f"Route files: {fc.routes[:20]}\nDB files: {fc.db[:20]}\n\n"
                        f"Feature taxonomy:\n{ctx.taxonomy.describe_for_prompt()}\n\n"
                        f"{pages_to_prompt(docs, 40_000)}",
                        _LLMCodeInsights,
                    )
                    if insights.summary:
                        profiles[-1]["summary"] = insights.summary
                    profiles[-1]["architecture_notes"] = insights.architecture_notes[:10]
                    for f in insights.features:
                        if f.feature_id not in ctx.taxonomy:
                            continue
                        ev = grounder.ground(f"Code implements {ctx.taxonomy.get(f.feature_id).name}",
                                             file_url(repo, f.source_path), f.quote, 0.8)
                        if ev:
                            feature_signals.append({
                                "feature_id": f.feature_id,
                                "status": "partial" if f.status == "partial" else "available",
                                "evidence_id": ev.id, "source": "llm_code_review", "confidence": ev.confidence})
                except LLMUnavailable:
                    pass
                except LLMError as exc:
                    errors.append(f"LLM code review failed for {repo['full_name']}: {exc}")

        return AgentResult(
            findings=findings,
            evidence=[e for e in ledger.all() if e.source_type in ("github", "gitlab")],
            errors=errors,
            confidence=0.85,
            data={"profiles": profiles, "feature_signals": feature_signals},
        )
