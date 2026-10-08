"""Value proposition, use cases, customer problems and workflows (BRS 7.2; PRD 10.2).

Everything here is extracted deterministically from the client's crawled pages and its capability
inventory, and every statement carries a verbatim quote from a page (stored as evidence), so nothing is
invented. Workflows come from ``workflows.yaml``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from cip.core.evidence import EvidenceLedger
from cip.core.taxonomy import Taxonomy, _phrase_pattern

WORKFLOWS_PATH = Path(__file__).with_name("workflows.yaml")
USE_CASE_PATHS = ("use-case", "usecase", "solution", "industr", "who-we-serve", "for-")
PROBLEM_MARKERS = ("tired of", "struggl", "no more", "without the hassle", "stop wasting", "eliminate", "reduc",
                   "missed", "manual", "paperwork", "no-show", "frustrat", "time-consuming", "spreadsheet",
                   "lost revenue", "errors", "headache", "chasing", "double-booking", "back-and-forth")
SENTENCE = re.compile(r"[^.!?]{25,240}[.!?]")
MAX_ITEMS = 6
HAS = ("available", "partial")
AUDIENCE = re.compile(r"\bfor (?:small |busy |modern |independent |growing |all )?([a-z]+(?:[ -][a-z]+)?)\b")
NOT_AUDIENCE = {"free", "more", "every", "your", "you", "the", "all", "any", "details", "example", "instance", "sale",
                "less", "life", "today", "now", "years", "minutes", "teams of", "business"}


def audiences(texts: list[str], n: int = 3) -> list[str]:
    """Who the client says its product is *for* ("scheduling software for clinics" -> clinics)."""
    found: Counter = Counter()
    for t in texts:
        for m in AUDIENCE.finditer((t or "").lower()):
            phrase = m.group(1).strip()
            head = phrase.split()[0]
            if head not in NOT_AUDIENCE and phrase not in NOT_AUDIENCE and len(head) > 3:
                found[phrase if phrase.endswith("s") else head] += 1
    return [p for p, _ in found.most_common(n)]


@dataclass(frozen=True)
class Step:
    name: str
    features: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Workflow:
    id: str
    actor: str
    name: str
    description: str
    steps: tuple[Step, ...]


@lru_cache
def load_workflows(path: str | None = None) -> tuple[Workflow, ...]:
    raw = yaml.safe_load(Path(path or WORKFLOWS_PATH).read_text())
    return tuple(Workflow(id=w["id"], actor=w["actor"], name=w["name"], description=w.get("description", ""),
                          steps=tuple(Step(name=s["name"], features=tuple(s.get("features", [])),
                                           keywords=tuple(k.lower() for k in s.get("keywords", [])))
                                      for s in w["steps"]))
                 for w in raw["workflows"])


def _page_text(p: dict) -> str:
    return " ".join([p.get("title", ""), p.get("description", ""), " ".join(p.get("headings", [])), p.get("text", "")])


def _clean(s: str) -> str:
    return " ".join(s.split()).strip(" -–—|:")


def value_proposition(pages: list[dict], ledger: EvidenceLedger, client: str) -> dict | None:
    """The client's own headline promise: the home page's main heading and its description."""
    home = next((p for p in pages if p.get("headings") or p.get("description")), None)
    if not home:
        return None
    brand = {w for w in re.findall(r"[a-z0-9]+", f"{client} {home.get('title', '')}".lower())}
    # The first heading that says something beyond the brand name.
    heading = next((_clean(h) for h in home.get("headings", [])[:4]
                    if len(_clean(h)) >= 12 and set(re.findall(r"[a-z0-9]+", h.lower())) - brand), "")
    desc = _clean(home.get("description") or "")
    statement = heading or desc
    if not statement:
        return None
    support = desc if desc and desc != statement else ""
    quote = statement + (f" — {support}" if support else "")
    ev = ledger.add(f"{client}'s value proposition: {statement[:160]}", home["url"], "website", 0.85,
                    extracted_text=quote)
    return {"statement": statement, "supporting": support or None, "evidence_ids": [ev.id], "source_url": home["url"]}


def use_cases(pages: list[dict], ledger: EvidenceLedger, client: str, audiences: list[str]) -> list[dict]:
    """Use cases from solution / use-case / industry pages, plus "for <audience>" statements."""
    out: dict[str, dict] = {}
    for p in pages:
        path = p["url"].lower()
        if not any(h in path for h in USE_CASE_PATHS):
            continue
        for h in p.get("headings", [])[1:12]:
            h = _clean(h)
            if 6 <= len(h) <= 90 and h.lower() not in out:
                ev = ledger.add(f"{client} use case: {h}", p["url"], "website", 0.75, extracted_text=h)
                out[h.lower()] = {"name": h, "evidence_ids": [ev.id], "source_url": p["url"]}
            if len(out) >= MAX_ITEMS:
                break
    for a in audiences:
        if len(out) >= MAX_ITEMS:
            break
        for p in pages:
            m = next((m for field in (p.get("description", ""), *p.get("headings", []), p.get("text", ""))
                      if (m := re.search(rf"[^.!?]{{0,120}}\bfor {re.escape(a)}\b[^.!?]{{0,80}}", field or "",
                                         re.IGNORECASE))), None)
            if m:
                quote = _clean(m.group(0))
                key = f"for {a}".lower()
                if key not in out:
                    ev = ledger.add(f"{client} serves {a}", p["url"], "website", 0.7, extracted_text=quote)
                    out[key] = {"name": f"{a[:1].upper()}{a[1:]}", "evidence_ids": [ev.id], "source_url": p["url"],
                                "quote": quote}
                break
    return list(out.values())[:MAX_ITEMS]


def _candidates(p: dict) -> list[str]:
    """Headings and the description first (they are clean statements), then short sentences of body text."""
    out = [h for h in p.get("headings", []) if 15 <= len(h) <= 160] + [p.get("description") or ""]
    for m in SENTENCE.finditer(p.get("text", "")):
        sent = m.group(0)
        if "©" not in sent and len(sent.split()) <= 35 and sent.strip()[:1].isupper():
            out.append(sent)
    return out


def customer_problems(pages: list[dict], ledger: EvidenceLedger, client: str) -> list[dict]:
    """Problems the client says it solves: statements on its pages that name a pain (verbatim)."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in pages:
        for raw in _candidates(p):
            sentence = _clean(raw)
            low = sentence.lower()
            if len(sentence) < 15:
                continue
            marker = next((k for k in PROBLEM_MARKERS if k in low), None)
            if not marker or low in seen:
                continue
            seen.add(low)
            ev = ledger.add(f"Problem {client} addresses: {sentence[:160]}", p["url"], "website", 0.7,
                            extracted_text=sentence)
            out.append({"statement": sentence, "marker": marker, "evidence_ids": [ev.id], "source_url": p["url"]})
            if len(out) >= MAX_ITEMS:
                return out
    return out


def workflows(pages: list[dict], observations: dict[str, dict], taxonomy: Taxonomy, ledger: EvidenceLedger,
              client: str) -> list[dict]:
    """Map the client's capabilities and page mentions onto the workflow catalogue."""
    texts = [(p["url"], _page_text(p)) for p in pages]
    out = []
    for wf in load_workflows():
        steps = []
        for st in wf.steps:
            feats = [f for f in st.features if f in taxonomy]
            have = [f for f in feats if (observations.get(f) or {}).get("status") in HAS]
            evidence = [e for f in have for e in (observations[f].get("evidence_ids") or [])][:3]
            status = "supported" if have else "not_identified"
            mention = None
            if not have:
                for url, text in texts:
                    hit = next((k for k in st.keywords if _phrase_pattern(k).search(text)), None)
                    if hit:
                        idx = text.lower().find(hit)
                        quote = _clean(text[max(0, idx - 80): idx + len(hit) + 80])
                        ev = ledger.add(f"{client}'s site mentions “{hit}” ({wf.name}: {st.name})", url, "website",
                                        0.55, extracted_text=quote)
                        status, evidence, mention = "mentioned", [ev.id], hit
                        break
            steps.append({"name": st.name, "status": status, "features": have or feats, "evidence_ids": evidence,
                          "mention": mention})
        visible = [s for s in steps if s["status"] != "not_identified"]
        if not visible:
            continue
        supported = sum(1 for s in steps if s["status"] == "supported")
        out.append({"id": wf.id, "actor": wf.actor, "name": wf.name, "description": wf.description, "steps": steps,
                    "coverage": round(len(visible) / len(steps), 2), "supported": supported,
                    "next_steps": [s["name"] for s in steps if s["status"] == "not_identified"]})
    return sorted(out, key=lambda w: (-w["coverage"], w["name"]))
