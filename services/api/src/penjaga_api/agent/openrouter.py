"""OpenRouter chat completions over plain HTTP (https://openrouter.ai/docs, checked 2026-10-18).

- Privacy on every request (PRD §7.5, PDPA): ``provider = {data_collection: "deny", zdr: true,
  require_parameters: true}`` routes only to zero-data-retention endpoints that support every
  parameter sent (tools, structured outputs). The request carries no household identifier.
- Fallback models: a ``models`` list in priority order; the response's ``model`` names the
  one that answered.
- Errors: 408/429/502/503/504 are retried (honouring ``Retry-After``); everything else, an
  ``error`` body inside an HTTP 200, a ``finish_reason`` of ``error``, or an empty reply cut off by
  ``length`` raises ``LLMUnavailableError``, and the caller falls back to the templates. Budget
  and key-limit blocks come back as 402 or 403; both mean "unavailable".
- Reasoning models: ``reasoning_details`` is returned so the tool loop can pass it back unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2

from penjaga_api.agent.config import AgentConfig

log = logging.getLogger("penjaga_api.agent")

RETRYABLE = frozenset({408, 429, 502, 503, 504})
RETRYABLE_TYPES = frozenset({"rate_limit_exceeded", "provider_overloaded", "timeout", "server"})
MAX_ATTEMPTS = 3
MAX_WAIT_S = 10.0
PRIVACY = {"data_collection": "deny", "zdr": True, "require_parameters": True}


class LLMUnavailableError(RuntimeError):
    """The LLM can't answer (off, out of budget, refused, failed or cut off): use the fallback."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


@dataclass(frozen=True)
class Completion:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning_details: Any = None
    finish_reason: str | None = None
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def assistant_message(self) -> dict[str, Any]:
        """This reply as the assistant turn to send back in a tool loop (reasoning unchanged)."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.reasoning_details is not None:
            msg["reasoning_details"] = self.reasoning_details
        return msg


class LLM(Protocol):
    @property
    def models(self) -> list[str]: ...

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        session_id: str | None = None,
        max_tokens: int | None = None,
    ) -> Completion: ...


class OpenRouterClient:
    def __init__(self, config: AgentConfig, *, transport: httpx2.AsyncBaseTransport | None = None):
        if not config.api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        self.config = config
        self._client = httpx2.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "HTTP-Referer": config.app_url,
                "X-OpenRouter-Title": "Penjaga",
                "X-OpenRouter-App-Visibility": "hidden",
            },
        )

    @property
    def models(self) -> list[str]:
        return self.config.models

    async def aclose(self) -> None:
        await self._client.aclose()

    def request_body(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        session_id: str | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "models": self.models,
            "messages": list(messages),
            "max_tokens": max_tokens or self.config.max_tokens,
            "provider": dict(PRIVACY),
        }
        if self.config.reasoning_effort:
            # reasoning tokens count against max_tokens; the tasks need little of it
            body["reasoning"] = {"effort": self.config.reasoning_effort}
        if tools:
            body["tools"] = list(tools)  # re-sent on every request of a tool loop
            body["tool_choice"] = "auto"
        if response_format is not None:
            body["response_format"] = response_format
        if session_id:
            body["session_id"] = session_id  # sticky routing for one conversation
        return body

    async def complete(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        session_id: str | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        body = self.request_body(
            messages,
            tools=tools,
            response_format=response_format,
            session_id=session_id,
            max_tokens=max_tokens,
        )
        last = "unavailable"
        for attempt in range(MAX_ATTEMPTS):
            final = attempt == MAX_ATTEMPTS - 1
            try:
                response = await self._client.post("/chat/completions", json=body)
            except httpx2.TimeoutException as exc:
                last = f"timeout: {exc}"
                if final:
                    break
                await asyncio.sleep(_backoff(attempt))
                continue
            except httpx2.HTTPError as exc:
                raise LLMUnavailableError("network", str(exc)) from exc
            if response.status_code in RETRYABLE and not final:
                await asyncio.sleep(_retry_after(response) or _backoff(attempt))
                continue
            doc = _json(response)
            error = doc.get("error") if isinstance(doc, dict) else None
            if response.status_code >= 400 or error:
                kind = _error_type(error) or str(response.status_code)
                if error and kind in RETRYABLE_TYPES and not final:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise LLMUnavailableError(kind, _error_message(error, response))
            return _completion(doc)
        raise LLMUnavailableError("retries_exhausted", last)


def _completion(doc: dict[str, Any]) -> Completion:
    choices = doc.get("choices") or []
    if not choices:
        raise LLMUnavailableError("empty", "no choices in the response")
    choice = choices[0]
    message = choice.get("message") or {}
    finish = choice.get("finish_reason")
    content = message.get("content")
    tool_calls = list(message.get("tool_calls") or [])
    if finish == "error":
        raise LLMUnavailableError("finish_error", str(doc.get("error") or ""))
    if finish == "length" and not (content or "").strip() and not tool_calls:
        # reasoning tokens count against max_tokens: nothing usable came back
        raise LLMUnavailableError("length", "empty reply cut off by max_tokens")
    return Completion(
        content=content,
        tool_calls=tool_calls,
        reasoning_details=message.get("reasoning_details"),
        finish_reason=finish,
        model=doc.get("model"),
        usage=dict(doc.get("usage") or {}),
    )


def _json(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _error_type(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    meta = error.get("metadata")
    if isinstance(meta, dict) and isinstance(meta.get("error_type"), str):
        return str(meta["error_type"])
    code = error.get("code")
    return None if code is None else str(code)


def _error_message(error: Any, response: httpx2.Response) -> str:
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    return f"HTTP {response.status_code}"


def _retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(MAX_WAIT_S, max(0.0, float(value))) if value else None
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    return float(min(MAX_WAIT_S, 0.5 * 2**attempt))
