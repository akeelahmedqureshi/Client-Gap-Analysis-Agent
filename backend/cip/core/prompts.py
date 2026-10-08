"""Prompt registry (BRS 39; PRD 10.37): every LLM prompt the agents use, with a stable id and a version.

The texts stay in the agent modules next to the code that relies on them; this module only names them.
An organization may override a prompt's text, and the model and temperature used per agent, through a
versioned configuration (services/config.py, kind ``llm``). Overrides never weaken the guarantees enforced
in code: outputs are still validated against the agent's schema and extracted facts are still grounded
(source URL + verbatim quote, core/grounding.py) before they reach the evidence ledger.
"""

from __future__ import annotations

import hashlib
import importlib

# id -> (module, attribute, agent, label)
PROMPTS: dict[str, tuple[str, str, str, str]] = {
    "client_research.extract": ("cip.agents.client_research", "SYSTEM_PROMPT", "client_research",
                                "Company facts extraction"),
    "product_features.extract": ("cip.agents.product_features", "SYSTEM_PROMPT", "product_features",
                                 "Product feature extraction"),
    "code_analysis.review": ("cip.agents.code_analysis", "SYSTEM_PROMPT", "code_analysis", "Repository review"),
    "industry_market.extract": ("cip.agents.industry_market", "PROMPT", "industry_market",
                                "Industry and market extraction"),
    "competitor_research.discovery": ("cip.agents.competitor_research", "DISCOVERY_PROMPT", "competitor_research",
                                      "Competitor discovery"),
    "competitor_research.profile": ("cip.agents.competitor_research", "PROFILE_PROMPT", "competitor_research",
                                    "Competitor verification and profile"),
    "prioritization.opportunity": ("cip.agents.prioritization", "SYSTEM_PROMPT", "opportunity_prioritization",
                                   "Opportunity narrative and ±1 factor adjustments"),
    "planning.patch_plan": ("cip.agents.planning", "SYSTEM_PROMPT", "enhancement_planning",
                            "Implementation (patch) plan"),
    "reporting.summary": ("cip.agents.reporting", "SUMMARY_PROMPT", "report", "Executive summary"),
    "outreach.email": ("cip.core.outreach", "SYSTEM_PROMPT", "outreach", "Outreach email draft"),
}
MAX_PROMPT_CHARS = 8000


def version(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def default_text(prompt_id: str) -> str:
    module, attr, _, _ = PROMPTS[prompt_id]
    return getattr(importlib.import_module(module), attr)


def registry(overrides: dict[str, str] | None = None) -> list[dict]:
    out = []
    for pid, (_, _, agent, label) in PROMPTS.items():
        default = default_text(pid)
        text = (overrides or {}).get(pid) or default
        out.append({"id": pid, "agent": agent, "label": label, "default": default, "text": text,
                    "overridden": text != default, "version": version(text), "default_version": version(default)})
    return out


def effective_versions(overrides: dict[str, str] | None = None) -> dict[str, str]:
    return {p["id"]: p["version"] for p in registry(overrides)}


def fingerprint(overrides: dict[str, str] | None = None) -> str:
    """One version for the whole prompt set, as recorded with every run."""
    return version("\n".join(f"{k}={v}" for k, v in sorted(effective_versions(overrides).items())))
