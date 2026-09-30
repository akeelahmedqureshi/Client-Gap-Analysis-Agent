import json

import httpx
import pytest
from pydantic import BaseModel

from cip.config import Settings
from cip.core.evidence import EvidenceLedger
from cip.core.grounding import Grounder, SourceDoc, quote_in_text
from cip.core.llm import LLMError, OpenRouterClient


class Out(BaseModel):
    name: str
    score: int


def _settings(**kw):
    return Settings(openrouter_api_key="or-test-key", openrouter_model="openai/gpt-6-luna", llm_max_retries=1, **kw)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


async def test_openrouter_request_shape_and_parsing():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return _reply('```json\n{"name": "x", "score": 3}\n```')

    client = OpenRouterClient(_settings(), transport=httpx.MockTransport(handler))
    out = await client.complete_json("sys", "user text", Out)
    assert out == Out(name="x", score=3)
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer or-test-key"
    assert seen["body"]["model"] == "openai/gpt-6-luna"
    assert seen["body"]["response_format"] == {"type": "json_object"}


async def test_secrets_are_redacted_from_prompts():
    bodies = []

    def handler(req):
        bodies.append(req.content.decode())
        return _reply('{"name": "x", "score": 1}')

    client = OpenRouterClient(_settings(), transport=httpx.MockTransport(handler))
    stripe = "sk" + "_live_" + "abcdefghijklmnop123456"
    await client.complete_json("sys", f"config: AKIAIOSFODNN7EXAMPLE and {stripe}", Out)
    assert "AKIAIOSFODNN7EXAMPLE" not in bodies[0] and stripe not in bodies[0]


async def test_invalid_json_is_repaired_once_then_fails():
    replies = iter([_reply('{"name": "x"}'), _reply('{"name": "x", "score": 2}')])
    client = OpenRouterClient(_settings(), transport=httpx.MockTransport(lambda r: next(replies)))
    assert (await client.complete_json("s", "u", Out)).score == 2

    client = OpenRouterClient(_settings(), transport=httpx.MockTransport(lambda r: _reply("not json")))
    with pytest.raises(LLMError):
        await client.complete_json("s", "u", Out)


async def test_non_transient_errors_are_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = OpenRouterClient(_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError):
        await client.complete_json("s", "u", Out)
    assert len(calls) == 1


def test_grounding_drops_unknown_sources_and_downgrades_unverified_quotes():
    ledger = EvidenceLedger()
    g = Grounder(ledger, [SourceDoc("https://a.com/p", "We offer SMS reminders for every clinic appointment.", "website")])
    ok = g.ground("offers SMS", "https://a.com/p/", "SMS reminders for every clinic appointment")
    assert ok and ok.confidence == 0.85 and ok.extracted_text.startswith("SMS reminders")
    weak = g.ground("offers video", "https://a.com/p", "video consultations included")
    assert weak and weak.confidence <= 0.45
    assert g.ground("hallucinated", "https://made-up.com", "anything") is None
    assert len(ledger) == 2


def test_quote_matching_tolerates_punctuation():
    assert quote_in_text("Single sign-on (SSO) with Okta", "... single sign on SSO with okta, and more")
    assert not quote_in_text("short", "short text")
