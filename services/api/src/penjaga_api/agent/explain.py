"""LLM-written case explanations on top of the templated facts (PRD §18.2.3).

The model gets one case's facts (``tools.case_facts``: what, where, how sure, cost, actions,
evidence; no contract IDs, no raw readings, no household identifiers) and returns strict JSON. The
reply is validated with Pydantic and every sentence passes the guardrails; anything else raises
``ExplanationRejectedError`` and the case keeps its template. Severity and safety text are never
the model's: they stay exactly as the case builder set them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from penjaga_api.agent.guardrails import check_text
from penjaga_api.agent.openrouter import LLM, LLMUnavailableError
from penjaga_api.agent.tools import case_facts
from penjaga_contract.house import HouseIndex

SYSTEM = """You are Penjaga, a calm, competent home guardian for a family in Malaysia. You rewrite \
one case for the home owner in plain English, using ONLY the facts given.

Rules:
- Use only numbers that appear in the facts. Never calculate new ones (no totals, no conversions).
- Never change how serious the case is, and never play it down. Match the tone to the severity: \
relaxed for heads_up, direct and clear for urgent.
- Say how sure Penjaga is honestly: "detected", "suspected" or "possible", as the facts say.
- Use names of places and things exactly as the facts give them. Never write codes or IDs.
- Trades: always "ST-registered electrical contractor" and "SPAN-registered plumber". Never \
describe a tradesperson as "licensed", and use no other trade titles.
- Electrical steps for the owner stop at switching off, unplugging and pressing the RCD test \
button. Never suggest opening the distribution board or a socket.
- Don't repeat the safety instructions; they are shown separately.
- No emojis, no hype, no "As an AI".

Return JSON:
- summary: 2 or 3 sentences, under 70 words: what is happening, where, why it matters, and the \
cost of waiting if the facts give one. Leave out anything the other fields say.
- confidence_note: one sentence on how sure Penjaga is and why.
- what_to_do_now: 0 to 3 short steps, taken from the facts' actions (empty if there is \
nothing for the owner to do).
- watching: one sentence on what Penjaga will keep watching."""

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "case_explanation",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "confidence_note", "what_to_do_now", "watching"],
            "properties": {
                "summary": {"type": "string"},
                "confidence_note": {"type": "string"},
                "what_to_do_now": {"type": "array", "items": {"type": "string"}},
                "watching": {"type": "string"},
            },
        },
    },
}


PROMPT_VERSION = "2"  # bump when SYSTEM changes: stored explanations are rewritten
ATTEMPTS = 2
# reasoning tokens count against max_tokens: 900 cut off some explanations in the evaluation
MAX_TOKENS = 1800


class Explanation(BaseModel):
    summary: str = Field(min_length=40, max_length=900)
    confidence_note: str = Field(min_length=10, max_length=400)
    what_to_do_now: list[str] = Field(max_length=3)  # may be empty: often nothing to do
    watching: str = Field(min_length=10, max_length=300)


class ExplanationRejectedError(ValueError):
    """The model's explanation was malformed or failed a guardrail."""


@dataclass(frozen=True)
class Explained:
    explanation: Explanation
    model: str | None


def explanation_facts(case: dict[str, Any]) -> dict[str, Any]:
    facts = case_facts(case)
    facts.pop("case_id", None)
    return facts


def fingerprint(facts: dict[str, Any]) -> str:
    """Changes whenever the facts (a new revision, a cost, an action) or the prompt do, so a
    stored explanation is only reused for exactly what it was written from."""
    text = json.dumps(
        {"prompt": PROMPT_VERSION, "facts": facts}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _parse(content: str | None) -> Explanation:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return Explanation.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        raise ExplanationRejectedError(f"not a valid explanation: {exc}") from exc


async def explain(
    llm: LLM, facts: dict[str, Any], *, index: HouseIndex, severity: str, floor: bool
) -> Explained:
    """Ask for an explanation; one rewrite is allowed when the reply is malformed or fails a
    guardrail (the model is told exactly what was wrong). A second failure is final."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ]
    problem = ""
    for _attempt in range(ATTEMPTS):
        try:
            completion = await llm.complete(
                messages, response_format=RESPONSE_FORMAT, max_tokens=MAX_TOKENS
            )
        except LLMUnavailableError as exc:
            if exc.kind != "length":
                raise
            # the model's reasoning used the whole budget: one more try with twice the room
            completion = await llm.complete(
                messages, response_format=RESPONSE_FORMAT, max_tokens=2 * MAX_TOKENS
            )
        try:
            explanation = _parse(completion.content)
        except ExplanationRejectedError as exc:
            problem = str(exc)
        else:
            texts = [
                explanation.summary,
                explanation.confidence_note,
                *explanation.what_to_do_now,
                explanation.watching,
            ]
            problems: list[str] = []
            for text in texts:
                problems += check_text(
                    text, index=index, facts=[facts], severity=severity, floor=floor
                ).problems
            if not problems:
                return Explained(explanation, completion.model)
            problem = "; ".join(dict.fromkeys(problems))
        messages += [
            completion.assistant_message(),
            {
                "role": "user",
                "content": f"[Penjaga system check] That can't be used: {problem}. Return only the "
                "JSON object with summary, confidence_note, what_to_do_now and watching, using "
                "only the facts given.",
            },
        ]
    raise ExplanationRejectedError(problem)


def merge(case: dict[str, Any], explained: dict[str, Any]) -> dict[str, Any]:
    """The rendered case with an explanation in place of the template's wording."""
    out = dict(case)
    out["summary"] = f"{explained['summary']} {explained['watching']}".strip()
    out["confidence_note"] = explained["confidence_note"]
    out["explanation"] = {
        "source": "ai",
        "model": explained.get("model"),
        "what_to_do_now": explained["what_to_do_now"],
        "watching": explained["watching"],
    }
    return out
