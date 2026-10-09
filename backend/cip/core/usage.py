"""LLM token, cost and web-request tracking with per-run and per-agent budgets (BRS 16, 20.5; PRD 10.46).

A ``UsageMeter`` belongs to a run. The orchestrator makes it current (a context variable) for the run
and tags each agent's work with the agent's name, so the LLM client, the web fetcher and the search
provider can record usage without being passed the run context:

* **LLM**: calls, prompt/completion tokens, model, and cost (OpenRouter's reported cost, otherwise the
  configured per-million-token prices);
* **web**: HTTP requests made while researching (pages, robots.txt, APIs), search queries, pages served
  from the research cache, and failed fetches with their reason (timeout, http_503, robots_disallowed…).

Budgets (``CIP_RUN_LLM_TOKEN_BUDGET``, ``CIP_AGENT_LLM_TOKEN_BUDGET``, ``CIP_RUN_WEB_REQUEST_BUDGET``;
0 = unlimited). When one is exhausted the LLM reports itself *unavailable* and web requests fail, so
agents fall back to their deterministic paths instead of failing; the budget event is recorded.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

_meter: ContextVar[UsageMeter | None] = ContextVar("cip_usage_meter", default=None)
_agent: ContextVar[str] = ContextVar("cip_usage_agent", default="")


def _blank() -> dict:
    return {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
            "web_requests": 0, "search_queries": 0, "cache_hits": 0, "web_failures": 0, "models": {}}


MAX_FAILURES_KEPT = 25


class BudgetExhausted(Exception):
    pass


@dataclass
class UsageMeter:
    run_token_budget: int = 0
    agent_token_budget: int = 0
    web_request_budget: int = 0
    price_in_per_million: float = 0.0
    price_out_per_million: float = 0.0
    search_cost: float = 0.0
    totals: dict = field(default_factory=_blank)
    by_agent: dict[str, dict] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    failures: dict[str, list[dict]] = field(default_factory=dict)  # agent -> [{url, reason, attempts}]

    @classmethod
    def from_settings(cls, s) -> UsageMeter:
        return cls(run_token_budget=s.run_llm_token_budget, agent_token_budget=s.agent_llm_token_budget,
                   web_request_budget=s.run_web_request_budget, price_in_per_million=s.llm_price_input_per_million,
                   price_out_per_million=s.llm_price_output_per_million, search_cost=s.search_cost_per_query)

    def preload(self, usages: dict[str, dict]) -> None:
        """Usage of agents completed in an earlier pass of the same run (resume) counts toward the budget."""
        for agent, u in usages.items():
            if not u:
                continue
            self.by_agent[agent] = {**_blank(), **{k: v for k, v in u.items() if k != "failures"},
                                    "models": dict(u.get("models", {}))}
            self._add(self.totals, u)

    @staticmethod
    def _add(target: dict, u: dict) -> None:
        for k in ("llm_calls", "prompt_tokens", "completion_tokens", "total_tokens", "web_requests", "search_queries",
                  "cache_hits", "web_failures"):
            target[k] += int(u.get(k, 0))
        target["cost_usd"] = round(target["cost_usd"] + float(u.get("cost_usd", 0.0)), 6)
        for m, t in (u.get("models") or {}).items():
            target["models"][m] = target["models"].get(m, 0) + t

    def _bucket(self) -> dict:
        return self.by_agent.setdefault(_agent.get() or "unattributed", _blank())

    # --- LLM ----------------------------------------------------------------------------------
    def check_llm(self) -> None:
        if self.run_token_budget and self.totals["total_tokens"] >= self.run_token_budget:
            self._event(f"Run LLM token budget ({self.run_token_budget:,}) exhausted")
            raise BudgetExhausted("LLM token budget for this run is exhausted")
        agent = self._bucket()
        if self.agent_token_budget and agent["total_tokens"] >= self.agent_token_budget:
            self._event(f"{_agent.get()}: agent LLM token budget ({self.agent_token_budget:,}) exhausted")
            raise BudgetExhausted("LLM token budget for this agent is exhausted")

    def record_llm(self, model: str, prompt: int, completion: int, cost: float | None = None) -> None:
        if cost is None:
            cost = (prompt * self.price_in_per_million + completion * self.price_out_per_million) / 1_000_000
        u = {"llm_calls": 1, "prompt_tokens": prompt, "completion_tokens": completion,
             "total_tokens": prompt + completion, "cost_usd": cost, "models": {model: prompt + completion}}
        self._add(self.totals, u)
        self._add(self._bucket(), u)

    # --- web ----------------------------------------------------------------------------------
    def record_web(self, search: bool = False) -> None:
        if self.web_request_budget and self.totals["web_requests"] + self.totals["search_queries"] >= \
                self.web_request_budget:
            self._event(f"Run web request budget ({self.web_request_budget:,}) exhausted")
            raise BudgetExhausted("web request budget for this run is exhausted")
        key = "search_queries" if search else "web_requests"
        u = {key: 1, "cost_usd": self.search_cost if search else 0.0}
        self._add(self.totals, {**_blank(), **u})
        self._add(self._bucket(), {**_blank(), **u})

    def record_cache_hit(self) -> None:
        self._add(self.totals, {**_blank(), "cache_hits": 1})
        self._add(self._bucket(), {**_blank(), "cache_hits": 1})

    def record_web_failure(self, url: str, reason: str, attempts: int = 1, detail: str = "") -> None:
        self._add(self.totals, {**_blank(), "web_failures": 1})
        self._add(self._bucket(), {**_blank(), "web_failures": 1})
        log = self.failures.setdefault(_agent.get() or "unattributed", [])
        if len(log) < MAX_FAILURES_KEPT:
            log.append({"url": url[:500], "reason": reason, "attempts": attempts, **({"detail": detail[:200]} if detail else {})})

    def _event(self, text: str) -> None:
        if text not in self.events:
            self.events.append(text)

    def agent_usage(self, agent: str) -> dict | None:
        u = self.by_agent.get(agent)
        if not (u and any(u[k] for k in ("llm_calls", "web_requests", "search_queries", "cache_hits", "web_failures"))):
            return None
        return {**u, "failures": list(self.failures[agent])} if self.failures.get(agent) else dict(u)

    def summary(self) -> dict:
        return {**self.totals, "budgets": {"run_tokens": self.run_token_budget, "agent_tokens": self.agent_token_budget,
                                           "web_requests": self.web_request_budget}, "events": list(self.events)}


def current() -> UsageMeter | None:
    return _meter.get()


def current_agent() -> str:
    return _agent.get()


def activate(meter: UsageMeter):
    return _meter.set(meter)


def deactivate(token) -> None:
    _meter.reset(token)


def set_agent(name: str):
    return _agent.set(name)


def reset_agent(token) -> None:
    _agent.reset(token)


def total_usage(usages: list[dict | None]) -> dict:
    out = _blank()
    for u in usages:
        if u:
            UsageMeter._add(out, u)
    return out
