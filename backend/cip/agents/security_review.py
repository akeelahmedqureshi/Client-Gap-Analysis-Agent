"""Security Review Agent (passive): live-site posture, vulnerable dependencies, insecure code patterns.

See ``connectors/research/security.py`` for exactly what is checked. Findings
become evidence, a transparent score/grade, and security gaps for the roadmap.
"""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.connectors.research.security import (
    SEVERITY_ORDER,
    Issue,
    code_issues,
    osv_lookup,
    probe_site,
    score,
)
from cip.core.code_scanner import Dependency
from cip.core.schemas import AgentResult, AgentStatus, Basis, Finding, Gap, GapType

GAP_GROUPS = [
    # (gap name, predicate over issues, description)
    ("HTTPS enforcement", lambda i: i.key == "https-redirect",
     "HTTP traffic is not redirected to HTTPS."),
    ("Web security hardening (headers & cookies)",
     lambda i: i.category == "web" and i.key != "https-redirect" and i.severity != "info",
     "Missing browser security headers and/or insecure cookie flags on the live site."),
    ("Vulnerable dependency remediation", lambda i: i.category == "dependency",
     "Declared dependencies have published vulnerabilities."),
    ("Secure coding fixes", lambda i: i.category == "code",
     "Insecure coding patterns were found in the reviewed files."),
    ("Vulnerability disclosure policy", lambda i: i.key == "security-txt",
     "No security.txt telling researchers how to report vulnerabilities."),
]


def _rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev)


class SecurityReviewAgent(Agent):
    name = "security_review"
    description = "Passive security review: website headers & TLS, vulnerable dependencies, insecure code patterns"
    after = ("client_research", "code_analysis")

    async def run(self, ctx: RunContext) -> AgentResult:
        s = ctx.settings
        if not s.security_review_enabled:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0, data={"enabled": False})
        rec = ctx.record
        issues: list[Issue] = []
        scope: list[str] = []
        site_summary: dict = {}

        # Live site — only if external research was allowed and ran.
        research = ctx.outputs.get("client_research")
        site = rec.project.url or (f"https://{rec.client.domain}" if rec.client.domain else None)
        if site and research is not None and research.status == AgentStatus.COMPLETED:
            web, site_summary = await probe_site(ctx.fetcher, site)
            issues += web
            if site_summary.get("checked"):
                scope.append(f"Live site {site_summary.get('final_url')}: headers, cookies, HTTPS redirect, security.txt")

        # Repository: dependencies (OSV) and code patterns in the reviewed files.
        profiles = ctx.data("code_analysis").get("profiles", [])
        repos = {r["full_name"]: r for r in ctx.data("repository").get("repositories", []) if r.get("accessible")}
        advisories: dict[str, tuple[Issue, list[str]]] = {}
        for prof in profiles:
            name = prof["full_name"]
            deps = [Dependency(**d) for d in prof.get("dependencies", [])]
            if deps and s.osv_enabled:
                for issue in await osv_lookup(ctx.fetcher, deps):
                    # The same advisory in several repositories is one thing to fix: merge, list the repos.
                    if issue.key in advisories:
                        advisories[issue.key][1].append(name)
                    else:
                        advisories[issue.key] = (issue, [name])
                scope.append(f"{len(deps)} declared dependencies of {name} checked against OSV.dev")
            repo = repos.get(name)
            if repo and repo.get("files"):
                base = repo.get("blob_base", prof["url"])
                found = code_issues(repo["files"],
                                    lambda path, line, base=base: f"{base}/{path}" + (f"#L{line}" if line else ""))
                for issue in found:
                    issue.key = f"{issue.key}:{name}"
                    issue.title = f"{issue.title.rsplit(' (', 1)[0]} ({name}: {issue.repository_path})"
                issues += found
                scope.append(f"{len(repo['files'])} reviewed files of {name} scanned for insecure patterns")
        for issue, repo_names in advisories.values():
            issue.detail += f" Affected repositories: {', '.join(repo_names)}."
            issues.append(issue)

        issues.sort(key=lambda i: _rank(i.severity))
        ledger = ctx.ledger
        out_issues = []
        for i in issues:
            ev = ledger.add(f"Security: {i.title}", i.source_url,
                            "github" if i.repository_path and i.category == "code" else
                            "advisory" if i.category == "dependency" else "website",
                            0.9 if i.category in ("web", "process") else 0.8,
                            extracted_text=i.extracted_text or None, repository_path=i.repository_path,
                            line_range=i.line_range)
            out_issues.append({"key": i.key, "title": i.title, "severity": i.severity, "category": i.category,
                               "detail": i.detail, "recommendation": i.recommendation, "references": i.references,
                               "evidence_id": ev.id})

        gaps: list[Gap] = []
        for name, pred, desc in GAP_GROUPS:
            group = [o for o, i in zip(out_issues, issues, strict=True) if pred(i)]
            if not group:
                continue
            worst = min((g["severity"] for g in group), key=_rank)
            gaps.append(Gap(name=name, category="Security", gap_type=GapType.SECURITY,
                            description=f"{desc} {len(group)} issue(s), highest severity: {worst}.",
                            evidence_ids=[g["evidence_id"] for g in group][:8],
                            confidence=0.8 if worst in ("critical", "high") else 0.7, basis=Basis.EVIDENCE))
        summary = score(issues)
        findings = [Finding(category=f"security.{o['category']}", title=f"[{o['severity']}] {o['title']}",
                            detail=o["recommendation"], evidence_ids=[o["evidence_id"]],
                            confidence=0.85, basis=Basis.EVIDENCE) for o in out_issues]
        if not scope:
            findings.append(Finding(category="security", title="Security review had nothing to inspect",
                                    detail="No reachable website and no accessible repository.", confidence=1.0))
        return AgentResult(
            confidence=0.8 if scope else 0.2,
            findings=findings,
            data={"enabled": True, **summary, "issues": out_issues, "site": site_summary, "scope": scope,
                  "gaps": [g.model_dump(mode="json") for g in gaps],
                  "note": "Passive review only (no scanning, fuzzing or exploitation). "
                          "Dependency versions from manifests are lower bounds unless pinned."},
        )
