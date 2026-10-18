"""'Ask Penjaga' (PRD §18.2.5): a tool-calling loop over the home's facts.

The model answers with tools (cases, the house record, readings summaries, costs, safety texts,
vendors) and may offer chips (show in 3D, open a case). The final text passes the guardrails before
anyone sees it; if it fails, the model gets one chance to rewrite it, then the owner gets a safe
reply. Out-of-scope requests get a short, polite redirect. Nothing is executed from chat: actions
are only proposed (step 7 runs them after the owner confirms).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from penjaga_api.agent.guardrails import check_text, plain
from penjaga_api.agent.openrouter import LLM, LLMUnavailableError
from penjaga_api.agent.tools import TOOLS, ToolBox

MAX_ROUNDS = 6
MAX_REPAIRS = 1
RETRY_MAX_TOKENS = 2400  # twice the default budget, for a reply cut off by reasoning
SEVERITY_RANK = {"info": 0, "heads_up": 1, "act_soon": 2, "urgent": 3}

SYSTEM = """You are Penjaga, a calm, competent, warm home guardian for a family in an SDP Elmina \
Green Six terrace house in Shah Alam, Malaysia. You answer the owner's questions about their \
home: where pipes and cables run, what a case means, what something costs, what to do, and \
maintenance.

How you work:
- Get every fact from your tools. Never guess a number, a place or a part of the house.
- Costs come only from estimate_cost or the case facts. Never do tariff arithmetic yourself.
- Use names, never codes or IDs, in what you write. IDs are only for tool arguments.
- When showing where something is would help, call focus_3d. To point at a case, call open_case.
- You only advise. You can't book, send or switch anything from chat; say what the owner can \
tap instead.
- Safety: for a hazard, quote get_safety_text and recommend a professional. Never give electrical \
or gas instructions that need a qualified person; owner steps stop at switching off, unplugging \
and pressing the RCD test button.
- Trades: always "ST-registered electrical contractor" and "SPAN-registered plumber". Never \
describe a tradesperson as "licensed", and use no other trade titles.
- Be honest about uncertainty: say "suspected" or "possible" when the facts do.
- Never play down a case that is urgent or act soon.
- If the request is not about the home (music, news, trivia, jokes), say in one sentence that \
you look after the home, and suggest something you can help with.
- Always answer the question itself in words first (what is wrong, what it means, what to do). \
Never reply with only buttons, and only mention a button you created with focus_3d or open_case.
- Plain English, short: usually under 100 words. Ringgit and litres. No emojis, no "As an AI"."""

UNAVAILABLE = (
    "I can't answer questions right now. Everything else still works: Penjaga is still watching "
    "your home, and your cases are up to date."
)
UNRELIABLE = (
    "I couldn't put together an answer I'm sure of for that. Your cases have the details I'm "
    "confident about."
)


@dataclass
class Answer:
    text: str
    chips: list[dict[str, Any]] = field(default_factory=list)
    source: str = "ai"  # ai | unavailable | unreliable
    model: str | None = None
    rounds: int = 0


BUTTON_TALK = re.compile(r"\b(tap|button|show me|open (?:the|this)|I can'?t (?:book|switch))", re.I)


# a promised 3D button must exist; the case card's own "Show me where" button doesn't count
SHOW_ME_TALK = re.compile(r"\bshow me\b(?! where)", re.I)


def only_buttons(text: str) -> bool:
    """True when every sentence just points at buttons or says what chat can't do."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return bool(sentences) and all(BUTTON_TALK.search(s) for s in sentences)


def _chips(toolbox: ToolBox) -> list[dict[str, Any]]:
    """The model's chips, each once (a repaired answer can call the same tool again); if it made
    none but looked a case up, a button to open that case."""
    if toolbox.chips:
        seen: set[str] = set()
        unique = []
        for chip in toolbox.chips:
            key = json.dumps(chip, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(chip)
        return unique
    if not toolbox.viewed:
        return []
    case = next(c for c in toolbox.cases if c["id"] == toolbox.viewed[0])
    return [{"label": f"Open: {case['title']}", "to": f"/cases/{case['id']}"}]


def context_message(cases: Sequence[dict[str, Any]], house_time: str, case_id: str | None) -> str:
    lines = [f"House time now: {house_time}."]
    open_ = [c for c in cases if c["status"] not in ("resolved", "dismissed")]
    if open_:
        lines.append("Open cases (use get_case for details):")
        lines += [f"- {c['id']}: {c['title']} ({c['severity']}, {c['location']})" for c in open_]
    else:
        lines.append("No open cases: the home is quiet.")
    if case_id:
        lines.append(f"The owner is looking at case {case_id}.")
    return "\n".join(lines)


async def answer(
    llm: LLM,
    toolbox: ToolBox,
    *,
    message: str,
    history: Sequence[dict[str, str]],
    context: str,
    session_id: str,
) -> Answer:
    """One reply; raises ``LLMUnavailableError`` when the model can't be reached."""
    open_cases = [c for c in toolbox.cases if c["status"] not in ("resolved", "dismissed")]
    severity = max(
        (c["severity"] for c in open_cases), key=SEVERITY_RANK.__getitem__, default="info"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "system", "content": context},
        *history,
        {"role": "user", "content": message},
    ]
    repairs = MAX_REPAIRS
    for round_ in range(1, MAX_ROUNDS + 1):
        try:
            completion = await llm.complete(messages, tools=TOOLS, session_id=session_id)
        except LLMUnavailableError as exc:
            if exc.kind != "length":
                raise
            # the model's reasoning used the whole budget: one more try with twice the room
            completion = await llm.complete(
                messages, tools=TOOLS, session_id=session_id, max_tokens=RETRY_MAX_TOKENS
            )
        if completion.tool_calls:
            messages.append(completion.assistant_message())
            for call in completion.tool_calls:
                fn = call.get("function") or {}
                output = await toolbox.run(str(fn.get("name")), str(fn.get("arguments") or "{}"))
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": output})
            continue
        text = (completion.content or "").strip()
        check = check_text(
            text,
            index=toolbox.service.index,
            facts=[*toolbox.outputs, context, message, json.dumps(history)],
            severity=severity,
        )
        if text and only_buttons(text):
            check.problems.append(
                "it only points at buttons: answer the question in words first, then the buttons"
            )
        if text and SHOW_ME_TALK.search(text) and not any(c.get("focus") for c in toolbox.chips):
            check.problems.append(
                "it mentions a 'Show me' button but none was made: call focus_3d with the element "
                "IDs, or leave the button out"
            )
        if text and check.ok:
            return Answer(plain(text), _chips(toolbox), "ai", completion.model, round_)
        if repairs == 0:
            return Answer(UNRELIABLE, [], "unreliable", completion.model, round_)
        repairs -= 1
        messages.append(completion.assistant_message())
        problems = "; ".join(check.problems) or "the reply was empty"
        messages.append(
            {
                "role": "user",
                "content": f"[Penjaga system check] That reply can't be shown: {problems}. Rewrite "
                "it using only facts from your tools, with names instead of IDs.",
            }
        )
    raise LLMUnavailableError("too_many_rounds", f"no answer after {MAX_ROUNDS} rounds")
