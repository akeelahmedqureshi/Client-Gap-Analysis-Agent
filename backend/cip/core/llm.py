"""LLM access via OpenRouter (OpenAI-compatible chat completions API).

Every call returns a validated Pydantic model — agents never consume free-form
LLM text. All outbound prompt content passes through the secret redactor as a
last line of defence, even though repository content is already filtered by the
code-analysis pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from cip.core import usage

from cip.config import Settings, get_settings
from cip.core.security.secrets import redact

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when no LLM is configured; agents fall back to deterministic logic."""


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    async def complete_json(self, system: str, user: str, schema: type[T]) -> T: ...


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{[\s\S]*\}|\[[\s\S]*\])\s*```")


def extract_json(text: str) -> str:
    m = _JSON_BLOCK.search(text)
    if m:
        return m.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return text
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if end > start else text


class OpenRouterClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        if not self.settings.openrouter_api_key:
            raise LLMUnavailable("CIP_OPENROUTER_API_KEY is not configured")
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            # Optional OpenRouter attribution headers.
            "HTTP-Referer": self.settings.openrouter_site_url,
            "X-Title": self.settings.openrouter_app_name,
        }

    async def _post(self, payload: dict) -> dict:
        s = self.settings
        backoff = 2.0
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            base_url=s.openrouter_base_url, timeout=s.llm_timeout_seconds, transport=self._transport
        ) as client:
            for attempt in range(s.llm_max_retries + 1):
                try:
                    resp = await client.post("/chat/completions", json=payload, headers=self._headers())
                    if resp.status_code in (429, 500, 502, 503, 504):
                        raise LLMError(f"OpenRouter transient error {resp.status_code}: {resp.text[:200]}")
                    if resp.status_code >= 400:
                        raise LLMError(f"OpenRouter error {resp.status_code}: {resp.text[:500]}")
                    body = resp.json()
                    if "error" in body:
                        raise LLMError(f"OpenRouter error: {body['error']}")
                    return body
                except (httpx.TransportError, LLMError) as exc:
                    last_exc = exc
                    transient = isinstance(exc, httpx.TransportError) or "transient" in str(exc)
                    if not transient or attempt == s.llm_max_retries:
                        break
                    log.warning("LLM call failed (attempt %s): %s", attempt + 1, exc)
                    await asyncio.sleep(backoff)
                    backoff *= 2
        raise LLMError(str(last_exc))

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        json_schema = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        system_msg = (
            f"{system}\n\nRespond with a single JSON object only — no prose, no markdown. "
            f"It must validate against this JSON Schema:\n{json_schema}"
        )
        user_msg, n_redacted = redact(user)
        if n_redacted:
            log.info("Redacted %s secret-like values from LLM prompt", n_redacted)
        messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}]

        meter = usage.current()
        for attempt in range(2):
            if meter:
                try:
                    meter.check_llm()
                except usage.BudgetExhausted as exc:
                    raise LLMUnavailable(str(exc)) from exc
            payload = {
                "model": self.settings.openrouter_model,
                "messages": messages,
                "temperature": self.settings.llm_temperature,
                "response_format": {"type": "json_object"},
                "usage": {"include": True},  # OpenRouter reports tokens and cost
            }
            body = await self._post(payload)
            if meter:
                u = body.get("usage") or {}
                cost = u.get("cost")
                meter.record_llm(body.get("model") or self.settings.openrouter_model, int(u.get("prompt_tokens") or 0),
                                 int(u.get("completion_tokens") or 0), float(cost) if cost is not None else None)
            try:
                content = body["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"Unexpected OpenRouter response shape: {str(body)[:300]}") from exc
            try:
                return schema.model_validate_json(extract_json(content))
            except ValidationError as exc:
                if attempt == 1:
                    raise LLMError(f"LLM output failed schema validation: {exc}") from exc
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That JSON did not validate: {exc.errors()[:5]}. Return corrected JSON only."},
                ]
        raise LLMError("unreachable")  # pragma: no cover


class NullLLM:
    """Used when no API key is configured. Agents catch this and degrade gracefully."""

    async def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        raise LLMUnavailable("No LLM configured")


def get_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_enabled:
        return OpenRouterClient(settings)
    return NullLLM()
