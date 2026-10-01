"""Passive, non-intrusive security checks.

Only what a normal visitor or a public database query would see:

* live site: response headers, cookie flags, HTTP→HTTPS redirect, server
  version disclosure, /.well-known/security.txt (RFC 9116);
* dependencies: declared versions checked against OSV.dev (public
  vulnerability database; only package names + versions are sent);
* code: insecure patterns in the files already selected for review.

No port scanning, fuzzing, authentication attempts or exploit checks — those
require an explicit security-testing agreement with the client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from cip.connectors.research.web import WebFetcher
from cip.core.code_scanner import Dependency, find_line

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_PENALTY = {"critical": 25, "high": 12, "medium": 5, "low": 2, "info": 0}


@dataclass
class Issue:
    key: str
    title: str
    severity: str
    category: str  # web | dependency | code | process
    detail: str
    recommendation: str
    source_url: str
    extracted_text: str = ""
    repository_path: str | None = None
    line_range: str | None = None
    references: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- live site

HEADER_CHECKS = [
    ("strict-transport-security", "medium", "HSTS header missing",
     "Browsers may be downgraded to HTTP.", "Send `Strict-Transport-Security: max-age=31536000; includeSubDomains`."),
    ("content-security-policy", "medium", "Content-Security-Policy missing",
     "No CSP to limit the impact of cross-site scripting.", "Define a Content-Security-Policy (start in report-only mode)."),
    ("x-content-type-options", "low", "X-Content-Type-Options missing",
     "Browsers may MIME-sniff responses.", "Send `X-Content-Type-Options: nosniff`."),
    ("referrer-policy", "low", "Referrer-Policy missing",
     "Full URLs may leak to third parties via the Referer header.", "Send `Referrer-Policy: strict-origin-when-cross-origin`."),
    ("permissions-policy", "info", "Permissions-Policy missing",
     "Powerful browser features are not explicitly restricted.", "Send a Permissions-Policy restricting unused features."),
]
_VERSIONED = re.compile(r"\d+\.\d+")


async def probe_site(fetcher: WebFetcher, url: str) -> tuple[list[Issue], dict]:
    issues: list[Issue] = []
    summary: dict = {"url": url, "checked": False}
    p = urlparse(url if "://" in url else f"https://{url}")
    https_url = f"https://{p.netloc}/"
    res = await fetcher.fetch_raw(https_url)
    if res is None:
        return issues, summary
    status, headers, final_url, _ = res
    summary.update({"checked": True, "status": status, "final_url": final_url,
                    "headers": {k: v for k, v in headers.items() if k.lower() in {
                        "strict-transport-security", "content-security-policy", "x-frame-options",
                        "x-content-type-options", "referrer-policy", "permissions-policy", "server", "x-powered-by"}}})
    lower = {k.lower(): v for k, v in headers.items()}

    def header_issue(key, sev, title, detail, rec):
        issues.append(Issue(f"header:{key}", title, sev, "web", detail, rec, final_url,
                            f"Response headers of {final_url} contain no {key}"))

    for key, sev, title, detail, rec in HEADER_CHECKS:
        if key not in lower:
            header_issue(key, sev, title, detail, rec)
    csp = lower.get("content-security-policy", "")
    if "x-frame-options" not in lower and "frame-ancestors" not in csp:
        header_issue("x-frame-options", "medium", "Clickjacking protection missing",
                     "Pages can be framed by other sites.",
                     "Send `X-Frame-Options: DENY` or CSP `frame-ancestors 'none'`.")
    for h in ("server", "x-powered-by"):
        if h in lower and _VERSIONED.search(lower[h]):
            issues.append(Issue(f"disclosure:{h}", "Server software version disclosed", "low", "web",
                                f"`{h}: {lower[h]}` reveals software versions to attackers.",
                                "Remove version details from response headers.", final_url, f"{h}: {lower[h]}"))
    for raw in headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0].strip()
        flags = raw.lower()
        missing = [f for f, ok in (("Secure", "secure" in flags), ("HttpOnly", "httponly" in flags),
                                   ("SameSite", "samesite" in flags)) if not ok]
        if missing:
            sev = "medium" if "Secure" in missing or "HttpOnly" in missing else "low"
            issues.append(Issue(f"cookie:{name}", f"Cookie '{name}' missing {', '.join(missing)}", sev, "web",
                                f"Set-Cookie for '{name}' lacks {', '.join(missing)}.",
                                "Set Secure, HttpOnly and SameSite on session cookies.", final_url,
                                raw.split(";")[0][:40] + "; " + "; ".join(x.strip() for x in raw.split(";")[1:])[:160]))

    # HTTP -> HTTPS
    http = await fetcher.fetch_raw(f"http://{p.netloc}/", follow_redirects=False)
    if http is not None:
        hstatus, hheaders, _, _ = http
        location = hheaders.get("location", "")
        redirects = hstatus in (301, 302, 307, 308) and location.startswith("https://")
        summary["http_redirects_to_https"] = redirects
        if not redirects:
            issues.append(Issue("https-redirect", "HTTP is not redirected to HTTPS", "high", "web",
                                f"http://{p.netloc}/ answered {hstatus} without redirecting to HTTPS.",
                                "Redirect all HTTP traffic to HTTPS (301) and enable HSTS.", f"http://{p.netloc}/",
                                f"HTTP {hstatus}" + (f", Location: {location}" if location else "")))

    sec = await fetcher.fetch_raw(f"https://{p.netloc}/.well-known/security.txt")
    has_policy = bool(sec and sec[0] == 200 and "contact:" in sec[3].lower())
    summary["security_txt"] = has_policy
    if not has_policy:
        issues.append(Issue("security-txt", "No vulnerability disclosure policy (security.txt)", "info", "process",
                            "There is no /.well-known/security.txt telling researchers how to report vulnerabilities.",
                            "Publish a security.txt (RFC 9116) with a contact and policy.",
                            f"https://{p.netloc}/.well-known/security.txt", "404 / no Contact field"))
    return issues, summary


# --------------------------------------------------------------------------- dependencies (OSV)

OSV_API = "https://api.osv.dev/v1"
_OSV_SEVERITY = {"critical": "critical", "high": "high", "moderate": "medium", "medium": "medium", "low": "low"}


def _fixed_versions(vuln: dict, package: str) -> list[str]:
    fixed = []
    for aff in vuln.get("affected", []):
        if (aff.get("package") or {}).get("name", "").lower() != package.lower():
            continue
        for r in aff.get("ranges", []):
            fixed += [e["fixed"] for e in r.get("events", []) if "fixed" in e]
    return list(dict.fromkeys(fixed))


async def osv_lookup(fetcher: WebFetcher, deps: list[Dependency], max_details: int = 30) -> list[Issue]:
    deps = [d for d in deps if d.version][:500]
    if not deps:
        return []
    batch = await fetcher.post_json(f"{OSV_API}/querybatch", {"queries": [
        {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version} for d in deps]})
    if not isinstance(batch, dict):
        return []
    hits: list[tuple[Dependency, str]] = []
    for dep, result in zip(deps, batch.get("results", []), strict=False):
        for v in (result or {}).get("vulns", []) or []:
            hits.append((dep, v["id"]))
    issues: list[Issue] = []
    for dep, vid in hits[:max_details]:
        vuln = await fetcher.get_json(f"{OSV_API}/vulns/{vid}") or {}
        sev = _OSV_SEVERITY.get(str((vuln.get("database_specific") or {}).get("severity", "")).lower(), "medium")
        aliases = [a for a in vuln.get("aliases", []) if a.startswith("CVE-")]
        fixed = _fixed_versions(vuln, dep.name)
        version_note = "" if dep.exact else f" (declared range; lower bound {dep.version} checked — confirm with the lockfile)"
        issues.append(Issue(
            f"osv:{vid}:{dep.name}", f"{dep.name} {dep.version}: {vuln.get('summary') or vid}", sev, "dependency",
            f"{vid}{' / ' + ', '.join(aliases) if aliases else ''} affects {dep.name} {dep.version}{version_note}.",
            f"Upgrade {dep.name} to {fixed[-1]} or later." if fixed else f"Review {vid} and upgrade {dep.name}.",
            f"https://osv.dev/vulnerability/{vid}", f"{dep.manifest}: {dep.name} {dep.version}",
            repository_path=dep.manifest, references=[f"https://osv.dev/vulnerability/{vid}"] + aliases))
    return issues


# --------------------------------------------------------------------------- code patterns

CODE_RULES: list[tuple[str, str, str, re.Pattern[str], str]] = [
    ("tls-verify-off", "high", "TLS certificate verification disabled",
     re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|"
                r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0", re.I),
     "Never disable certificate verification; fix the trust store instead."),
    ("jwt-unverified", "high", "JWT signature verification disabled",
     re.compile(r"verify_signature['\"]?\s*:\s*False|algorithms\s*[:=]\s*\[\s*['\"]none['\"]|"
                r"jwt\.decode\([^)]*verify\s*=\s*False", re.I),
     "Always verify JWT signatures with an explicit, non-'none' algorithm."),
    ("cors-wildcard", "medium", "CORS allows any origin",
     re.compile(r"origin\s*:\s*['\"]\*['\"]|Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*|"
                r"CORS_ORIGIN_ALLOW_ALL\s*=\s*True|allow_origins\s*=\s*\[\s*['\"]\*['\"]", re.I),
     "Restrict CORS to known origins, especially when credentials are allowed."),
    ("debug-on", "medium", "Debug mode enabled in configuration",
     re.compile(r"^\s*DEBUG\s*=\s*True|app\.run\([^)]*debug\s*=\s*True", re.M),
     "Disable debug mode in production; it exposes stack traces and internals."),
    ("weak-hash", "medium", "Weak hash (MD5/SHA-1) in use",
     re.compile(r"createHash\(\s*['\"](md5|sha1)['\"]|hashlib\.(md5|sha1)\(|Digest::(MD5|SHA1)", re.I),
     "Use bcrypt/argon2 for passwords and SHA-256+ elsewhere."),
    ("eval", "medium", "Dynamic code execution (eval)",
     re.compile(r"(?<![\w.])eval\s*\(|new\s+Function\s*\(", re.I),
     "Avoid eval/new Function on any data that could be user-controlled."),
    ("sql-concat", "high", "SQL built by string formatting",
     re.compile(r"(execute|query|raw)\s*\(\s*f?['\"`]\s*(SELECT|INSERT|UPDATE|DELETE)[^'\"`]*(\{|\$\{|%s|['\"`]\s*\+)", re.I),
     "Use parameterised queries / the ORM's query builder."),
]


def code_issues(files: dict[str, str], file_url) -> list[Issue]:
    issues: list[Issue] = []
    for path, content in files.items():
        if path.lower().endswith((".md", ".json", ".lock", ".txt", ".toml")):
            continue
        for key, sev, title, pat, rec in CODE_RULES:
            m = pat.search(content)
            if not m:
                continue
            line = find_line(content, m.group(0)[:30])
            issues.append(Issue(f"code:{key}:{path}", f"{title} ({path})", sev, "code",
                                f"Pattern found in {path}{f' line {line}' if line else ''}.", rec,
                                file_url(path, line), m.group(0)[:160], repository_path=path, line_range=line))
    return issues


def score(issues: list[Issue]) -> dict:
    penalty = {}
    for i in issues:
        penalty[i.severity] = penalty.get(i.severity, 0) + SEVERITY_PENALTY[i.severity]
    total = max(0, 100 - sum(penalty.values()))
    grade = "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D" if total >= 40 else "F"
    counts = {s: sum(1 for i in issues if i.severity == s) for s in SEVERITY_ORDER}
    return {"score": total, "grade": grade, "counts": counts, "penalties": penalty,
            "method": "100 minus per-issue penalties: " + ", ".join(f"{k} {v}" for k, v in SEVERITY_PENALTY.items())}
