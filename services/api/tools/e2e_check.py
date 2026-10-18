"""End-to-end check of the Core API on the live stack: the whole flow, step by step, with a tick or a cross.

    uv run python scripts/dev_stack.py --speed 600      # first, in another terminal
    uv run python services/api/tools/e2e_check.py       # then this (about 3 minutes; one small AI call)

It drives the simulator as the Lab would (operator token), uses the Core API as the homeowner app and the
management portal do (their tokens from var/), and checks what comes back: detection -> case -> notice
linking -> urgent safety -> AI explanation and chat -> report to management -> reply on the owner's case
-> privacy switch -> disclosure log. It removes the faults and notices it created at the end. Needs house
time running fast (--speed 600); at 60x the waits are too short.
"""

# ruff: noqa: E501, PLR0915  (a linear script: one step per check, long spec lines)

from __future__ import annotations

import contextlib
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
API = "http://127.0.0.1:8705/api/v1"
SIM = "http://127.0.0.1:8702/sim/v1"
STREAM = "http://127.0.0.1:8701/stream/v1"


def _tokens() -> dict[str, str]:
    api = json.loads((ROOT / "var" / "api-tokens.json").read_text(encoding="utf-8"))
    dev = json.loads((ROOT / "var" / "dev-tokens.json").read_text(encoding="utf-8"))
    return {
        "homeowner": api["homeowner"],
        "management": api["management"],
        "operator": dev["operator"],
    }


TOKENS = _tokens()


def call(method: str, url: str, role: str, body: Any = None) -> Any:
    req = urllib.request.Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKENS[role]}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        done = next(b for b in raw.decode().split("\n\n") if b.startswith("event: done"))
        return json.loads(done.split("data: ", 1)[1])
    return json.loads(raw or b"null")


def wait_for(what: Callable[[], Any], seconds: float, every: float = 3.0) -> Any:
    end = time.time() + seconds
    while time.time() < end:
        found = what()
        if found:
            return found
        time.sleep(every)
    return None


results: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> bool:
    results.append((ok, text))
    print(f"  {'PASS' if ok else 'FAIL'}  {text}", flush=True)
    return ok


def cases() -> list[dict[str, Any]]:
    return call("GET", f"{API}/cases", "homeowner")


def case_where(pred: Callable[[dict[str, Any]], bool]) -> Callable[[], dict[str, Any] | None]:
    return lambda: next((c for c in cases() if pred(c)), None)


def fault(spec: dict[str, Any]) -> str:
    return call("POST", f"{SIM}/faults", "operator", {"progression": {"kind": "constant"}, **spec})[
        "fault_id"
    ]


def main() -> int:
    made_faults: list[str] = []
    made_notices: list[str] = []
    try:
        print("0. The stack is up")
        clock = call("GET", f"{STREAM}/clock", "operator")
        health = call("GET", f"{API}/diagnostics", "operator")
        check(health["detection_connected"], "the Core API follows detection")
        if not check(
            clock["speed"] >= 300, f"house time runs fast enough (speed {clock['speed']:g}x)"
        ):
            print("     start the stack with --speed 600")
            return 1

        print("1. A water cut with no notice becomes a case that says so")
        made_faults.append(
            fault(
                {
                    "fault_type": "U2",
                    "location_id": None,
                    "magnitude": {"value": 1, "unit": "1"},
                    "duration_s": 6 * 3600,
                }
            )
        )
        supply = wait_for(
            case_where(lambda c: c["layer"] == "L5" and c["status"] in ("open", "monitoring")), 120
        )
        if check(supply is not None, "detection found it and the Core API made a case"):
            check(
                "No notice from management" in " ".join(supply["impact"]["lines"]),
                "the case says no notice covers it",
            )

            print("2. A management notice for the same works changes how the case reads")
            notice = call(
                "POST",
                f"{API}/notices/seed",
                "operator",
                {"preset": "water_works", "starts_in_h": -1},
            )
            made_notices.append(notice["id"])
            matched = wait_for(
                case_where(lambda c: c["id"] == supply["id"] and "scheduled works" in c["title"]),
                30,
            )
            check(matched is not None, "the drop is explained as planned works, not a fault")
            view = call("GET", f"{API}/view", "homeowner")
            check(
                any(n.get("impact") for n in view["notices"]),
                "the notice shows what it means for this house",
            )

        print("3. A burst pipe is urgent and comes with the reviewed safety steps")
        made_faults.append(
            fault(
                {
                    "fault_type": "W5",
                    "location_id": "pipe_bath2_cold_supply",
                    "magnitude": {"value": 15, "unit": "L/min"},
                }
            )
        )
        burst = wait_for(
            case_where(lambda c: c["layer"] == "L1" and c["severity"] == "urgent"), 120
        )
        if check(burst is not None, "an urgent water case appeared"):
            check(bool(burst.get("safety")), "it carries the safety steps")
            check(
                bool(burst["impact"]["headline"]) and "L" in burst["impact"]["headline"],
                "it says how much water is escaping",
            )

            print("4. The AI explains the case and answers questions (one small AI call)")
            explained = wait_for(
                lambda: (
                    (c := call("GET", f"{API}/cases/{burst['id']}", "homeowner")).get("explanation")
                    and c
                ),
                90,
            )
            check(
                explained is not None,
                "an AI explanation replaced the template (or the AI is off: see 7)",
            )
            reply = call(
                "POST",
                f"{API}/chat",
                "homeowner",
                {"message": "What should I do right now?", "case_id": burst["id"]},
            )
            check(
                reply["source"] == "ai" and len(reply["message"]["text"]) > 40,
                "chat answered with the AI",
            )
            print("     ", reply["message"]["text"][:220].replace("\n", " "))

            print("5. The owner reports it; management answers; the owner sees it")
            preview = call(
                "POST",
                f"{API}/cases/{burst['id']}/actions/ticket/preview",
                "homeowner",
                {"params": {"note": "Water everywhere, please help", "contact": "in_app"}},
            )
            check(
                bool(preview.get("confirmation")),
                "the owner sees exactly what will be sent before confirming",
            )
            ticket = call(
                "POST",
                f"{API}/cases/{burst['id']}/tickets",
                "homeowner",
                {
                    "note": "Water everywhere, please help",
                    "contact": "in_app",
                    "confirmation": preview["confirmation"],
                },
            )
            inbox = call("GET", f"{API}/manage/tickets", "management")
            check(
                any(t["id"] == ticket["id"] for t in inbox),
                "the report reached the management portal",
            )
            call(
                "POST",
                f"{API}/manage/tickets/{ticket['id']}/status",
                "management",
                {"status": "scheduled"},
            )
            call(
                "POST",
                f"{API}/manage/tickets/{ticket['id']}/replies",
                "management",
                {"text": "A plumber is on the way."},
            )
            owner = call("GET", f"{API}/cases/{burst['id']}", "homeowner")
            texts = [e["text"] for e in owner["timeline"]]
            check("Management scheduled a visit" in texts, "the visit shows on the owner's case")
            check(
                "Management: A plumber is on the way." in texts,
                "the reply shows on the owner's case",
            )

        print("6. Switching AI off is respected")
        consent = {c["layer"]: c for c in call("GET", f"{API}/consent", "homeowner")}
        version = consent["AI"]["text_version"]
        call("PUT", f"{API}/consent/AI", "homeowner", {"enabled": False, "text_version": version})
        try:
            off = call("POST", f"{API}/chat", "homeowner", {"message": "Hello"})
            check(off["source"] != "ai", "with AI off, chat says so instead of calling the model")
        finally:
            call(
                "PUT", f"{API}/consent/AI", "homeowner", {"enabled": True, "text_version": version}
            )

        print("7. What left the house is logged (PDPA)")
        log = call("GET", f"{API}/disclosures", "homeowner")
        check(
            any("OpenRouter" in d["recipient"] for d in log),
            "every AI call is in the disclosure log",
        )
        check(
            any("ticket" in d["purpose"] for d in log),
            "the report to management is in the disclosure log",
        )
    except (urllib.error.URLError, OSError) as exc:
        check(False, f"could not reach the stack: {exc}. Is scripts/dev_stack.py running?")
    finally:
        for fid in made_faults:
            with contextlib.suppress(urllib.error.URLError):
                call("DELETE", f"{SIM}/faults/{fid}", "operator")
        for nid in made_notices:
            with contextlib.suppress(urllib.error.URLError):
                call("POST", f"{API}/manage/notices/{nid}/withdraw", "management")
    passed = sum(ok for ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
