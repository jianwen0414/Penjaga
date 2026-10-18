"""Reviewed safety guidance texts, one per ``SafetyGuidanceKey`` (``data/safety_guidance.json``).

Urgent cases show these lines verbatim; nothing else (templates or, later, the LLM) writes safety
text. Each line carries its source, link and confidence; lines with no official source say so.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "safety_guidance.json"


@cache
def _texts() -> dict[str, list[dict[str, Any]]]:
    doc: dict[str, Any] = json.loads(DATA.read_text(encoding="utf-8"))
    texts: dict[str, list[dict[str, Any]]] = doc["texts"]
    return texts


def guidance_keys() -> frozenset[str]:
    return frozenset(_texts())


def safety_lines(key: str, *, circuit: str = "that circuit") -> list[str]:
    """The guidance for ``key``; ``KeyError`` if the knowledge base has no text for it."""
    return [str(line["text"]).replace("{circuit}", circuit) for line in _texts()[key]]


def safety_text(key: str) -> list[dict[str, Any]]:
    """Each line of the guidance for ``key`` with its source, link and confidence."""
    return [dict(line) for line in _texts()[key]]
