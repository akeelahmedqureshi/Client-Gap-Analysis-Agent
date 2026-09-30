"""Secret scanning and redaction.

Runs before any repository content (or other text) is sent to an external LLM
provider. Sensitive files are skipped outright; everything else is scanned and
matching values are redacted in place.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

SENSITIVE_FILENAMES = {
    ".env", ".envrc", ".npmrc", ".pypirc", ".netrc", ".htpasswd", ".git-credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "credentials.json",
    "secrets.json", "secrets.yml", "secrets.yaml", "service-account.json",
    "master.key", "credentials.yml.enc", "terraform.tfstate", "terraform.tfstate.backup",
    "kubeconfig", ".dockercfg",
}
SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".tfstate", ".tfvars",
    ".ovpn", ".kdbx", ".asc", ".gpg",
)
SENSITIVE_PREFIXES = (".env.",)  # .env.local, .env.production …
SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template", ".env.dist"}


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    line: int


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|\Z)")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|or-v1-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("connection_string", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@'\"]+:[^\s@'\"]+@[^\s'\"]+")),
]

# key = "value" style assignments where the key name suggests a secret.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>[A-Za-z0-9_.\-]*(?:secret|password|passwd|pwd|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|client[_\-]?secret|auth)[A-Za-z0-9_.\-]*)
    (?P<sep>\s*[:=]\s*|\s*=>\s*)
    (?P<quote>['"]?)
    (?P<value>[^\s'"#,;]{8,})
    (?P=quote)
    """
)
_PLACEHOLDER = re.compile(
    r"^(?:\$\{?[A-Z_]+\}?|<[^>]+>|x{4,}|\*{4,}|changeme|change-me.*|your[_\-].*|example.*|dummy.*|test.*|process\.env.*|os\.environ.*|env\(.*|settings\..*|config\..*|none|null|true|false)$",
    re.IGNORECASE,
)


def is_sensitive_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in SAFE_ENV_TEMPLATES:
        return False
    if name in SENSITIVE_FILENAMES:
        return True
    if any(name.startswith(p) for p in SENSITIVE_PREFIXES):
        return True
    return name.endswith(SENSITIVE_SUFFIXES)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def scan(text: str) -> list[SecretMatch]:
    matches: list[SecretMatch] = []
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            matches.append(SecretMatch(kind, text.count("\n", 0, m.start()) + 1))
    for m in _ASSIGNMENT.finditer(text):
        value = m.group("value")
        if _PLACEHOLDER.match(value) or _entropy(value) < 3.0:
            continue
        matches.append(SecretMatch("assignment", text.count("\n", 0, m.start()) + 1))
    return matches


def redact(text: str) -> tuple[str, int]:
    """Return ``(redacted_text, number_of_redactions)``."""
    count = 0

    def _sub_kind(kind: str):
        def _r(_m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return f"[REDACTED:{kind}]"
        return _r

    for kind, pat in _PATTERNS:
        text = pat.sub(_sub_kind(kind), text)

    def _assign(m: re.Match[str]) -> str:
        nonlocal count
        value = m.group("value")
        if _PLACEHOLDER.match(value) or _entropy(value) < 3.0 or value.startswith("[REDACTED"):
            return m.group(0)
        count += 1
        q = m.group("quote")
        return f"{m.group('key')}{m.group('sep')}{q}[REDACTED:secret]{q}"

    text = _ASSIGNMENT.sub(_assign, text)
    return text, count
