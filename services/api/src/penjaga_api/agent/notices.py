"""Read a pasted management notice into structured fields (PRD §17: staff paste a utility's
notice; Penjaga proposes the fields; staff check them and publish).

The model only proposes: the reply must be strict JSON that validates as a ``NoticeDraft``
(times with the house's +08:00 offset, end after start). Staff see every field before anything
is published, and the portal shows the issues ``draft_issues`` finds. The pasted text is the
management office's own notice; no homeowner data is sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from penjaga_api.agent.openrouter import LLM, LLMUnavailableError
from penjaga_api.notices import NoticeDraft

MAX_TOKENS = 1600
ATTEMPTS = 2
MAX_TEXT = 6000

SYSTEM = """You read notices of planned utility works for a township management office in
Selangor, Malaysia, and return their details as JSON. Notices may be in Malay or English; write
every field in plain English.

Rules:
- Use only what the notice says. Leave a field empty ("" or [] or null) when the notice doesn't
  give it. Never invent times, areas, phone numbers or reference numbers.
- Times are Malaysian time: write them as ISO 8601 with +08:00, e.g. 2026-10-20T09:00:00+08:00.
  The current date and time is given; use it to resolve a date with no year.
- type: water_disruption (water supply off), planned_power_outage (electricity off), works
  (road or pipe works without a supply cut), or general.
- utility: water, electricity or other.
- scheduled: true for planned works announced in advance ("berjadual", "dirancang",
  "scheduled", "planned"); false only for emergency or unplanned disruptions ("kecemasan",
  "tidak dijadualkan", "emergency").
- areas: each area or street the notice lists, as written.
- expected_recovery: when supply is expected to be fully back, if the notice says; staged or
  distance-based recovery goes in recovery_note.
- advice: the notice's own advice to residents, one short sentence each.
- title: a short plain title, e.g. "Scheduled water supply disruption for pipe works".
"""

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "notice",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "type", "utility", "scheduled", "title", "reason", "areas", "district",
                "starts_at", "ends_at", "expected_recovery", "recovery_note", "advice",
                "alternative_supply", "contact", "reference",
            ],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["water_disruption", "planned_power_outage", "works", "general"],
                },
                "utility": {"type": "string", "enum": ["water", "electricity", "other"]},
                "scheduled": {"type": "boolean"},
                "title": {"type": "string"},
                "reason": {"type": "string"},
                "areas": {"type": "array", "items": {"type": "string"}},
                "district": {"type": "string"},
                "starts_at": {"type": "string"},
                "ends_at": {"type": "string"},
                "expected_recovery": {"type": ["string", "null"]},
                "recovery_note": {"type": "string"},
                "advice": {"type": "array", "items": {"type": "string"}},
                "alternative_supply": {"type": ["string", "null"]},
                "contact": {"type": ["string", "null"]},
                "reference": {"type": ["string", "null"]},
            },
        },
    },
}  # fmt: skip


class NoticeUnreadableError(ValueError):
    """The model's reply couldn't be made into a valid notice."""


@dataclass(frozen=True)
class ParsedNotice:
    draft: NoticeDraft
    model: str | None


def _parse(content: str | None) -> NoticeDraft:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):  # an empty optional field means "not given"
            doc = {k: v for k, v in doc.items() if v != "" or k in ("reason", "district")}
        return NoticeDraft.model_validate(doc)
    except (ValueError, ValidationError) as exc:
        raise NoticeUnreadableError(str(exc)) from exc


async def read_notice(llm: LLM, text: str, *, now: str) -> ParsedNotice:
    """Fields for a pasted notice; one correction is allowed when the reply doesn't validate."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Now: {now}\n\nNotice:\n{text[:MAX_TEXT]}"},
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
            completion = await llm.complete(
                messages, response_format=RESPONSE_FORMAT, max_tokens=2 * MAX_TOKENS
            )
        try:
            return ParsedNotice(_parse(completion.content), completion.model)
        except NoticeUnreadableError as exc:
            problem = str(exc)
        messages += [
            completion.assistant_message(),
            {
                "role": "user",
                "content": f"[Penjaga system check] That can't be used: {problem}. Return only the "
                "JSON object, with times as ISO 8601 with +08:00 and the end after the start.",
            },
        ]
    raise NoticeUnreadableError(problem)
