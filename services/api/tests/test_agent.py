"""The agent with a scripted LLM: guardrails, case explanations, the chat tool loop, consent,
disclosure logging and the chat route (no live model in CI)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penjaga_api.agent.guardrails import check_text, plain
from penjaga_api.agent.openrouter import LLMUnavailableError
from penjaga_api.api import create_app
from penjaga_api.service import CONSENT_TEXTS, ApiService, AppLayer, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeLLM, FakeUpstream, call, finding, reply
from penjaga_contract.validate import load_context

INDEX = load_context().index()
TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}
H = {"Authorization": f"Bearer {TOKENS[Role.homeowner]}"}

GOOD_EXPLANATION = {
    "summary": (
        "Water has been flowing nonstop through your meter at about 15 L/min. Normal use always "
        "pauses, so this looks like a burst pipe or a tap left fully open."
    ),
    "confidence_note": "Detected: the flow didn't pause once in 60 minutes.",
    "what_to_do_now": ["Close the main stop valve", "Book a SPAN-registered plumber"],
    "watching": "Penjaga is watching the meter and will tell you when the flow stops.",
}


class Harness:
    def __init__(self, tmp_path: Path, llm: FakeLLM | None) -> None:
        self.up = FakeUpstream(episode_start="2025-11-01T00:00:00+08:00")
        self.up.house_time = "2025-11-03T12:00:00+08:00"
        self.store = ApiStore(tmp_path / "api.sqlite")
        self.llm = llm
        self.service = ApiService(self.store, self.up, llm=llm)
        self.client = TestClient(create_app(self.service, tokens=TOKENS, autostart=False))

    def follow(self, *docs: dict[str, Any]) -> None:
        for doc in docs:
            self.up.emit(doc)

        async def run() -> None:
            async def end_soon() -> None:
                await asyncio.sleep(0.05)
                self.up.end_streams()

            await asyncio.gather(self.service.follower.sync(), end_soon())

        asyncio.run(run())

    def case(self) -> dict[str, Any]:
        (case,) = self.client.get("/api/v1/cases", headers=H).json()
        found: dict[str, Any] = case
        return found

    def chat(self, message: str, **extra: Any) -> tuple[list[str], dict[str, Any]]:
        """POST /chat; the streamed deltas and the final message."""
        with self.client.stream(
            "POST", "/api/v1/chat", json={"message": message, **extra}, headers=H
        ) as r:
            assert r.status_code == 200, r.read()
            deltas, done = [], {}
            event = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("data: "):
                    data = json.loads(line.removeprefix("data: "))
                    if event == "delta":
                        deltas.append(data["text"])
                    elif event == "done":
                        done = data
        return deltas, done


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    made: list[Harness] = []

    def build(llm: FakeLLM | None) -> Harness:
        h = Harness(tmp_path / f"h{len(made)}", llm)
        made.append(h)
        return h

    yield build
    for h in made:
        h.store.close()


# ---------------------------------------------------------------------- guardrails


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Check circ_sockets_bed4 straight away.", "raw house IDs"),
        ("It is costing you about RM99 a month.", "numbers not in the facts"),
        ("This is not urgent; nothing to worry about.", "plays down"),
        ("Call a licensed electrician today.", "wrong trade term"),
    ],
)
def test_guardrails_catch_each_kind_of_failure(text: str, problem: str) -> None:
    facts = [{"rm_per_month": 58.58, "flow": "15 L/min"}]
    result = check_text(text, index=INDEX, facts=facts, severity="urgent")
    assert not result.ok
    assert problem in " ".join(result.problems)


def test_guardrails_pass_grounded_text() -> None:
    facts = [{"rm_per_month": 58.58, "lpd": 1015.7, "where": "Flexi-Suite bedroom (Bedroom 4)"}]
    text = "About 1,016 L a day leaks in Bedroom 4, costing about RM58.58 a month."
    assert check_text(text, index=INDEX, facts=facts, severity="urgent").ok


# ---------------------------------------------------------------------- explanations


def test_an_explanation_replaces_the_template_once_written(make: Any) -> None:
    h = make(FakeLLM([reply(json.dumps(GOOD_EXPLANATION))]))
    h.follow(finding("burst_pipe_suspected", first_seen="2025-11-03T10:00:00+08:00"))
    before = h.case()
    assert before["title"].startswith("Water has been flowing nonstop")
    assert "explanation" not in before  # the view never waits for the model
    assert asyncio.run(h.service.explain_pending()) == 1
    after = h.case()
    assert after["summary"].startswith("Water has been flowing nonstop through your meter")
    assert after["summary"].endswith("will tell you when the flow stops.")
    assert after["explanation"]["source"] == "ai"
    assert after["explanation"]["what_to_do_now"][1] == "Book a SPAN-registered plumber"
    # severity, safety and actions are never the model's
    assert after["severity"] == before["severity"] == "urgent"
    assert after["safety"] == before["safety"]
    assert after["actions"] == before["actions"]
    # the model saw facts, not IDs or household identifiers
    (request,) = h.llm.calls
    sent = json.dumps(request["messages"])
    assert "meter_water_main" not in sent
    assert "elmina_g6_demo" not in sent
    assert request["response_format"]["json_schema"]["strict"] is True
    # every LLM call is a logged disclosure
    (disclosure,) = h.store.disclosures()
    assert disclosure["recipient"].startswith("OpenRouter")
    assert "PDPA" in disclosure["basis"]
    # a case event tells the app to reload
    assert any(e.payload.get("explained") for e in h.store.events_after(0))


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        json.dumps({**GOOD_EXPLANATION, "summary": "It costs RM333 a month to leave this alone."}),
        json.dumps({**GOOD_EXPLANATION, "summary": "Nothing to worry about here, it's harmless."}),
        json.dumps({k: v for k, v in GOOD_EXPLANATION.items() if k != "watching"}),
    ],
)
def test_a_bad_explanation_keeps_the_template_and_is_not_retried(make: Any, bad: str) -> None:
    h = make(FakeLLM([reply(bad), reply(bad)]))
    h.follow(finding("burst_pipe_suspected", first_seen="2025-11-03T10:00:00+08:00"))
    template = h.case()
    assert asyncio.run(h.service.explain_pending()) == 0
    assert h.case()["summary"] == template["summary"]
    _, second = h.llm.calls  # one rewrite, told what was wrong
    assert "[Penjaga system check]" in second["messages"][-1]["content"]
    assert asyncio.run(h.service.explain_pending()) == 0
    assert len(h.llm.calls) == 2  # the same facts aren't sent again straight away


def test_one_bad_reply_then_a_good_one_is_used(make: Any) -> None:
    h = make(FakeLLM([reply("{not json"), reply(json.dumps(GOOD_EXPLANATION))]))
    h.follow(finding("burst_pipe_suspected", first_seen="2025-11-03T10:00:00+08:00"))
    h.case()
    assert asyncio.run(h.service.explain_pending()) == 1
    assert h.case()["explanation"]["source"] == "ai"


def test_an_explanation_may_have_nothing_to_do(make: Any) -> None:
    quiet = {
        "summary": "Mains power to your house was lost. Your board monitor reports the incoming "
        "supply is off, which is different from a tripped breaker inside your home.",
        "confidence_note": "Detected by the monitor at your distribution board.",
        "what_to_do_now": [],
        "watching": "Penjaga will tell you when the supply comes back.",
    }
    h = make(FakeLLM([reply(json.dumps(quiet))]))
    h.follow(finding("power_supply_interruption"))
    h.case()
    assert asyncio.run(h.service.explain_pending()) == 1


def test_an_unavailable_model_keeps_the_template(make: Any) -> None:
    h = make(FakeLLM([LLMUnavailableError("402", "out of credits")]))
    h.follow(finding("water_supply_interruption"))
    template = h.case()
    assert asyncio.run(h.service.explain_pending()) == 0
    assert h.case() == template


def test_switching_ai_off_stops_every_llm_call(make: Any) -> None:
    h = make(FakeLLM([reply(json.dumps(GOOD_EXPLANATION))]))
    h.follow(finding("burst_pipe_suspected"))
    r = h.client.put(
        "/api/v1/consent/AI",
        json={"enabled": False, "text_version": CONSENT_TEXTS[AppLayer.AI].version},
        headers=H,
    )
    assert r.status_code == 200
    assert h.up.layer_calls == []  # AI isn't a detection layer
    h.case()
    assert asyncio.run(h.service.explain_pending()) == 0
    _, done = h.chat("What's wrong at home?")
    assert done["source"] == "unavailable"
    assert h.llm.calls == []


def test_without_a_key_everything_is_templated(make: Any) -> None:
    h = make(None)
    h.follow(finding("burst_pipe_suspected"))
    assert "explanation" not in h.case()
    deltas, done = h.chat("What's wrong?")
    assert done["source"] == "unavailable"
    assert "can't answer questions right now" in "".join(deltas)
    assert done["message"]["chips"][0]["to"].startswith("/cases/")


# ---------------------------------------------------------------------- chat


def test_chat_uses_tools_and_offers_a_3d_chip(make: Any) -> None:
    reasoning = [{"type": "reasoning.text", "text": "look up the case"}]
    llm = FakeLLM()
    h = make(llm)
    h.follow(finding("burst_pipe_suspected", first_seen="2025-11-03T10:00:00+08:00"))
    case_id = h.case()["id"]
    llm.replies = [
        reply(None, call("c1", "get_case", case_id=case_id), reasoning=reasoning),
        reply(
            None,
            call(
                "c2",
                "focus_3d",
                object_ids=["valve_main_stop"],
                layer="water",
                label="Main stop valve",
            ),
        ),
        reply("Water is flowing nonstop at about 15 L/min. Close the main stop valve now."),
    ]
    deltas, done = h.chat("What's going on?")
    assert (
        "".join(deltas)
        == "Water is flowing nonstop at about 15 L/min. Close the main stop valve now."
    )
    assert done["source"] == "ai"
    assert done["message"]["chips"] == [
        {
            "label": "Show me: Main stop valve",
            "focus": {
                "layer": "water",
                "object_ids": ["valve_main_stop"],
                "severity": "info",
                "label": "Main stop valve",
            },
        }
    ]
    first, second, _ = llm.calls
    assert first["tools"]
    assert second["tools"]
    assistant = second["messages"][-2]
    assert assistant["reasoning_details"] == reasoning  # passed back unchanged
    tool = second["messages"][-1]
    assert tool["role"] == "tool"
    assert tool["tool_call_id"] == "c1"
    assert "Water has been flowing nonstop" in tool["content"]
    assert first["session_id"] == done["conversation_id"]


def test_an_unknown_id_in_a_tool_call_goes_back_to_the_model(make: Any) -> None:
    llm = FakeLLM(
        [
            reply(None, call("c1", "trace_supply", object_id="app_jacuzzi_roof")),
            reply("I can't find that appliance in your home record."),
        ]
    )
    h = make(llm)
    h.follow()
    _, done = h.chat("Where does the jacuzzi get power?")
    assert done["source"] == "ai"
    tool = llm.calls[1]["messages"][-1]
    assert "not in the house contract" in tool["content"]


def test_a_reply_that_fails_a_guardrail_gets_one_rewrite(make: Any) -> None:
    llm = FakeLLM(
        [
            reply("Fixing it costs RM777 and you should check circ_sockets_bed4."),
            reply("I don't have a cost for that yet. Your case shows what's known so far."),
        ]
    )
    h = make(llm)
    h.follow()
    _, done = h.chat("How much will it cost?")
    assert done["source"] == "ai"
    assert "RM777" not in done["message"]["text"]
    repair = llm.calls[1]["messages"][-1]["content"]
    assert "numbers not in the facts: 777" in repair  # not the 4 in an ID
    assert "raw house IDs" in repair


def test_a_second_failure_gives_a_safe_reply(make: Any) -> None:
    llm = FakeLLM([reply("It costs RM777."), reply("Still RM888.")])
    h = make(llm)
    h.follow()
    _, done = h.chat("How much?")
    assert done["source"] == "unreliable"
    assert "RM" not in done["message"]["text"]


def test_chat_history_is_kept_and_sent_back(make: Any) -> None:
    llm = FakeLLM([reply("Your home is quiet right now."), reply("Still quiet.")])
    h = make(llm)
    h.follow()
    _, first = h.chat("Anything wrong?")
    conversation = first["conversation_id"]
    _, second = h.chat("And now?", conversation_id=conversation)
    assert second["conversation_id"] == conversation
    sent = llm.calls[1]["messages"]
    assert {"role": "user", "content": "Anything wrong?"} in sent
    assert {"role": "assistant", "content": "Your home is quiet right now."} in sent
    history = h.client.get(
        "/api/v1/chat/history", params={"conversation_id": conversation}, headers=H
    ).json()
    assert [m["role"] for m in history] == ["owner", "penjaga", "owner", "penjaga"]
    assert len(h.store.disclosures()) == 2


def test_the_model_being_down_gives_a_polite_reply(make: Any) -> None:
    h = make(FakeLLM([LLMUnavailableError("503", "no provider")]))
    h.follow(finding("water_supply_interruption"))
    _, done = h.chat("What's up?")
    assert done["source"] == "unavailable"
    assert done["message"]["chips"]


def test_chat_route_rules(make: Any) -> None:
    h = make(FakeLLM())
    h.follow()
    long = h.client.post("/api/v1/chat", json={"message": "x" * 1001}, headers=H)
    assert long.status_code == 422
    bad_conversation = h.client.post(
        "/api/v1/chat", json={"message": "hi", "conversation_id": "../etc"}, headers=H
    )
    assert bad_conversation.status_code == 422
    portal = h.client.post(
        "/api/v1/chat",
        json={"message": "hi"},
        headers={"Authorization": f"Bearer {TOKENS[Role.management]}"},
    )
    assert portal.status_code == 403


def test_a_reply_cut_off_by_reasoning_gets_more_room(make: Any) -> None:
    llm = FakeLLM([LLMUnavailableError("length", "cut off"), reply(json.dumps(GOOD_EXPLANATION))])
    h = make(llm)
    h.follow(finding("burst_pipe_suspected", first_seen="2025-11-03T10:00:00+08:00"))
    h.case()
    assert asyncio.run(h.service.explain_pending()) == 1
    assert len(llm.calls) == 2


def test_a_case_the_model_looked_up_gets_an_open_button(make: Any) -> None:
    llm = FakeLLM()
    h = make(llm)
    h.follow(finding("water_supply_interruption"))
    case = h.case()
    llm.replies = [
        reply(None, call("c1", "get_case", case_id=case["id"])),
        reply(
            "The water supply to your house has stopped. Your roof tank keeps the bathrooms going."
        ),
    ]
    _, done = h.chat("Why is there no water?")
    open_button = {"label": f"Open: {case['title']}", "to": f"/cases/{case['id']}"}
    assert done["message"]["chips"] == [open_button]


def test_a_show_me_label_is_not_doubled(make: Any) -> None:
    show = call(
        "c1",
        "focus_3d",
        object_ids=["valve_main_stop"],
        layer="water",
        label="Show me the stop valve",
    )
    llm = FakeLLM([reply(None, show), reply("The main stop valve is at the front, by the meter.")])
    h = make(llm)
    h.follow()
    _, done = h.chat("Where is the stop valve?")
    assert done["message"]["chips"][0]["label"] == "Show me: the stop valve"


def test_a_reply_that_only_points_at_buttons_is_rewritten(make: Any) -> None:
    llm = FakeLLM(
        [
            reply(
                'Tap "Show me: the main stop valve" to see it. I can\'t book anything from here.'
            ),
            reply("Water is flowing nonstop at your meter. Tap the button to see the stop valve."),
        ]
    )
    h = make(llm)
    h.follow()
    _, done = h.chat("What's wrong?")
    assert done["message"]["text"].startswith("Water is flowing nonstop")
    assert "only points at buttons" in llm.calls[1]["messages"][-1]["content"]


def test_a_repeated_tool_call_gives_each_button_once(make: Any) -> None:
    show = call(
        "c1", "focus_3d", object_ids=["valve_main_stop"], layer="water", label="the stop valve"
    )
    again = {**show, "id": "c2"}
    answer = reply("It is by the meter at the front.")
    llm = FakeLLM([reply(None, show), reply(None, again), answer])
    h = make(llm)
    h.follow()
    _, done = h.chat("Where is the stop valve?")
    assert [c["label"] for c in done["message"]["chips"]] == ["Show me: the stop valve"]


def test_ai_text_drops_em_dashes_but_keeps_ranges() -> None:
    em = "\u2014"
    got = plain(f"About RM73 a month {em} 696 litres a day.")
    assert got == "About RM73 a month, 696 litres a day."
    assert plain("Typically RM150\u2013250.") == "Typically RM150\u2013250."
