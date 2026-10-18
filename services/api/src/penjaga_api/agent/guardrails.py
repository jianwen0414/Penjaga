"""Guardrails on everything the LLM writes, enforced in code, not just in the prompt (PRD §18.4 🔒).

- **IDs:** text for the owner never contains a raw contract ID (names only); IDs in tool arguments
  are validated by the tools themselves (``validate_ids``).
- The number check proves a number came from the facts, not that it is used in the right place:
  the prompt, strict output and short answers limit that risk; the eval script measures it.
- **Numbers:** every number in the text must appear in the facts the model was given (the case
  facts, tool outputs, the owner's own message), allowing for rounding. Otherwise it was invented.
- **Severity:** the text may not play down a case that is ``act_soon``, ``urgent`` or held by a
  safety floor.
- **Trade terms:** "ST-registered electrical contractor" and "SPAN-registered plumber", never
  "licensed electrician/plumber" or "wireman".
A failed check means the answer is not shown: explanations fall back to the template, chat gets
one repair attempt and then a safe reply.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from penjaga_contract.house import HouseIndex

ID_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# digits after letters count too ("RM99", "2am"); a thousands separator needs three digits after it
NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\d)|(?<![\d.,])\d+(?:\.\d+)?(?!\d)")
BANNED_TRADE = re.compile(r"licensed (electrician|plumber)|wireman", re.IGNORECASE)
DOWNPLAY = re.compile(
    r"\b(not (?:urgent|serious|dangerous|a (?:big |real )?(?:problem|concern|worry))"
    r"|nothing to worry|no need to (?:worry|act|do anything|rush)|safe to ignore"
    r"|can (?:safely )?(?:ignore|wait)|harmless|no cause for concern)\b",
    re.IGNORECASE,
)
FREE_NUMBERS = frozenset({0.0, 1.0, 2.0})  # "one or two", "1 of 2": not facts to invent
RELATIVE_TOLERANCE = 0.02  # "about 1,000 L" for 1,016 L


@dataclass
class Check:
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def numbers_in(text: str) -> list[tuple[float, int]]:
    """(value, decimal places) of every number written in ``text``."""
    out = []
    for match in NUMBER.finditer(text):
        raw = match.group(0).replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        out.append((float(raw), decimals))
    return out


def fact_numbers(facts: Iterable[Any]) -> set[float]:
    """Every number in the facts: JSON numbers and numbers written inside strings."""
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int | float):
            found.add(float(node))
        elif isinstance(node, str):
            found.update(v for v, _ in numbers_in(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list | tuple):
            for value in node:
                walk(value)

    for fact in facts:
        if isinstance(fact, str):
            try:
                walk(json.loads(fact))
                continue
            except ValueError:
                pass
        walk(fact)
    return found


def _matches(value: float, decimals: int, known: set[float]) -> bool:
    if value in FREE_NUMBERS:
        return True
    step = 0.5 * 10**-decimals + 1e-9
    for fact in known:
        if abs(value - fact) <= step:  # written rounded to its shown precision
            return True
        if abs(fact) >= 10 and abs(value - fact) <= RELATIVE_TOLERANCE * abs(fact):
            return True
        if 0 < abs(fact) <= 1 and abs(value - fact * 100) <= step:  # 0.53 written as 53%
            return True
    return False


def check_text(
    text: str,
    *,
    index: HouseIndex,
    facts: Iterable[Any],
    severity: str | None = None,
    floor: bool = False,
) -> Check:
    check = Check()
    leaked = sorted({t for t in ID_TOKEN.findall(text) if index.exists(t)})
    if leaked:
        check.problems.append(f"raw house IDs in the text: {', '.join(leaked)} (use names)")
    known = fact_numbers(facts)
    words = ID_TOKEN.sub(" ", text)  # digits inside IDs ("bath2") are the ID check's business
    invented = sorted({f"{v:g}" for v, d in numbers_in(words) if not _matches(v, d, known)})
    if invented:
        check.problems.append(f"numbers not in the facts: {', '.join(invented)}")
    downplay = DOWNPLAY.search(text)
    if (floor or severity in ("act_soon", "urgent")) and downplay:
        check.problems.append(f"plays down a {severity} case: {downplay.group(0)!r}")
    if BANNED_TRADE.search(text):
        check.problems.append(
            "wrong trade term: say 'ST-registered electrical contractor' or "
            "'SPAN-registered plumber'"
        )
    return check
