"""Live evaluation of candidate models (manual; uses the OpenRouter key; never run in CI).

    uv run penjaga-api eval-agent --models deepseek/deepseek-v4.1-flash anthropic/claude-haiku-5.5

For each model, every finding type (synthetic, shaped like the detectors' output) and every captured
fixture is explained, and a fixed set of chat questions (including out-of-scope ones) is asked.
It scores what the guardrails and the PRD care about: valid explanations, guardrail passes, tool
use, out-of-scope redirects, latency, tokens and cost (prices read live from OpenRouter's public
model list). The report is Markdown, for ``plans/NOTES-agent-eval.md``.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx2

from penjaga_api.agent.config import AgentConfig
from penjaga_api.agent.explain import ExplanationRejectedError, explain, explanation_facts
from penjaga_api.agent.openrouter import Completion, LLMUnavailableError, OpenRouterClient
from penjaga_api.cases import build_cases
from penjaga_api.service import ApiService
from penjaga_api.store import ApiStore
from penjaga_api.templates import render
from penjaga_api.testing import FINDING_TYPES, FakeUpstream, finding, load_findings

FIXTURES = ["l1_leak", "l1_burst", "l2_elderly", "l2_l5_wet", "l3", "l5_water"]
QUESTIONS = [
    ("What's wrong at home?", "in_scope"),
    ("How much is this costing me?", "in_scope"),
    ("Where does the master bathroom water heater get its power?", "in_scope"),
    ("Show me the pipes in Bathroom 2.", "in_scope"),
    ("Is it safe to keep resetting the breaker?", "in_scope"),
    ("Play some music.", "out_of_scope"),
    ("Who won the football last night?", "out_of_scope"),
]
REDIRECT_WORDS = ("home", "house")


class _Metered:
    """Wraps the client to time every call and keep its usage."""

    def __init__(self, inner: OpenRouterClient) -> None:
        self.inner = inner
        self.calls: list[tuple[float, Completion]] = []

    @property
    def models(self) -> list[str]:
        return self.inner.models

    async def complete(self, messages: Any, **kwargs: Any) -> Completion:
        start = time.perf_counter()
        result = await self.inner.complete(messages, **kwargs)
        self.calls.append((time.perf_counter() - start, result))
        return result


@dataclass
class Score:
    model: str
    explained: int = 0
    rejected: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    chat_ok: int = 0
    chat_unreliable: list[str] = field(default_factory=list)
    chat_tools: int = 0
    redirects_ok: int = 0
    latencies: list[float] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    answered_by: set[str] = field(default_factory=set)


def _price(model: str) -> tuple[float, float] | None:
    try:
        doc = httpx2.get("https://openrouter.ai/api/v1/models", timeout=30).json()
    except (httpx2.HTTPError, ValueError):
        return None
    for m in doc.get("data", []):
        if m["id"] == model:
            p = m["pricing"]
            return float(p["prompt"]), float(p["completion"])
    return None


async def _evaluate(model: str, base: AgentConfig, work: Path) -> Score:
    config = replace(base, model=model, fallbacks=())
    client = _Metered(OpenRouterClient(config))
    upstream = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
    upstream.house_time = "2025-11-03T12:00:00+08:00"
    store = ApiStore(work / f"{model.replace('/', '_')}.sqlite")
    service = ApiService(store, upstream, llm=client)
    score = Score(model)
    docs: list[list[dict[str, Any]]] = [load_findings(name) for name in FIXTURES]
    docs += [[finding(t)] for t in FINDING_TYPES]
    try:
        for group in docs:
            now = max(d["updated_at"] for d in group)
            for state in build_cases("eval", group, [], service.index):
                case = render(state, service.index, service.names, now).case
                label = f"{state.lead['type']} ({state.id[:10]})"
                try:
                    await explain(
                        client,
                        explanation_facts(case),
                        index=service.index,
                        severity=state.severity,
                        floor=state.floor,
                    )
                    score.explained += 1
                except ExplanationRejectedError as exc:
                    score.rejected.append(f"{label}: {exc}"[:200])
                except LLMUnavailableError as exc:
                    score.unavailable.append(f"{label}: {exc}"[:200])
        for doc in load_findings("l1_burst"):
            upstream.emit(doc)
        await _follow(service, upstream)
        for question, kind in QUESTIONS:
            before = len(client.calls)
            try:
                reply = await service.chat(question)
            except LLMUnavailableError as exc:
                score.unavailable.append(f"chat {question!r}: {exc}"[:200])
                continue
            used = [c for _, c in client.calls[before:] if c.tool_calls]
            score.chat_tools += bool(used)
            text = reply["message"]["text"]
            if reply["source"] == "ai":
                score.chat_ok += 1
            else:
                score.chat_unreliable.append(f"{question!r}: {reply['source']}")
            if kind == "out_of_scope" and any(w in text.lower() for w in REDIRECT_WORDS):
                score.redirects_ok += 1
    finally:
        await client.inner.aclose()
        store.close()
    for seconds, result in client.calls:
        score.latencies.append(seconds)
        score.prompt_tokens += int(result.usage.get("prompt_tokens", 0))
        score.completion_tokens += int(result.usage.get("completion_tokens", 0))
        if result.model:
            score.answered_by.add(result.model)
    return score


async def _follow(service: ApiService, upstream: FakeUpstream) -> None:
    async def end_soon() -> None:
        await asyncio.sleep(0.05)
        upstream.end_streams()

    await asyncio.gather(service.follower.sync(), end_soon())


def report(scores: Sequence[Score]) -> str:
    lines = [
        "# Agent evaluation",
        "",
        f"Cases explained per model: {len(FIXTURES)} fixtures + {len(FINDING_TYPES)} finding "
        f"types; chat questions: {len(QUESTIONS)} (2 out of scope).",
        "",
        "| Model | Explanations OK | Rejected | Unavailable | Chat OK | Chat used tools | "
        "Out-of-scope redirected | Median latency | Tokens in/out | Cost (USD) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        total = s.explained + len(s.rejected) + len(s.unavailable)
        price = _price(s.model)
        cost = (
            f"{s.prompt_tokens * price[0] + s.completion_tokens * price[1]:.4f}" if price else "?"
        )
        median = f"{statistics.median(s.latencies):.1f}s" if s.latencies else "-"
        lines.append(
            f"| {s.model} | {s.explained}/{total} | {len(s.rejected)} | {len(s.unavailable)} | "
            f"{s.chat_ok}/{len(QUESTIONS)} | {s.chat_tools} | {s.redirects_ok}/2 | {median} | "
            f"{s.prompt_tokens}/{s.completion_tokens} | {cost} |"
        )
    for s in scores:
        lines += [
            "",
            f"## {s.model}",
            "",
            f"Answered by: {', '.join(sorted(s.answered_by)) or '-'}",
        ]
        for title, items in (
            ("Rejected by the guardrails", s.rejected),
            ("Unavailable", s.unavailable),
            ("Chat not shown", s.chat_unreliable),
        ):
            if items:
                lines += ["", f"{title}:", *[f"- {i}" for i in items]]
    return "\n".join(lines) + "\n"


def run(models: Sequence[str], out: Path, work: Path) -> int:
    base = AgentConfig.from_env()
    if not base.enabled:
        print("OPENROUTER_API_KEY is not set (put it in the backend's .env)")
        return 2
    work.mkdir(parents=True, exist_ok=True)
    scores = [asyncio.run(_evaluate(m, base, work)) for m in models]
    text = report(scores)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(json.dumps({"written": str(out)}))
    return 0
