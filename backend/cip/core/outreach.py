"""Personalised outreach email: claim-safe facts, generation and claim checking (BRS 7.19, PRD 10.21/10.50).

The email is written only from **facts** taken from the run's structured state (the sales summary):
each fact is a short statement with the evidence ids behind it, and only facts that are safe to say to
the client are included:

* competitive gaps with enough confidence, phrased as "not publicly identified" — never "you lack";
* customer pain from public app-store reviews and conversion/mobile UX issues on public pages;
* AI / automation opportunities, labelled as ideas (estimates), not observations;
* internal capabilities and case studies only when approved **and** client-facing; a case-study
  customer is named only when its record allows references.

Security weaknesses, internal-only knowledge and assumptions about the client's internal operations
are never included.

``check_claims`` re-checks any draft (LLM-written or human-edited): it flags internal-only names,
customers that may not be named, competitors that are not in the facts, security topics, and numbers
that do not appear in the facts. An LLM draft that fails the check is retried once, then replaced by
the deterministic template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from cip.core.llm import LLMClient, LLMError, LLMUnavailable

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ALWAYS_ALLOWED_NUMBERS = {"30"}  # "a 30-minute call"
SECURITY_WORDS = ("vulnerab", "security header", "exploit", "breach", "cve-", "insecure", "tls ", "csp")


@dataclass
class Fact:
    kind: str  # product | gap | pain | opportunity | capability | case_study
    text: str
    evidence_ids: list[str] = field(default_factory=list)
    estimate: bool = False
    email_text: str = ""  # natural wording for the template email (same claim, no new numbers)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "evidence_ids": self.evidence_ids, "estimate": self.estimate,
                "email_text": self.email_text}

    @property
    def say(self) -> str:
        return self.email_text or self.text


@dataclass
class OutreachInput:
    client: str
    product: str
    industry: str | None
    facts: list[Fact]
    competitors_allowed: list[str]
    competitors_all: list[str]
    forbidden_terms: list[str]  # internal-only record titles, customers that may not be named
    top_gaps: list[str]
    opportunity: str | None
    capability: str | None
    recipient: str | None = None

    def to_dict(self) -> dict:
        return {"client": self.client, "product": self.product, "industry": self.industry,
                "facts": [f.to_dict() for f in self.facts], "competitors_allowed": self.competitors_allowed,
                "competitors_all": self.competitors_all, "forbidden_terms": self.forbidden_terms,
                "top_gaps": self.top_gaps, "opportunity": self.opportunity, "capability": self.capability,
                "recipient": self.recipient}

    @classmethod
    def from_dict(cls, d: dict) -> OutreachInput:
        return cls(**{**d, "facts": [Fact(**f) for f in d.get("facts", [])]})


class EmailDraft(BaseModel):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=6000)


SYSTEM_PROMPT = """You are an experienced technology consultant writing a first outreach email to a company
you have researched. Write a short, natural, specific email (120-220 words) that:
- opens with one concrete observation about their product;
- mentions at most two competitive gaps, naming competitors only as given in the facts, and phrasing them as
  capabilities competitors offer that were "not publicly identified" for the client — never claim the client
  lacks something;
- mentions at most one AI or automation idea, presented as an idea, not a fact;
- mentions our relevant experience only from the capability/case_study facts, exactly as given;
- explains the business value in plain words and ends with a low-pressure call to action (a 30-minute call);
- uses no numbers, names or claims that are not in the facts, no hype, no buzzwords, no bullet lists of
  every finding, and placeholders [Your name] and [Your organization] for the signature.
Return JSON {"subject": "...", "body": "..."}."""


def build_prompt(inp: OutreachInput, instructions: str = "") -> str:
    lines = [f"Company: {inp.client}", f"Product: {inp.product}", f"Industry: {inp.industry or 'unknown'}",
             "Facts you may use (nothing else):"]
    lines += [f"- [{f.kind}{', idea' if f.estimate else ''}] {f.text}" for f in inp.facts]
    if instructions.strip():
        lines += ["", f"Additional instructions from the consultant: {instructions.strip()[:500]}"]
    return "\n".join(lines)


def check_claims(subject: str, body: str, inp: OutreachInput) -> list[str]:
    """Problems with a draft; an empty list means it passed."""
    text = f"{subject}\n{body}"
    low = text.lower()
    problems: list[str] = []
    for term in inp.forbidden_terms:
        if term and len(term) > 2 and term.lower() in low:
            problems.append(f"Mentions internal-only or non-referenceable information: “{term}”")
    allowed = {c.lower() for c in inp.competitors_allowed}
    for name in inp.competitors_all:
        if name.lower() not in allowed and re.search(rf"\b{re.escape(name.lower())}\b", low):
            problems.append(f"Names a competitor that is not supported by the facts: {name}")
    if any(w in low for w in SECURITY_WORDS):
        problems.append("Mentions security weaknesses; keep them out of outreach")
    fact_text = " ".join(f.text for f in inp.facts) + f" {inp.client} {inp.product}"
    fact_numbers = set(_NUMBER.findall(fact_text)) | ALWAYS_ALLOWED_NUMBERS
    unsupported = sorted({n for n in _NUMBER.findall(text) if n not in fact_numbers})
    if unsupported:
        problems.append(f"Uses numbers that are not in the facts: {', '.join(unsupported)}")
    return problems


def template_email(inp: OutreachInput) -> EmailDraft:
    """Deterministic, claim-safe email used without an LLM or when an LLM draft fails the claim check."""
    gaps = [f for f in inp.facts if f.kind == "gap"][:2]
    pains = [f for f in inp.facts if f.kind == "pain"][:1]
    opp = next((f for f in inp.facts if f.kind == "opportunity"), None)
    proof = next((f for f in inp.facts if f.kind == "case_study"), None) or \
        next((f for f in inp.facts if f.kind == "capability"), None)
    product = inp.product or inp.client
    topic = " and ".join(inp.top_gaps[:2]) or (inp.opportunity or "a few product ideas")
    subject = f"{topic} — ideas for {product}"[:200]

    para = [f"Hi {inp.client} team,", "",
            f"I spent some time looking at {product}"
            + (f" and how it compares with other {inp.industry} products" if inp.industry else
               " and how it compares with similar products") + "."]
    if gaps or pains:
        para += ["", "A few things stood out:"]
        para += [f"- {f.say}" for f in gaps + pains]
    if opp:
        para += ["", f"One idea that could be worth exploring: {opp.say}"]
    if proof:
        para += ["", f"This is close to work we have done before: {proof.say}"]
    para += ["", "Would a 30-minute call be useful to walk through the comparison and what it could mean for "
                 f"{product}? Happy to share the findings either way.",
             "", "Best regards,", "[Your name]", "[Your organization]"]
    return EmailDraft(subject=subject, body="\n".join(para))


@dataclass
class GeneratedEmail:
    subject: str
    body: str
    generated_by: str  # llm | template
    problems: list[str]
    notes: list[str]


async def generate_email(llm: LLMClient, inp: OutreachInput, instructions: str = "") -> GeneratedEmail:
    notes: list[str] = []
    prompt = build_prompt(inp, instructions)
    for attempt in range(2):
        try:
            draft = await llm.complete_json(SYSTEM_PROMPT, prompt, EmailDraft)
        except LLMUnavailable:
            break
        except LLMError as exc:
            notes.append(f"LLM drafting failed: {exc}")
            break
        problems = check_claims(draft.subject, draft.body, inp)
        if not problems:
            return GeneratedEmail(draft.subject.strip(), draft.body.strip(), "llm", [], notes)
        notes.append(f"LLM draft {attempt + 1} failed the claim check: {'; '.join(problems)}")
        prompt += "\n\nYour previous draft was rejected: " + "; ".join(problems) + ". Fix these and use only the facts."
    t = template_email(inp)
    return GeneratedEmail(t.subject, t.body, "template", check_claims(t.subject, t.body, inp), notes)
