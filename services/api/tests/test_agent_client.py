"""The OpenRouter client over a mocked transport: request shape, privacy settings and every error
path that must fall back to the templates."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx2
import pytest

from penjaga_api.agent.config import AgentConfig
from penjaga_api.agent.openrouter import LLMUnavailableError, OpenRouterClient

CONFIG = AgentConfig(
    api_key="sk-or-test-not-a-real-key",
    model="deepseek/deepseek-v4.1-flash",
    fallbacks=("anthropic/claude-haiku-5.5",),
)
OK = {
    "model": "deepseek/deepseek-v4.1-flash",
    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Hello."}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)  # the client waits with asyncio.sleep
    return waits


def client(responses: list[httpx2.Response], seen: list[httpx2.Request]) -> OpenRouterClient:
    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return responses.pop(0)

    return OpenRouterClient(CONFIG, transport=httpx2.MockTransport(handle))


def run(c: OpenRouterClient, **kwargs: Any) -> Any:
    return asyncio.run(c.complete([{"role": "user", "content": "hi"}], **kwargs))


def test_every_request_carries_the_privacy_settings_and_fallbacks() -> None:
    seen: list[httpx2.Request] = []
    c = client([httpx2.Response(200, json=OK)], seen)
    result = run(c, tools=[{"type": "function"}], session_id="conv_0123456789abcdef")
    assert result.content == "Hello."
    assert result.model == "deepseek/deepseek-v4.1-flash"
    (request,) = seen
    body = json.loads(request.content)
    assert body["provider"] == {"data_collection": "deny", "zdr": True, "require_parameters": True}
    assert body["models"] == ["deepseek/deepseek-v4.1-flash", "anthropic/claude-haiku-5.5"]
    assert body["reasoning"] == {"effort": "low"}
    assert "model" not in body
    assert body["tools"] == [{"type": "function"}]
    assert body["tool_choice"] == "auto"
    assert body["session_id"] == "conv_0123456789abcdef"
    assert request.url.path == "/api/v1/chat/completions"
    assert request.headers["X-OpenRouter-Title"] == "Penjaga"
    assert request.headers["X-OpenRouter-App-Visibility"] == "hidden"
    assert request.headers["Authorization"] == "Bearer sk-or-test-not-a-real-key"
    # no household identifier anywhere in the request
    assert "elmina_g6_demo" not in request.content.decode()


def test_the_key_is_never_shown() -> None:
    assert "sk-or" not in repr(CONFIG)
    assert AgentConfig.from_env({}).enabled is False
    env = AgentConfig.from_env(
        {"OPENROUTER_API_KEY": "k", "PENJAGA_MODEL": "a/b", "PENJAGA_MODEL_FALLBACKS": "c/d, e/f"}
    )
    assert env.models == ["a/b", "c/d", "e/f"]


def test_rate_limits_are_retried_honouring_retry_after(no_sleep: list[float]) -> None:
    seen: list[httpx2.Request] = []
    c = client(
        [httpx2.Response(429, headers={"Retry-After": "3"}), httpx2.Response(200, json=OK)], seen
    )
    assert run(c).content == "Hello."
    assert len(seen) == 2
    assert no_sleep == [3.0]


def test_persistent_outages_give_up_after_three_attempts() -> None:
    seen: list[httpx2.Request] = []
    c = client([httpx2.Response(502) for _ in range(3)], seen)
    with pytest.raises(LLMUnavailableError):
        run(c)
    assert len(seen) == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_client_and_budget_errors_are_not_retried(status: int) -> None:
    seen: list[httpx2.Request] = []
    body = {
        "error": {"code": status, "message": "no", "metadata": {"error_type": "authentication"}}
    }
    c = client([httpx2.Response(status, json=body)], seen)
    with pytest.raises(LLMUnavailableError) as raised:
        run(c)
    assert len(seen) == 1
    assert raised.value.kind == "authentication"


def test_an_error_inside_an_http_200_is_an_error() -> None:
    seen: list[httpx2.Request] = []
    body = {"error": {"code": 400, "message": "provider failed", "metadata": {"error_type": "bad"}}}
    c = client([httpx2.Response(200, json=body)], seen)
    with pytest.raises(LLMUnavailableError, match="provider failed"):
        run(c)


def test_an_empty_reply_cut_off_by_max_tokens_is_unusable() -> None:
    seen: list[httpx2.Request] = []
    cut = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
    c = client([httpx2.Response(200, json=cut)], seen)
    with pytest.raises(LLMUnavailableError) as raised:
        run(c)
    assert raised.value.kind == "length"


def test_reasoning_details_go_back_unchanged_in_the_tool_loop() -> None:
    seen: list[httpx2.Request] = []
    details = [{"type": "reasoning.encrypted", "data": "opaque-blob", "id": "r1"}]
    tool_reply = {
        "model": "deepseek/deepseek-v4.1-flash",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "reasoning_details": details,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {"name": "x", "arguments": "{}"},
                        }
                    ],
                },
            }
        ],
    }
    c = client([httpx2.Response(200, json=tool_reply)], seen)
    result = run(c)
    message = result.assistant_message()
    assert message["reasoning_details"] == details
    assert message["tool_calls"][0]["id"] == "t1"
