"""The Core API service: the store, the findings follower, cases, consent and the live
event feed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from penjaga_api import maintenance
from penjaga_api.actions import Actions, management_name
from penjaga_api.agent.chat import UNAVAILABLE, Answer, answer, context_message
from penjaga_api.agent.explain import (
    ExplanationRejectedError,
    explain,
    explanation_facts,
    fingerprint,
    merge,
)
from penjaga_api.agent.notices import read_notice
from penjaga_api.agent.openrouter import LLM, LLMUnavailableError
from penjaga_api.agent.tools import ToolBox
from penjaga_api.cases import DISMISS, CaseState, build_cases, dismissable, epoch_ms
from penjaga_api.costs import DAYS_PER_MONTH, Baseline, CostEstimate, estimate
from penjaga_api.follower import FindingFollower
from penjaga_api.ids import validate_ids
from penjaga_api.names import Names
from penjaga_api.notices import (
    NOTICE_KIND,
    SUPPLY_FINDINGS,
    NoticeDraft,
    NoticeMatch,
    back_by,
    draft_issues,
    during_recovery,
    explains,
    from_preset,
    reaches_house,
    to_view,
)
from penjaga_api.store import ApiStore, OutboxEvent
from penjaga_api.tariffs import NoTariffError
from penjaga_api.templates import (
    GUIDED_TYPES,
    CaseExtras,
    Rendered,
    clock,
    render,
    series_points,
    smart,
)
from penjaga_api.upstream import Upstream, UpstreamUnavailableError
from penjaga_contract.house import HouseIndex
from penjaga_contract.resources import load_house_document
from penjaga_contract.validate import load_context

log = logging.getLogger("penjaga_api.service")


class Role(StrEnum):
    homeowner = "homeowner"
    management = "management"
    operator = "operator"


class AppLayer(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    AI = "AI"  # not a detection layer: consent to AI explanations and chat (PDPA s129)


@dataclass(frozen=True)
class ConsentText:
    name: str
    version: str
    text: str


# What each layer reads, shown to the owner when they switch it on or off (PDPA Notice & Choice).
# A new wording gets a new version; a decision records the version the owner saw.
CONSENT_TEXTS: dict[AppLayer, ConsentText] = {
    AppLayer.L1: ConsentText(
        "Night-flow leak hunter",
        "v1",
        "Reads your water meter's flow, the water pressure at your inlet and your roof tank level, "
        "to find water running when nobody is using it.",
    ),
    AppLayer.L2: ConsentText(
        "Electrical fire prevention",
        "v1",
        "Reads your distribution board monitor: current on each circuit, earth leakage at each "
        "safety switch, and breaker trips.",
    ),
    AppLayer.L3: ConsentText(
        "Appliance health",
        "v1",
        "Reads the energy used by your aircon circuits and your fridge's smart plug, with the "
        "outdoor weather for your area, to spot appliances losing efficiency.",
    ),
    AppLayer.L4: ConsentText(
        "Maintenance schedule",
        "v1",
        "Uses the install dates and service history in your home record. It reads no sensors.",
    ),
    AppLayer.L5: ConsentText(
        "Management notices",
        "v1",
        "Reads notices from your township management and compares them with your water pressure "
        "and power supply readings, so planned works are not mistaken for faults.",
    ),
    AppLayer.AI: ConsentText(
        "AI explanations and chat",
        "v1",
        "Sends facts Penjaga has worked out about your home's cases (what, where, cost; never your "
        "name, address or raw readings) to OpenRouter, which passes them to an AI model provider "
        "outside Malaysia that keeps no copy, to write explanations and answer your questions. "
        "Off: Penjaga still detects everything and shows plain explanations.",
    ),
}

AI_BASIS = "owner consent to AI explanations and chat, text v1 (PDPA s129 cross-border transfer)"
AI_FIELDS = ["case facts derived from findings", "house element names", "the owner's question"]
EXPLAIN_RETRY_S = 600.0  # a failed explanation isn't retried for the same facts before this

# Layers whose processing runs in the detection service; switching them off stops it there.
DETECTION_LAYERS = frozenset({AppLayer.L1, AppLayer.L2, AppLayer.L3, AppLayer.L5})

# Who may receive each kind of live event.
EVENT_AUDIENCE: dict[str, frozenset[Role]] = {
    "episode": frozenset(Role),
    "consent": frozenset({Role.homeowner, Role.operator}),
    "finding": frozenset({Role.operator}),
    "case": frozenset({Role.homeowner, Role.operator}),
    "ticket": frozenset(Role),
    "notice": frozenset(Role),
    "maintenance": frozenset({Role.homeowner, Role.operator}),
}

# Who did something on a case, as the app's timeline names them.
TIMELINE_ACTOR: dict[Role, str] = {
    Role.homeowner: "owner",
    Role.management: "management",
    Role.operator: "penjaga",
}

TOURED_KEY = "toured_at"
CASE_FINGERPRINTS_KEY = "case_fingerprints"
REMINDED_KEY = "maintenance_reminded"
NOTICE_DAYS = 3  # notices stay in the app this long after supply is back
USUAL_USE_DAYS = 7  # a notice's tank outlook uses the household's water use over these days
SERIES_CACHE_SIZE = 256
TYPICAL_DAYS = 7  # "typical" today = the same hours on up to this many earlier days
# A cost of waiting compares against the household's usual month, measured over up to this
# many days before the problem began, and over at least MIN_BASELINE_S.
BASELINE_DAYS = 30
MIN_BASELINE_S = 86400.0
WATER_STREAM = "water.main.flow_lpm"
POWER_STREAM = "elec.main.power_w"
LIVE_WINDOW_S = 60.0  # the live line shows the latest reading in this window
# The other sensors, for the app's sensor strip: (stream, how far back a reading still counts, s).
# Windows follow each stream's sample period (the weather feed is hourly).
LIVE_SENSORS = {
    "pressure_bar": ("water.main.pressure_bar", LIVE_WINDOW_S),
    "tank_pct": ("water.tank.level_pct", 300.0),
    "fridge_w": ("elec.appliance.app_fridge_kitchen.power_w", LIVE_WINDOW_S),
    "outdoor_c": ("weather.outdoor.temp_c", 7200.0),
}
MAINTENANCE_SHOWN = 6  # the app lists the soonest reminders; GET /maintenance has them all


class NoEpisodeError(RuntimeError):
    """No episode has been followed yet (detection or the clock never answered)."""


class StaleConsentTextError(ValueError):
    """The owner decided on a consent text that is no longer the current one."""


class CaseNotFoundError(LookupError):
    """No case with that ID in the current run."""


class CaseActionRefusedError(ValueError):
    """The action isn't allowed on the case in its current state."""


class NoticeNotFoundError(LookupError):
    """No notice with that ID."""


class MaintenanceItemNotFoundError(LookupError):
    """No current maintenance reminder with that ID."""


class LayerOffError(ValueError):
    """The owner has switched off the layer this needs."""


class ApiService:
    def __init__(
        self,
        store: ApiStore,
        upstream: Upstream,
        *,
        index: HouseIndex | None = None,
        purge_every_s: float = 60.0,
        llm: LLM | None = None,
    ) -> None:
        self.store = store
        self.upstream = upstream
        self.llm = llm
        self._explain_jobs: dict[str, tuple[str, dict[str, Any], str, bool]] = {}
        self._explain_failed: dict[str, float] = {}
        self._explain_wake = asyncio.Event()
        self.index = index if index is not None else load_context().index()
        self.purge_every_s = purge_every_s
        self.names = Names(self.index)
        self._changed = asyncio.Event()
        self._series: OrderedDict[tuple[str, str, str, float], dict[str, Any]] = OrderedDict()
        self.follower = FindingFollower(store, upstream, on_change=self._findings_changed)
        self.actions = Actions(self)

    # ------------------------------------------------------------------ lifecycle

    async def serve(self, stop: asyncio.Event) -> None:
        purge = asyncio.create_task(self._purge_loop(stop))
        explainer = asyncio.create_task(self._explain_loop(stop))
        checks = asyncio.create_task(self._checks_loop(stop))
        try:
            await self.follower.run(stop)
        finally:
            for task in (purge, explainer, checks):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _checks_loop(self, stop: asyncio.Event) -> None:
        """Collect finished guided checks every few seconds."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 2.0)
            if stop.is_set():
                return
            try:
                await self.actions.poll_checks()
            except Exception:
                log.exception("guided check poll failed")

    async def _purge_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.purge_every_s)
            if stop.is_set():
                return
            try:
                await self.purge()
            except Exception:
                log.exception("retention purge failed")
            try:
                await self.publish_maintenance_due()
            except Exception:
                log.exception("maintenance reminders failed")

    async def purge(self) -> dict[str, int]:
        now = datetime.fromisoformat(str((await self.upstream.clock())["house_time"]))
        deleted = self.store.purge(now)
        if any(deleted.values()):
            log.info("retention purge: %s", deleted)
        return deleted

    # ------------------------------------------------------------------ live events

    def notify(self) -> None:
        event, self._changed = self._changed, asyncio.Event()
        event.set()

    def _findings_changed(self) -> None:
        self.publish_case_changes()
        self.notify()

    async def wait_for_events(self, after: int, timeout_s: float) -> list[OutboxEvent]:
        changed = self._changed  # taken before reading, so nothing published in between is missed
        events = self.store.events_after(after)
        if events:
            return events
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(changed.wait(), timeout_s)
        return self.store.events_after(after)

    # ------------------------------------------------------------------ house and findings

    def house_document(self) -> dict[str, Any]:
        return load_house_document()

    def validate_ids(self, ids: Iterable[str]) -> tuple[str, ...]:
        return validate_ids(self.index, ids)

    def findings(self, layer: str | None = None) -> list[dict[str, Any]]:
        episode = self.follower.episode
        if episode is None:
            raise NoEpisodeError("no episode followed yet")
        return self.store.latest_findings(episode[0], layer=layer)

    async def house_time(self) -> str:
        return str((await self.upstream.clock())["house_time"])

    # ------------------------------------------------------------------ cases

    def _run(self) -> tuple[str, str]:
        episode = self.follower.episode
        if episode is None:
            raise NoEpisodeError("no episode followed yet")
        return episode

    def case_states(self, now: str | None = None) -> list[CaseState]:
        run, _ = self._run()
        return build_cases(
            run, self.store.all_revisions(run), self.store.case_actions(run), self.index, now
        )

    @staticmethod
    def _visible(states: list[CaseState], consent: dict[str, bool]) -> list[CaseState]:
        """Cases of layers the owner has switched off stay stored but aren't shown."""
        return [s for s in states if consent.get(s.layer, True)]

    async def _consent_map(self) -> dict[str, bool]:
        return {c["layer"]: bool(c["enabled"]) for c in await self.consent()}

    async def cases(self, *, consent: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        """Every visible case of the current run in the app's ``Case`` shape, charts included."""
        consent = await self._consent_map() if consent is None else consent
        now = await self.house_time()
        states = self._visible(self.case_states(now), consent)
        costs = await self._costs(states, now)
        notices = self.notice_docs()
        rendered = [
            render(
                s, self.index, self.names, now, costs, extras=self._extras(s, notices, consent, now)
            )
            for s in states
        ]
        await asyncio.gather(*(self._fill_series(r, now) for r in rendered))
        for state, r in zip(states, rendered, strict=True):
            r.case = self._explained(state, r.case, consent)
        return [r.case for r in rendered]

    async def case(self, case_id: str) -> dict[str, Any]:
        consent = await self._consent_map()
        now = await self.house_time()
        state = self._find(case_id, consent, now)
        rendered = render(
            state,
            self.index,
            self.names,
            now,
            await self._costs([state], now),
            extras=self._extras(state, self.notice_docs(), consent, now),
        )
        await self._fill_series(rendered, now)
        return self._explained(state, rendered.case, consent)

    def _extras(
        self,
        state: CaseState,
        notices: list[dict[str, Any]],
        consent: dict[str, bool],
        now: str,
    ) -> CaseExtras:
        """The case's booking and checks, and (with L5 on) the notice that explains it."""
        extras = self.actions.extras(state.id)
        if not consent.get(AppLayer.L5.value, True):
            return extras
        lead = state.lead
        began = str(lead["first_seen"])
        if lead["type"] in SUPPLY_FINDINGS:
            found = next((n for n in reversed(notices) if explains(n, lead["type"], began)), None)
            if found is None:
                return replace(extras, no_notice=True)
            late = state.status in ("open", "monitoring") and (
                datetime.fromisoformat(now) > back_by(found)
            )
            return replace(extras, notice=NoticeMatch(found, management_name(), late))
        if lead["type"] in GUIDED_TYPES:
            found = next((n for n in reversed(notices) if during_recovery(n, began)), None)
            if found is not None:
                return replace(extras, recovery=found)
        return extras

    async def _costs(self, states: list[CaseState], now: str) -> dict[str, CostEstimate | str]:
        """Cost of waiting for each case's lead finding that has a billable quantity, or the
        reason there is no figure."""
        on = datetime.fromisoformat(now).date()
        out: dict[str, CostEstimate | str] = {}
        for state in states:
            lead = state.lead
            quantities = lead["quantities"]
            if not {"water_l_per_day", "extra_energy_kwh_per_day"} & set(quantities):
                continue
            baseline = await self.baseline(str(lead["first_seen"]), now)
            try:
                found = estimate(quantities, baseline, on)
            except NoTariffError as exc:
                log.warning("no tariff for %s: %s", lead["id"], exc)
                out[lead["id"]] = (
                    f"There's no official tariff on record for {on:%B %Y}, so no ringgit figure."
                )
                continue
            if found is not None:
                out[lead["id"]] = found
        return out

    async def baseline(self, before: str, now: str) -> Baseline:
        """The household's usual 30-day use, from its readings in the ``BASELINE_DAYS`` before
        ``before`` (within this run). Unknown (None) with less than ``MIN_BASELINE_S`` of history
        or when the stream service can't answer."""
        _, episode_start = self._run()
        end = datetime.fromisoformat(before)
        start = max(end - timedelta(days=BASELINE_DAYS), datetime.fromisoformat(episode_start))
        span = (end - start).total_seconds()
        if span < MIN_BASELINE_S:
            return Baseline(None, None, max(0.0, span) / 86400)
        water = await self._mean(WATER_STREAM, start, end, now)
        power = await self._mean(POWER_STREAM, start, end, now)
        minutes = 1440 * DAYS_PER_MONTH
        return Baseline(
            water_m3=None if water is None else water * minutes / 1000,  # L/min → m³ a month
            elec_kwh=None if power is None else power * minutes / 60 / 1000,  # W → kWh a month
            days=span / 86400,
        )

    async def _mean(self, stream: str, start: datetime, end: datetime, now: str) -> float | None:
        """Mean of a stream over a window, weighted by sample count; None without data."""
        span = (end - start).total_seconds()
        try:
            doc = await self._summary(stream, start.isoformat(), end.isoformat(), span, now)
        except UpstreamUnavailableError as exc:
            log.warning("baseline unavailable for %s: %s", stream, exc)
            return None
        points = [p for p in doc.get("points", []) if p.get("mean") is not None and p.get("n")]
        samples = sum(int(p["n"]) for p in points)
        if not samples:
            return None
        return sum(float(p["mean"]) * int(p["n"]) for p in points) / samples

    def _find(self, case_id: str, consent: dict[str, bool], now: str | None = None) -> CaseState:
        for state in self._visible(self.case_states(now), consent):
            if state.id == case_id:
                return state
        raise CaseNotFoundError(f"no case {case_id!r}")

    async def _fill_series(self, rendered: Rendered, now: str) -> None:
        plan = rendered.series
        if plan is None:
            return
        try:
            summary = await self._summary(plan.stream, plan.start, plan.end, plan.bucket_s, now)
        except UpstreamUnavailableError as exc:
            log.warning("evidence series unavailable for %s: %s", rendered.case["id"], exc)
            return
        rendered.case["evidence"]["series"] = series_points(plan, summary)

    async def _summary(
        self, stream: str, start: str, end: str, bucket_s: float, now: str
    ) -> dict[str, Any]:
        """A stream summary; a window that ended before ``now`` never changes, so it's cached."""
        cached = self._series.get(key := (stream, start, end, bucket_s))
        if cached is not None:
            self._series.move_to_end(key)
            return cached
        summary = await self.upstream.readings_summary(stream, start, end, bucket_s)
        if datetime.fromisoformat(end) < datetime.fromisoformat(now):
            self._series[key] = summary
            while len(self._series) > SERIES_CACHE_SIZE:
                self._series.popitem(last=False)
        return summary

    async def dismiss(self, case_id: str, reason: str, actor: Role) -> dict[str, Any]:
        state = self._find(case_id, await self._consent_map())
        if not dismissable(state):
            if state.status in ("open", "monitoring"):
                raise CaseActionRefusedError(
                    "an urgent or safety-floor case can't be dismissed; it closes when the "
                    "condition clears"
                )
            raise CaseActionRefusedError(f"case is {state.status}")
        run, _ = self._run()
        house_time = await self.house_time()
        self.store.add_case_action(
            run,
            case_id,
            kind=DISMISS,
            actor=TIMELINE_ACTOR[actor],
            house_time=house_time,
            detail={"reason": reason},
        )
        self.publish_case_changes()
        self.notify()
        return await self.case(case_id)

    def publish_case_changes(self) -> int:
        """Publish a ``case`` event for every case whose state changed; how many changed."""
        try:
            run, _ = self._run()
        except NoEpisodeError:
            return 0
        key = f"{CASE_FINGERPRINTS_KEY}:{run}"
        known: dict[str, list[Any]] = json.loads(self.store.get_value(key) or "{}")
        changed = 0
        for state in self.case_states():
            lead = state.lead
            fingerprint = [state.status, state.severity, len(state.timeline), lead["id"]]
            if known.get(state.id) == fingerprint:
                continue
            known[state.id] = fingerprint
            changed += 1
            house_time = state.timeline[-1].t if state.timeline else str(lead["updated_at"])
            self.store.publish(
                "case",
                house_time,
                {
                    "id": state.id,
                    "layer": state.layer,
                    "status": state.status,
                    "severity": state.severity,
                },
            )
        if changed:
            self.store.set_value(key, json.dumps(known, sort_keys=True))
        return changed

    # ------------------------------------------------------------------ the agent

    def ai_allowed(self, consent: dict[str, bool]) -> bool:
        return self.llm is not None and consent.get(AppLayer.AI.value, True)

    def _explained(
        self, state: CaseState, case: dict[str, Any], consent: dict[str, bool]
    ) -> dict[str, Any]:
        """The case with its stored explanation, if one was written from exactly these facts;
        otherwise the template, and the explanation is queued (never awaited by a request)."""
        if not self.ai_allowed(consent) or state.status in ("resolved", "dismissed"):
            return case
        facts = explanation_facts(case)
        fp = fingerprint(facts)
        stored = self.store.get_explanation(state.id, fp)
        if stored is not None:
            return merge(case, stored)
        failed = self._explain_failed.get(fp)
        if failed is None or time.monotonic() - failed > EXPLAIN_RETRY_S:
            self._explain_jobs[state.id] = (fp, facts, state.severity, state.floor)
            self._explain_wake.set()
        return case

    async def _explain_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.explain_pending()
            except Exception:
                log.exception("explanation worker failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._explain_wake.wait(), 5.0)
            self._explain_wake.clear()

    async def explain_pending(self) -> int:
        """Write the queued explanations, one at a time; how many were stored."""
        stored = 0
        while self._explain_jobs and self.llm is not None:
            case_id, (fp, facts, severity, floor) = self._explain_jobs.popitem()
            if self.store.get_explanation(case_id, fp) is not None:
                continue
            try:
                house_time = await self.house_time()
            except UpstreamUnavailableError:
                self._explain_jobs[case_id] = (fp, facts, severity, floor)
                return stored
            self._log_llm(house_time, "explain a case")
            try:
                result = await explain(
                    self.llm, facts, index=self.index, severity=severity, floor=floor
                )
            except (LLMUnavailableError, ExplanationRejectedError) as exc:
                self._explain_failed[fp] = time.monotonic()
                log.warning("explanation for %s not used: %s", case_id, exc)
                continue
            doc = {**result.explanation.model_dump(), "model": result.model}
            self.store.put_explanation(case_id, fp, house_time, doc)
            self.store.publish("case", house_time, {"id": case_id, "explained": True})
            self.notify()
            stored += 1
        return stored

    def _log_llm(self, house_time: str, purpose: str) -> None:
        models = ", ".join(self.llm.models) if self.llm is not None else ""
        self.store.log_disclosure(
            house_time=house_time,
            actor="penjaga",
            recipient=f"OpenRouter, for an AI model provider ({models})",
            purpose=purpose,
            fields=AI_FIELDS,
            basis=AI_BASIS,
        )

    async def chat(
        self, message: str, *, conversation_id: str | None = None, case_id: str | None = None
    ) -> dict[str, Any]:
        """Answer one "Ask Penjaga" message; a polite unavailable reply when AI is off."""
        conversation_id = conversation_id or f"conv_{uuid.uuid4().hex[:16]}"
        now = await self.house_time()
        consent = await self._consent_map()
        states = self._visible(self.case_states(), consent)
        costs = await self._costs(states, now)
        notices = self.notice_docs()
        cases = [
            render(
                s, self.index, self.names, now, costs, extras=self._extras(s, notices, consent, now)
            ).case
            for s in states
        ]
        by_case = {s.id: costs[s.lead["id"]] for s in states if s.lead["id"] in costs}
        history = [
            {"role": "assistant" if m["role"] == "penjaga" else "user", "content": m["text"]}
            for m in self.store.chat_history(conversation_id, limit=12)
        ]
        reply = Answer(UNAVAILABLE, [], "unavailable")
        if self.llm is not None and self.ai_allowed(consent):
            self._log_llm(now, "answer an Ask Penjaga question")
            try:
                reply = await answer(
                    self.llm,
                    ToolBox(self, now, cases, by_case),
                    message=message,
                    history=history,
                    context=context_message(cases, now, case_id),
                    session_id=conversation_id,
                )
            except LLMUnavailableError as exc:
                log.warning("chat unavailable: %s", exc)
        if reply.source != "ai":
            live = [c for c in cases if c["status"] not in ("resolved", "dismissed")]
            reply.chips = [
                {"label": f"Open: {c['title']}", "to": f"/cases/{c['id']}"} for c in live[:3]
            ]
        self.store.add_chat_message(
            conversation_id, role="owner", text=message, chips=[], source="owner", model=None,
            house_time=now,
        )  # fmt: skip
        seq = self.store.add_chat_message(
            conversation_id,
            role="penjaga",
            text=reply.text,
            chips=reply.chips,
            source=reply.source,
            model=reply.model,
            house_time=now,
        )
        return {
            "conversation_id": conversation_id,
            "source": reply.source,
            "model": reply.model,
            "message": {
                "id": f"m{seq}",
                "role": "penjaga",
                "text": reply.text,
                "chips": reply.chips,
                "t": epoch_ms(now),
            },
        }

    # ------------------------------------------------------------------ the app's view

    async def view(self) -> dict[str, Any]:
        """Everything the homeowner app renders, in the stand-in's ``View`` shape."""
        consent = await self._consent_map()
        cases = await self.cases(consent=consent)
        now = await self.house_time()
        passport = [
            {
                "t": c["resolved_at"],
                "title": c["title"],
                "detail": c.get("outcome", ""),
                "case_id": c["id"],
            }
            for c in cases
            if c["status"] == "resolved" and "resolved_at" in c
        ]
        maintenance_on = consent.get(AppLayer.L4.value, True)
        if maintenance_on:
            passport += self._maintenance_passport()
        passport.append(
            {
                "t": epoch_ms(f"{maintenance.handover(self.index).isoformat()}T09:00:00+08:00"),
                "title": "Home handed over",
                "detail": "Aircons, water heaters, safety switches and the water purifier "
                "registered from the handover record",
                "case_id": "",
            }
        )
        notices = await self.notices(cases, now) if consent.get(AppLayer.L5.value, True) else []
        return {
            "cases": cases,
            "notices": notices,
            "wellbeing": None,
            "home": await self.home(cases, now),
            "maintenance": self._maintenance_shown(now) if maintenance_on else [],
            "passport": sorted(passport, key=lambda p: -int(p["t"])),
            "consent": consent,
            "toured": self.store.get_value(TOURED_KEY) is not None,
        }

    async def live(self) -> dict[str, Any]:
        """The house right now, for the app's "listening" line: the latest water flow (L/min)
        and total power (kW) from the last ``LIVE_WINDOW_S`` of readings, and the other sensors
        (``LIVE_SENSORS``) for its sensor strip. The app never holds a stream token; values are
        None when no reading arrived in a stream's window (or the house has no such sensor)."""
        now = await self.house_time()
        end = datetime.fromisoformat(now)

        async def latest(
            stream: str, window_s: float = LIVE_WINDOW_S, *, optional: bool = False
        ) -> float | None:
            start = (end - timedelta(seconds=window_s)).isoformat()
            try:
                doc = await self.upstream.readings_summary(stream, start, now, window_s)
            except UpstreamUnavailableError as exc:  # a sensor this house doesn't have
                if not optional:
                    raise
                log.debug("live %s: %s", stream, exc)
                return None
            points = [p for p in doc.get("points", []) if p.get("last") is not None]
            return float(points[-1]["last"]) if points else None

        names = list(LIVE_SENSORS)
        water, power, *others = await asyncio.gather(
            latest(WATER_STREAM),
            latest(POWER_STREAM),
            *(latest(*LIVE_SENSORS[n], optional=True) for n in names),
        )
        sensors = {
            n: None if v is None else round(v, 2) for n, v in zip(names, others, strict=True)
        }
        return {
            "at": epoch_ms(now),
            "water_lpm": None if water is None else round(water, 3),
            "power_kw": None if power is None else round(power / 1000, 3),
            **sensors,
        }

    async def mark_toured(self) -> None:
        if self.store.get_value(TOURED_KEY) is None:
            self.store.set_value(TOURED_KEY, await self.house_time())

    async def home(self, cases: list[dict[str, Any]], now: str) -> dict[str, Any]:
        live = [c for c in cases if c["status"] in ("open", "action_pending")]
        if any(c["severity"] == "urgent" for c in live):
            status = "urgent"
        elif live:
            status = "attention"
        else:
            status = "all_quiet"
        today: dict[str, Any] = {
            "water_l": 0,
            "water_typical_l": None,
            "water_series": [],
            "elec_kwh": 0.0,
            "elec_typical_kwh": None,
            "elec_series": [],
        }
        try:
            water = await self._usage_today("water.main.flow_lpm", now, per_hour=60.0)
            elec = await self._usage_today("elec.main.power_w", now, per_hour=1 / 1000)
        except UpstreamUnavailableError as exc:
            log.warning("today's usage unavailable: %s", exc)
        else:
            today.update(
                water_l=round(water[0]),
                water_typical_l=None if water[1] is None else round(water[1]),
                water_series=[round(v, 1) for v in water[2]],
                elec_kwh=round(elec[0], 1),
                elec_typical_kwh=None if elec[1] is None else round(elec[1], 1),
                elec_series=[round(v, 3) for v in elec[2]],
            )
        return {"status": status, "checked_at": epoch_ms(now), "today": today}

    async def _usage_today(
        self, stream: str, now: str, *, per_hour: float
    ) -> tuple[float, float | None, list[float]]:
        """(so far today, typical by this time of day, hourly amounts) for a rate stream.

        An hour's amount is its mean rate x ``per_hour`` (L/min x 60 = litres; W x 1/1000 =
        kWh), pro rata for the current hour. "Typical" is the average of the same hours on up to
        ``TYPICAL_DAYS`` earlier days of this run that have data for every one of them: the
        house's own history, never an assumed profile (None until there is such a day).
        """
        t_now = datetime.fromisoformat(now)
        midnight = t_now.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed_h = (t_now - midnight).total_seconds() / 3600
        hours = max(1, math.ceil(elapsed_h))
        part = elapsed_h - (hours - 1)  # share of the current hour gone by

        series: list[float] = []
        if t_now > midnight:
            doc = await self.upstream.readings_summary(stream, midnight.isoformat(), now, 3600.0)
            got = _hourly(doc, per_hour).get(midnight, {})
            series = [got.get(h, 0.0) for h in range(hours)]
            series[-1] *= part

        _, episode_start = self._run()
        earliest = max(
            midnight - timedelta(days=TYPICAL_DAYS), datetime.fromisoformat(episode_start)
        )
        typical: float | None = None
        if earliest < midnight:
            doc = await self.upstream.readings_summary(
                stream, earliest.isoformat(), midnight.isoformat(), 3600.0
            )
            sums = [
                sum(day[h] for h in range(hours - 1)) + day[hours - 1] * part
                for day in _hourly(doc, per_hour).values()
                if all(h in day for h in range(hours))
            ]
            if sums:
                typical = sum(sums) / len(sums)
        return sum(series), typical, series

    # ------------------------------------------------------------------ notices (L5)

    def notice_docs(self) -> list[dict[str, Any]]:
        """Every stored notice, oldest first (withdrawn ones included)."""
        return [_doc(r) for r in self.store.records(NOTICE_KIND)]

    async def publish_notice(
        self, draft: NoticeDraft, *, actor: Role, source_note: str | None = None
    ) -> dict[str, Any]:
        """Publish a notice staff confirmed; it reaches this house if its areas name it."""
        house_time = await self.house_time()
        notice_id = f"ntc_{uuid.uuid4().hex[:12]}"
        doc = {
            **draft.model_dump(mode="json"),
            "id": notice_id,
            "status": "published",
            "posted_at": house_time,
            "posted_by": actor.value,
            "reaches_house": reaches_house(draft.areas, draft.district),
        }
        if source_note:
            doc["provenance"] = source_note  # kept for the record; never shown
        self.store.put_record(
            NOTICE_KIND, notice_id, run="", case_id="", status="published",
            house_time=house_time, doc=doc,
        )  # fmt: skip
        self.store.publish("notice", house_time, {"id": notice_id, "status": "published"})
        self.publish_case_changes()
        self.notify()
        return doc

    async def read_notice(self, text: str) -> dict[str, Any]:
        """Fields the AI reads from a pasted notice, for staff to check (nothing is published).
        The text is the management office's own notice: no homeowner data goes to the model."""
        if self.llm is None:
            raise LLMUnavailableError("disabled", "the AI notice reader isn't configured")
        now = await self.house_time()
        parsed = await read_notice(self.llm, text, now=now)
        return {
            "draft": parsed.draft.model_dump(mode="json"),
            "issues": draft_issues(parsed.draft, now),
            "model": parsed.model,
        }

    async def seed_notice(self, preset: str, starts_in_h: float | None = None) -> dict[str, Any]:
        """A notice adapted from a past public one, placed relative to house time (the Lab)."""
        draft, note = from_preset(preset, await self.house_time(), starts_in_h)
        return await self.publish_notice(draft, actor=Role.operator, source_note=note)

    async def withdraw_notice(self, notice_id: str) -> dict[str, Any]:
        record = self.store.record(notice_id)
        if record is None or record["kind"] != NOTICE_KIND:
            raise NoticeNotFoundError(notice_id)
        house_time = await self.house_time()
        doc = {**_doc(record), "status": "withdrawn", "withdrawn_at": house_time}
        self.store.put_record(
            NOTICE_KIND, notice_id, run="", case_id="", status="withdrawn",
            house_time=house_time, doc=doc,
        )  # fmt: skip
        self.store.publish("notice", house_time, {"id": notice_id, "status": "withdrawn"})
        self.publish_case_changes()
        self.notify()
        return doc

    async def notices(self, cases: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
        """Published notices that reach this house, current or recent, newest first, each
        with what it means for this house."""
        t_now = datetime.fromisoformat(now)
        shown = [
            n
            for n in self.notice_docs()
            if n["status"] == "published"
            and n.get("reaches_house")
            and back_by(n) > t_now - timedelta(days=NOTICE_DAYS)
        ]
        usual = await self._usual_water_l_day(now) if shown else None
        issuer = management_name()
        out = [to_view(n, issuer, self._impact(n, cases, usual)) for n in shown]
        return sorted(out, key=lambda n: -int(n["starts_at"]))

    async def _usual_water_l_day(self, now: str) -> float | None:
        """The household's water use a day over the last ``USUAL_USE_DAYS`` (None with less
        than a day of history)."""
        _, episode_start = self._run()
        end = datetime.fromisoformat(now)
        start = max(end - timedelta(days=USUAL_USE_DAYS), datetime.fromisoformat(episode_start))
        if (end - start).total_seconds() < MIN_BASELINE_S:
            return None
        mean = await self._mean(WATER_STREAM, start, end, now)
        return None if mean is None else mean * 1440  # L/min → litres a day

    def _impact(
        self, notice: dict[str, Any], cases: list[dict[str, Any]], usual: float | None
    ) -> str | None:
        """What the notice means for this house, from its record (and a matched case)."""
        start = clock(notice["starts_at"])
        hours = (back_by(notice) - datetime.fromisoformat(notice["starts_at"])).total_seconds()
        hours /= 3600
        mark = f"Matched to the notice: {notice['title']}"
        matched = any(
            c["status"] in ("open", "monitoring", "action_pending")
            and any(e["text"] == mark for e in c["timeline"])
            for c in cases
        )
        if notice["utility"] == "water":
            tank = self.index.house.water.tanks[0]
            main = self.names.listing(self.index.main_fed_fixtures, limit=3)
            text = (
                f"Taps fed straight from the mains ({main}) stop when the works start. Your roof "
                f"tank holds up to {smart(tank.capacity_l)} L for everything else"
            )
            if usual:
                tank_h = tank.capacity_l / usual * 24
                text += f", about {smart(tank_h)} hours at your usual use"
                if tank_h < hours:
                    text += (
                        f". Supply may be off for up to {smart(hours)} hours, longer than a full "
                        f"tank lasts, so store extra water before {start}"
                    )
            text += "."
            if matched:
                text = (
                    "Water pressure at your inlet dropped. This matches the scheduled works, so "
                    f"it isn't a fault in your home. {text}"
                )
            return text
        if notice["utility"] == "electricity":
            kinds = {str(a.kind): a.id for a in self.index.house.appliances}
            named = [kinds[k] for k in ("refrigerator", "router") if k in kinds]
            text = (
                f"Everything on your distribution board goes off for up to {smart(hours)} hours"
                + (f", including the {self.names.listing(named)}" if named else "")
                + f". Unplug the television and computers before {start}, and plug them back in "
                "once power is steady."
            )
            if matched:
                text = (
                    "Your power supply cut out. This matches the planned outage, so it isn't a "
                    f"fault in your home. {text}"
                )
            return text
        return None

    # ------------------------------------------------------------------ maintenance (L4)

    def _done(self) -> dict[tuple[str, str], str]:
        return maintenance.last_done(self.store.records(maintenance.DONE_KIND))

    def maintenance(self, now: str) -> list[dict[str, Any]]:
        """Every reminder for the house, soonest first, in the app's ``MaintenanceItem``."""
        return [
            maintenance.to_view(item, self.names, now)
            for item in maintenance.schedule(self.index, now, self._done())
        ]

    async def maintenance_items(self) -> list[dict[str, Any]]:
        if not (await self._consent_map()).get(AppLayer.L4.value, True):
            return []
        return self.maintenance(await self.house_time())

    def _maintenance_shown(self, now: str) -> list[dict[str, Any]]:
        """The app's list: the next few coming up first (the app says "Next on my list" for the
        first), then anything overdue, then the rest, up to ``MAINTENANCE_SHOWN``."""
        items = self.maintenance(now)
        upcoming = [m for m in items if not m["overdue"]]
        overdue = [m for m in items if m["overdue"]]
        return (upcoming[:3] + overdue + upcoming[3:])[:MAINTENANCE_SHOWN]

    def _maintenance_passport(self) -> list[dict[str, Any]]:
        return [
            {
                "t": epoch_ms(r["done_at"]),
                "title": r["title"],
                "detail": f"Done \u00b7 {r['interval']} \u00b7 {r['source']}",
                "case_id": "",
            }
            for r in self.store.records(maintenance.DONE_KIND)
        ]

    async def mark_maintenance_done(self, item_id: str) -> dict[str, Any]:
        """The owner did a maintenance task: record it for every element the reminder covers."""
        if not (await self._consent_map()).get(AppLayer.L4.value, True):
            raise LayerOffError("the maintenance schedule is switched off")
        now = await self.house_time()
        items = maintenance.schedule(self.index, now, self._done())
        item = next((i for i in items if i.id == item_id), None)
        if item is None:
            raise MaintenanceItemNotFoundError(item_id)
        record_id = f"mdone_{uuid.uuid4().hex[:12]}"
        doc = {
            "id": record_id,
            "item_id": item_id,
            "rule": item.rule.id,
            "object_ids": list(item.object_ids),
            "title": maintenance.title(item, self.names),
            "interval": item.rule.interval,
            "source": item.rule.source,
            "due": item.due.isoformat(),
            "done_at": now,
        }
        self.store.put_record(
            maintenance.DONE_KIND, record_id, run="", case_id="", status="done",
            house_time=now, doc=doc,
        )  # fmt: skip
        self.store.publish("maintenance", now, {"id": item_id, "done": True})
        self.notify()
        return doc

    async def publish_maintenance_due(self) -> int:
        """A ``maintenance`` event for each reminder that has come due since the last check
        (once per reminder); how many were published. Nothing while L4 is off."""
        if not (await self._consent_map()).get(AppLayer.L4.value, True):
            return 0
        now = await self.house_time()
        today = datetime.fromisoformat(now).date()
        reminded: list[str] = json.loads(self.store.get_value(REMINDED_KEY) or "[]")
        due = [
            i
            for i in maintenance.schedule(self.index, now, self._done())
            if i.due <= today and i.id not in reminded
        ]
        for item in due:
            title = maintenance.title(item, self.names)
            self.store.publish(
                "maintenance", now, {"id": item.id, "title": title, "due": item.due.isoformat()}
            )
            reminded.append(item.id)
        if due:
            self.store.set_value(REMINDED_KEY, json.dumps(reminded[-500:]))
            self.notify()
        return len(due)

    # ------------------------------------------------------------------ consent

    async def consent(self) -> list[dict[str, Any]]:
        decided = self.store.consent_state()
        running = {d["layer"]: bool(d["enabled"]) for d in await self.upstream.layers()}
        out = []
        for layer, text in CONSENT_TEXTS.items():
            decision = decided.get(layer.value)
            if decision is not None:
                enabled = bool(decision["enabled"])
            else:
                enabled = running.get(layer.value, True)
            out.append(
                {
                    "layer": layer.value,
                    "name": text.name,
                    "text": text.text,
                    "text_version": text.version,
                    "enabled": enabled,
                    "decided_at": None if decision is None else decision["decided_at"],
                    "decided_on_version": None if decision is None else decision["text_version"],
                }
            )
        return out

    async def set_consent(
        self, layer: AppLayer, enabled: bool, text_version: str, actor: Role
    ) -> dict[str, Any]:
        """Record the owner's decision; for detection layers, start or stop processing first."""
        current = CONSENT_TEXTS[layer]
        if text_version != current.version:
            raise StaleConsentTextError(
                f"{layer.value}: decided on text {text_version!r}, current is {current.version!r}"
            )
        if layer in DETECTION_LAYERS:
            await self.upstream.set_layer(layer.value, enabled)
        house_time = await self.house_time()
        self.store.record_consent(layer.value, enabled, text_version, actor.value, house_time)
        self.store.publish("consent", house_time, {"layer": layer.value, "enabled": enabled})
        self.notify()
        return {
            "layer": layer.value,
            "enabled": enabled,
            "text_version": text_version,
            "decided_at": house_time,
        }

    # ------------------------------------------------------------------ health

    def health(self) -> dict[str, Any]:
        f = self.follower
        episode = f.episode
        return {
            "status": "ok" if f.connected else "degraded",
            "detection_connected": f.connected,
            "episode_id": None if episode is None else episode[0],
            "detect_cursor": f.cursor,
            "dropped_revisions": f.dropped,
            "store_conflicts": self.store.conflicts,
            "last_error": f.last_error,
        }


RECORD_COLUMNS = frozenset({"kind", "run", "case_id", "created", "updated"})


def _doc(record: dict[str, Any]) -> dict[str, Any]:
    """A stored record's own document, without the store's columns."""
    return {k: v for k, v in record.items() if k not in RECORD_COLUMNS}


def _hourly(summary: dict[str, Any], per_hour: float) -> dict[datetime, dict[int, float]]:
    """{local midnight: {hour of day: amount}} from an hourly stream summary; empty hours are
    left out."""
    days: dict[datetime, dict[int, float]] = {}
    for point in summary.get("points", []):
        if point.get("mean") is None:
            continue
        t = datetime.fromisoformat(point["t"])
        midnight = t.replace(hour=0, minute=0, second=0, microsecond=0)
        days.setdefault(midnight, {})[t.hour] = float(point["mean"]) * per_hour
    return days
