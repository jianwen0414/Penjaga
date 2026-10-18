"""Templated case text: the app's ``Case`` shape (``src/api/types.ts``) from a ``CaseState``.

This is the LLM-down path the PRD requires (§18.4: "a clear, templated case"), and the facts the
step-6 agent rewrites. Rules:
- every number comes from the finding (``evidence``, ``quantities``) or the house record;
- every name comes from the house record (``Names``), never a raw ID;
- detected / suspected / possible follows ``confidence_kind`` and confidence (§18.4);
- urgent cases show the reviewed safety text for their ``safety_guidance_key`` (``safety``),
  added by ``render`` for every urgent case whatever its type;
- trades are an "ST-registered electrical contractor" and a "SPAN-registered plumber"; electrical
  DIY stops at switching off, unplugging and the RCD test button.
Money comes from the deterministic cost calculator (``costs``): the bill with the problem
minus the usual bill, on the official tariffs. Without a baseline there is no RM figure.

Charts: a case's evidence series is described here as a ``SeriesPlan`` (stream, window, bucket)
and fetched by the service from ``/stream/v1/readings/summary``. Findings with ``series_refs`` use
them; for the rest the plan names the stream the detector itself reads.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from penjaga_api.cases import BOOKING_ACK_AFTER, CaseState, dismissable, epoch_ms, house_dt
from penjaga_api.costs import DAYS_PER_MONTH, CostEstimate
from penjaga_api.ids import validate_ids
from penjaga_api.labels import finding_label
from penjaga_api.names import Names
from penjaga_api.notices import SUPPLY_FINDINGS, NoticeMatch, back_by
from penjaga_api.safety import safety_lines
from penjaga_api.vendors import job_price
from penjaga_contract.house import ElementKind, HouseIndex

ST_CONTRACTOR = "ST-registered electrical contractor"
SPAN_PLUMBER = "SPAN-registered plumber"

# Klang Valley average domestic use per person (SPAN, via Air Selangor); step 4 moves this
# sourced figure into the versioned knowledge base.
LITRES_PER_PERSON_DAY = 241.0
OWNER_REPAIRS = (
    "Repairs inside your property boundary, including pipes in walls and ceilings, "
    "are the owner's responsibility (Air Selangor)."
)
MAX_POINTS = 480  # stay under the stream service's 500-bucket limit
NICE_BUCKETS_S = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
NO_HISTORY = "A ringgit figure needs at least a day of your home's usage before this began."
NONE = "\u2013"  # en dash: a stat with no value


@dataclass(frozen=True)
class SeriesPlan:
    stream: str
    start: str
    end: str
    bucket_s: float
    unit: str
    scale: float = 1.0  # plotted value = bucket mean x scale
    lines: tuple[dict[str, Any], ...] = ()
    bands: tuple[tuple[str, str, str], ...] = ()  # (from, to, label), house time
    markers: tuple[tuple[str, str], ...] = ()  # (t, label), house time


@dataclass(frozen=True)
class CaseExtras:
    """What happened on a case beyond its findings: the latest booking, completed guided
    checks (``fixture_id``, ``result`` stopped|continued, ``flow_before``, ``flow_after``), and
    management notices: the one that explains a supply drop (``notice``), whether notices were
    checked and none did (``no_notice``), and works a leak began during (``recovery``)."""

    booking: dict[str, Any] | None = None
    checks: tuple[dict[str, Any], ...] = ()
    notice: NoticeMatch | None = None
    no_notice: bool = False
    recovery: dict[str, Any] | None = None


GUIDED_TYPES = frozenset(
    {"night_flow_unexplained", "running_cistern_suspected", "dripping_fixture_suspected"}
)
GUIDED_INTRO = (
    "Close one valve at a time while Penjaga watches your water meter: Air Selangor's leak "
    "test, done for you from your meter. When the flow stops, we've found it."
)


def guided_targets(case: CaseState, index: HouseIndex) -> list[tuple[str, str]]:
    """(fixture, the valve that isolates it most narrowly) to try, in order: the case's
    candidates if detection named any, else the tank-fed toilet cisterns, ground floor first."""
    lead = case.lead
    fixtures = [
        c["object_id"]
        for c in lead["candidates"]
        if index.exists(c["object_id"]) and index.kind_of(c["object_id"]) is ElementKind.fixture
    ]
    if not fixtures:
        fixtures = [f for f in index.tank_fed_fixtures if f.endswith("_wc_cistern")]
        fixtures.sort(key=lambda f: min(index.room_level(r) for r in index.rooms_of(f)))
    valves = index.ids(ElementKind.valve)
    out = []
    for fixture in fixtures:
        isolating = [v for v in valves if fixture in index.isolated_by(v)]
        if isolating:
            out.append((fixture, min(isolating, key=lambda v: len(index.isolated_by(v)))))
    return out


def _guided(ctx: Ctx, extras: CaseExtras) -> tuple[dict[str, Any], tuple[str, str] | None]:
    latest = {c["fixture_id"]: c for c in extras.checks}
    steps = []
    located: tuple[str, str] | None = None
    for fixture, valve in guided_targets(ctx.case, ctx.index):
        step: dict[str, Any] = {
            "fixture_id": fixture,
            "fixture_label": ctx.names.of(fixture),
            "valve_id": valve,  # the valve the owner closes (the Lab mirrors it in the simulator)
            "instruction": (
                f"Close the {ctx.names.of(valve)} (clockwise until it stops), wait a moment, "
                "then tap Check."
            ),
        }
        done = latest.get(fixture)
        if done:
            step["result"] = done["result"]
            for key in ("flow_before", "flow_after"):
                if done.get(key) is not None:
                    step[key] = done[key]
            if done["result"] == "stopped" and located is None:
                located = (fixture, valve)
        steps.append(step)
    guided: dict[str, Any] = {"intro": GUIDED_INTRO, "steps": steps, "done": located is not None}
    if located:
        guided["located"] = located[0]
    return guided, located


def _booking(extras: CaseExtras, now: str) -> dict[str, Any] | None:
    b = extras.booking
    if b is None:
        return None
    if b["status"] == "completed":
        status = "completed"
    elif house_dt(now) >= house_dt(b["created"]) + BOOKING_ACK_AFTER:
        status = "acknowledged"
    else:
        status = "requested"
    return {
        "id": b["id"],
        "vendor_id": b["vendor_id"],
        "vendor_name": b["vendor_name"],
        "slot": b["slot"],
        "status": status,
        "created_at": epoch_ms(b["created"]),
    }


@dataclass
class Rendered:
    case: dict[str, Any]
    series: SeriesPlan | None


@dataclass
class Text:
    """What a template decides; ``render`` turns it into the app's shape."""

    title: str
    location: str
    summary: str
    confidence_note: str
    headline: str
    impact_lines: list[str] = field(default_factory=list)
    how: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    evidence_title: str = ""
    stats: list[tuple[str, str]] = field(default_factory=list)
    explanation: str = ""
    series: SeriesPlan | None = None
    focus_layer: str = "water"
    focus_ids: list[str] = field(default_factory=list)
    focus_label: str = ""


@dataclass(frozen=True)
class Ctx:
    index: HouseIndex
    names: Names
    now: str  # house time the view is built at
    case: CaseState
    # cost of waiting for the lead finding, or why there is no figure (a sentence)
    cost: CostEstimate | str | None = None

    @property
    def lead(self) -> dict[str, Any]:
        return self.case.lead


# ---------------------------------------------------------------------- formatting


def num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def smart(value: float) -> str:
    """A measurement with sensible precision: 1,016 / 23.4 / 0.71 / 0.011."""
    a = abs(value)
    if a >= 100:
        return num(value)
    if a >= 10:
        return num(value, 1).removesuffix(".0")
    if a >= 1:
        return num(value, 2).rstrip("0").rstrip(".")
    if a == 0:
        return "0"
    digits = max(2, -math.floor(math.log10(a)) + 1)
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def clock(value: str) -> str:
    dt = house_dt(value)
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}, {dt:%a %d %b}"


def day(value: str) -> str:
    return f"{house_dt(value):%a %d %b}"


def ev(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in doc["evidence"]}


def evn(doc: dict[str, Any], name: str) -> float | None:
    item = ev(doc).get(name)
    value = None if item is None else item.get("value")
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def qty(doc: dict[str, Any], name: str) -> float | None:
    item = doc["quantities"].get(name)
    return None if item is None else float(item["value"])


def certainty(doc: dict[str, Any]) -> str:
    """Detected (a measured rule), suspected (a confident model) or possible (PRD §18.4)."""
    if doc["confidence_kind"] == "deterministic_rule":
        return "Detected"
    return "Suspected" if float(doc["confidence"]) >= 0.8 else "Possible"


def window(start: datetime, end: datetime, base_bucket_s: float) -> tuple[str, str, float]:
    span = max(1.0, (end - start).total_seconds())
    needed = max(base_bucket_s, span / MAX_POINTS)
    bucket = next((float(b) for b in NICE_BUCKETS_S if b >= needed), float(math.ceil(needed)))
    return start.isoformat(), end.isoformat(), bucket


def until(ctx: Ctx, doc: dict[str, Any], pad: timedelta = timedelta(0)) -> datetime:
    """End of a chart window: when the finding ended (plus ``pad``), or now."""
    now = house_dt(ctx.now)
    ended = doc.get("cleared_at")
    return min(now, house_dt(ended) + pad) if ended else now


# ---------------------------------------------------------------------- actions


def act(kind: str, label: str, **extra: Any) -> dict[str, Any]:
    action = {"id": kind, "kind": kind, "label": label}
    action.update({k: v for k, v in extra.items() if v is not None})
    return action


def plumber(primary: bool = False, detail: str | None = None) -> dict[str, Any]:
    return act(
        "book_vendor",
        f"Book a {SPAN_PLUMBER}",
        vendor_category="plumber",
        primary=primary or None,
        detail=detail,
    )


def electrician(primary: bool = False, detail: str | None = None) -> dict[str, Any]:
    return act(
        "book_vendor",
        f"Book an {ST_CONTRACTOR}",
        vendor_category="electrician",
        primary=primary or None,
        detail=detail,
    )


def management(primary: bool = False, detail: str | None = None) -> dict[str, Any]:
    return act("send_management", "Send to management", primary=primary or None, detail=detail)


def show(label: str, layer: str, ids: list[str], severity: str, focus_label: str) -> dict[str, Any]:
    return {
        "id": f"show_{ids[0]}",
        "kind": "show_in_3d",
        "label": label,
        "focus": {
            "layer": layer,
            "object_ids": ids,
            "severity": severity,
            "label": focus_label,
        },
    }


# ---------------------------------------------------------------------- L1 water


def _money(t: Text, ctx: Ctx, quantity: str, *, per_day: bool = False) -> None:
    """Add the cost of waiting to an impact: RM a month in the headline, a year's worth, any
    tariff step it crosses, and how it was worked out. ``per_day`` for something that runs for
    hours or days (a burst), not months."""
    cost = ctx.cost
    if not isinstance(cost, CostEstimate):
        t.how = (t.how + " " if t.how else "") + (cost or NO_HISTORY)
        return
    if per_day:
        daily = cost.rm_per_month / DAYS_PER_MONTH
        t.headline += f" \u00b7 about RM{daily:,.0f} for every day it runs"
        t.how = f"{quantity} {cost.how} Per day: that monthly difference divided by 30."
        return
    t.headline += f" \u00b7 about RM{cost.rm_per_month:,.2f} a month"
    t.impact_lines.insert(0, f"Over a year: about RM{cost.rm_per_month * 12:,.0f}.")
    if cost.threshold_note:
        t.impact_lines.insert(1, cost.threshold_note)
    t.how = f"{quantity} {cost.how}"


def _water_impact(t: Text, ctx: Ctx, *, ongoing: str = "a day", per_day: bool = False) -> None:
    doc = ctx.lead
    lpd = qty(doc, "water_l_per_day")
    if lpd is None:
        t.headline = "Water is being lost"
        return
    t.headline = f"About {num(lpd)} L {ongoing}"
    people = lpd / LITRES_PER_PERSON_DAY
    if people >= 0.5:
        t.impact_lines.append(
            f"That's about {people:.1f} times what one person in the Klang Valley "
            f"uses in a day ({num(LITRES_PER_PERSON_DAY)} L)."
        )
    t.impact_lines.append(OWNER_REPAIRS)
    t.how = "Litres a day come from the measured flow at your meter."
    _money(t, ctx, t.how, per_day=per_day)
    t.how += " Average use per person: SPAN (2023), via Air Selangor."


def _where_water_could_be(ctx: Ctx) -> str:
    tank = ctx.index.tank_fed_fixtures
    main = ctx.index.main_fed_fixtures
    return (
        f"It could be any of the {len(tank)} fixtures fed by your roof tank (the bathrooms), "
        f"one of the {len(main)} fed straight from the mains "
        f"({ctx.names.listing(main, limit=3)}), or a pipe between them."
    )


def _night_flow(ctx: Ctx) -> Text:
    d = ctx.lead
    mnf = evn(d, "night_min_flow")
    nights = evn(d, "nights_above_threshold")
    limit = evn(d, "threshold")
    lpd = qty(d, "water_l_per_day")
    night_words = "last night" if not nights or nights < 2 else f"on {num(nights)} nights so far"
    summary = (
        f"Between 2am and 4am, when a home is normally still, water kept flowing {night_words}"
        + (f": never less than {smart(mnf)} L/min" if mnf is not None else "")
        + (f", about {num(lpd)} litres a day." if lpd is not None else ".")
        + " Your water meter can't tell which fixture or pipe it is. "
        + _where_water_could_be(ctx)
        + " A short guided check will narrow it down."
    )
    t = Text(
        title="Water is running when nobody is using it",
        location="Somewhere after your water meter",
        summary=summary,
        confidence_note=(
            "Detected: the lowest flow at your meter stayed above the night-time limit"
            + (f" ({smart(limit)} L/min)" if limit is not None else "")
            + ". The meter alone can't pinpoint where."
        ),
        headline="",
        actions=[
            act(
                "guided_check",
                "Find it with a guided check",
                primary=True,
                detail="Close one bathroom's stop valve at a time while Penjaga watches the meter",
            ),
            plumber(),
        ],
        evidence_title="Water flow at your meter, last 3 days",
        stats=[
            ("Lowest night-time flow", f"{smart(mnf)} L/min" if mnf is not None else NONE),
            ("Nights above the limit", num(nights) if nights is not None else NONE),
            ("Night-time limit", f"{smart(limit)} L/min" if limit is not None else NONE),
        ],
        explanation=(
            "Penjaga checks your meter every night between 2am and 4am. If the lowest flow in "
            "that window stays above the limit, water is going somewhere: a fixture that doesn't "
            "seal, or a leaking pipe."
        ),
        focus_ids=[ctx.index.meter_id],
        focus_label="Water meter: the leak is somewhere after it",
    )
    _water_impact(t, ctx)
    ref = (d["series_refs"] or [None])[0]
    if ref is not None:
        end = house_dt(ref["window"]["to"])
        start = end - timedelta(days=3)
        s, e, bucket = window(start, end, float(ref["bucket_s"]))
        night = end - house_dt(ref["window"]["from"])
        bands = []
        for back in range(3):
            night_end = end - timedelta(days=back)
            bands.append(
                ((night_end - night).isoformat(), night_end.isoformat(), "Night check (2am to 4am)")
            )
        lines = (
            ({"v": limit, "label": "Night-time limit", "tone": "warn"},)
            if limit is not None
            else ()
        )
        t.series = SeriesPlan(ref["stream"], s, e, bucket, "L/min", lines=lines, bands=tuple(bands))
    return t


LEAK_KINDS: dict[str, tuple[str, str, str]] = {
    # type: (title, what it looks like, plural of the candidate kind)
    "dripping_fixture_suspected": (
        "A tap or fixture may be dripping",
        "a steady small flow, like a tap or shower that doesn't fully close",
        "taps and fixtures",
    ),
    "running_cistern_suspected": (
        "A toilet cistern may keep refilling",
        "short refills every few minutes, the signature of a toilet cistern that doesn't seal",
        "toilet cisterns",
    ),
    "tank_overflow_suspected": (
        "Your roof tank may be overflowing",
        "the tank's level staying full while water keeps flowing in",
        "tank valves",
    ),
    "concealed_leak_suspected": (
        "There may be a hidden leak in a pipe",
        "a constant flow with no fixture pattern, which points to a pipe inside a wall or ceiling",
        "concealed pipes",
    ),
}


def _leak_model(ctx: Ctx) -> Text:
    d = ctx.lead
    title, pattern, plural = LEAK_KINDS[d["type"]]
    word = certainty(d)
    cands = [c for c in d["candidates"] if ctx.index.exists(c["object_id"])]
    ids = [c["object_id"] for c in cands]
    mnf = evn(d, "night_min_flow")
    if len(cands) == 1 and float(cands[0]["p"]) >= 0.9:
        only = cands[0]["object_id"]
        location = f"{ctx.names.where(only)} · {ctx.names.of(only)}"
        where = f"It is most likely the {ctx.names.of(only)}."
    elif cands:
        equal = max(c["p"] for c in cands) - min(c["p"] for c in cands) < 1e-6
        more = float(d["candidates_other_p"]) > 0.001
        location = f"One of your {plural}"
        if equal:
            where = (
                f"The meter can't tell which one: {ctx.names.listing(ids)} are equally likely"
                + (", and so are others." if more else ".")
            )
        else:
            where = f"Most likely the {ctx.names.of(ids[0])}, then {ctx.names.listing(ids[1:])}."
    else:
        location = "Somewhere after your water meter"
        where = _where_water_could_be(ctx)
    t = Text(
        title=title,
        location=location,
        summary=(
            "Overnight flow at your meter"
            + (f" ({smart(mnf)} L/min at its lowest)" if mnf is not None else "")
            + f" shows {pattern}. {where}"
        ),
        confidence_note=(
            f"{word}: Penjaga's leak model puts the chance that this is a leak at "
            f"{pct(float(d['confidence']))} (calibrated on held-out test data)."
        ),
        headline="",
        evidence_title="Water flow at your meter overnight",
        stats=[("Lowest night-time flow", f"{smart(mnf)} L/min" if mnf is not None else NONE)],
        explanation=f"The flow pattern overnight matches {finding_label(d['type'])}.",
        focus_ids=ids or [ctx.index.meter_id],
        focus_label=location,
    )
    for name, label, fmt in (
        ("leak_probability", "Chance it's a leak", pct),
        ("frac_flowing", "Share of the night with flow", pct),
        ("interval_median_min", "Typical gap between refills", lambda v: f"{smart(v)} min"),
    ):
        value = evn(d, name)
        if value is not None:
            t.stats.append((label, fmt(value)))
    if d["type"] in ("tank_overflow_suspected", "concealed_leak_suspected"):
        t.actions = [plumber(primary=True)]
    else:
        t.actions = [
            act(
                "guided_check",
                "Find it with a guided check",
                primary=True,
                detail="Close one valve at a time while Penjaga watches the meter",
            ),
            plumber(),
        ]
    _water_impact(t, ctx)
    ref = (d["series_refs"] or [None])[0]
    if ref is not None:
        s, e, bucket = window(
            house_dt(ref["window"]["from"]), house_dt(ref["window"]["to"]), float(ref["bucket_s"])
        )
        t.series = SeriesPlan(ref["stream"], s, e, bucket, "L/min")
    return t


def _burst(ctx: Ctx) -> Text:
    d = ctx.lead
    mean = evn(d, "mean_flow")
    minutes = evn(d, "window")
    span = f"{num(minutes)} minutes" if minutes is not None else "a long time"
    t = Text(
        title="Water has been flowing nonstop at a high rate",
        location="Somewhere after your water meter",
        summary=(
            f"For more than {span}, water has flowed through your meter without a single pause"
            + (f", at about {smart(mean)} L/min" if mean is not None else "")
            + ". Normal use always pauses. This looks like a burst pipe or a tap left fully "
            "open. If nobody is running water, act now."
        ),
        confidence_note=f"Detected: the flow didn't pause once in {span}.",
        headline="",
        actions=[
            plumber(primary=True),
            show(
                "Show me the main stop valve",
                "water",
                ["valve_main_stop"],
                "urgent",
                "Main stop valve",
            ),
            management(),
        ],
        evidence_title="Water flow at your meter",
        stats=[
            ("Average flow", f"{smart(mean)} L/min" if mean is not None else NONE),
            ("Without a pause for", span),
        ],
        explanation=(
            "Even a long shower or a washing machine stops and starts. Flow that never drops "
            "for this long means water is escaping."
        ),
        focus_ids=[ctx.index.meter_id, "valve_main_stop"],
        focus_label="Main stop valve",
    )
    _water_impact(t, ctx, ongoing="a day if it continues", per_day=True)
    t.impact_lines.insert(0, "Escaping water can quickly damage walls, ceilings and wiring.")
    start = house_dt(d["first_seen"]) - timedelta(hours=3)
    s, e, bucket = window(start, until(ctx, d, timedelta(hours=1)), 60.0)
    t.series = SeriesPlan("water.main.flow_lpm", s, e, bucket, "L/min")
    return t


# ---------------------------------------------------------------------- L2 electrical


def _circuit(ctx: Ctx, doc: dict[str, Any]) -> str | None:
    for object_id in doc["object_ids"]:
        kind = ctx.index.kind_of(object_id)
        if kind is ElementKind.circuit:
            return str(object_id)
        if kind is ElementKind.mcb:
            under = ctx.index.circuits_under(object_id)
            if len(under) == 1:
                return under[0]
    return None


def _device(doc: dict[str, Any], prefix: str) -> str | None:
    return next((i for i in doc["object_ids"] if str(i).startswith(prefix)), None)


def _rooms(ctx: Ctx, object_id: str) -> str:
    rooms = ctx.index.rooms_of(object_id)
    return ctx.names.listing([r for r in rooms], limit=2) if rooms else ""


def _board_action(ctx: Ctx, device: str | None, severity: str) -> dict[str, Any]:
    ids = ["db_main"] + ([device] if device else [])
    return show("Show me the breaker", "electrical", ids, severity, "Distribution board")


def _electrical_actions(
    ctx: Ctx, device: str | None, *, urgent_trade: bool
) -> list[dict[str, Any]]:
    sev = ctx.case.severity
    return [
        electrician(primary=urgent_trade),
        _board_action(ctx, device, sev),
        management(),
    ]


def _overload(ctx: Ctx) -> Text:
    d = ctx.lead
    circ = _circuit(ctx, d)
    mcb = _device(d, "mcb_")
    name = ctx.names.of(circ) if circ else "this"
    place = ctx.names.raw(circ) if circ else "Distribution board"
    m15, m1 = evn(d, "mean_current_15min"), evn(d, "mean_current_1min")
    rated = evn(d, "rated_current")
    mult = evn(d, "multiple_of_rating")
    peak = max(v for v in (m15, m1, 0.0) if v is not None)
    appliances = ctx.index.appliances_on_circuit(circ) if circ else ()
    on_it = (
        f" Appliances on it include {ctx.names.listing(list(appliances), limit=3)}."
        if appliances
        else ""
    )
    urgent = d["severity"] == "urgent"
    t = Text(
        title=f"The {name} circuit is overloaded",
        location=f"{_rooms(ctx, circ)} · {place}" if circ else place,
        summary=(
            f"The {name} circuit has been carrying {smart(peak)} A"
            + (f", {smart(mult)} times its {num(rated)} A breaker rating" if mult and rated else "")
            + ". The cable heats up when a circuit carries more than it is rated for. "
            "Too many appliances are probably running on it at once."
            + on_it
            + ("" if urgent else " Unplug what you don't need on this circuit.")
        ),
        confidence_note=(
            "Detected: the current measured at your distribution board crossed the breaker "
            "rating. This comes from Penjaga's fixed safety rules, which nothing can override."
        ),
        headline="Risk of overheating and fire" if urgent else "Risk of the cable overheating",
        impact_lines=([f"Measured {smart(peak)} A on a {num(rated)} A breaker."] if rated else []),
        actions=_electrical_actions(ctx, mcb, urgent_trade=d["severity"] != "heads_up"),
        evidence_title=f"Current on the {name} circuit",
        stats=[
            ("15-minute average", f"{smart(m15)} A" if m15 is not None else NONE),
            ("1-minute average", f"{smart(m1)} A" if m1 is not None else NONE),
            ("Breaker rating", f"{num(rated)} A" if rated is not None else NONE),
        ],
        explanation=(
            "A breaker is sized for its cable. Running above the rating for minutes at a time "
            "heats the cable inside your walls."
        ),
        focus_layer="electrical",
        focus_ids=[i for i in (circ, mcb, "db_main") if i],
        focus_label=place,
    )
    ref = (d["series_refs"] or [None])[0]
    if ref is not None:
        start = house_dt(ref["window"]["from"])
        s, e, bucket = window(start, until(ctx, d, timedelta(hours=1)), float(ref["bucket_s"]))
        lines = (
            ({"v": rated, "label": f"Breaker rating ({num(rated)} A)", "tone": "warn"},)
            if rated is not None
            else ()
        )
        t.series = SeriesPlan(ref["stream"], s, e, bucket, "A", lines=lines)
    return t


def _rcd_scope(ctx: Ctx, rcd: str) -> str:
    return ctx.names.listing(ctx.index.circuits_under(rcd), limit=3)


def _leakage_elevated(ctx: Ctx) -> Text:
    d = ctx.lead
    rcd = _device(d, "rcd_") or "rcd_main"
    switch = ctx.names.of(rcd)
    mean = evn(d, "mean_residual_10min")
    rated = evn(d, "rated_residual")
    share = evn(d, "share_of_rated_residual")
    t = Text(
        title=f"Electricity is leaking to earth behind your {switch}",
        location=ctx.names.where(rcd),
        summary=(
            f"About {smart(mean or 0)} mA is leaking to earth on the circuits behind your {switch}"
            + (f", {pct(share)} of its {num(rated)} mA rating" if share and rated else "")
            + ". At its rating it trips and cuts the power. Leakage like this often comes from "
            "moisture, or a worn cable or appliance. If an appliance on these circuits is damaged "
            "or wet, unplug it."
        ),
        confidence_note=(
            "Detected: measured at the safety switch. This comes from Penjaga's fixed safety "
            "rules, which nothing can override."
        ),
        headline="Risk of electric shock and nuisance tripping",
        impact_lines=[f"Circuits behind it: {_rcd_scope(ctx, rcd)}."],
        actions=_electrical_actions(ctx, rcd, urgent_trade=True),
        evidence_title=f"Earth leakage at your {switch}",
        stats=[
            ("10-minute average", f"{smart(mean)} mA" if mean is not None else NONE),
            ("Switch rating", f"{num(rated)} mA" if rated is not None else NONE),
            ("Share of rating", pct(share) if share is not None else NONE),
        ],
        explanation="A little leakage is normal. A level this close to the trip point is not.",
        focus_layer="electrical",
        focus_ids=[rcd, "db_main", *ctx.index.circuits_under(rcd)],
        focus_label=switch,
    )
    ref = (d["series_refs"] or [None])[0]
    if ref is not None:
        start = house_dt(ref["window"]["from"])
        s, e, bucket = window(start, until(ctx, d, timedelta(hours=1)), float(ref["bucket_s"]))
        lines = (
            ({"v": rated, "label": f"Trips at up to {num(rated)} mA", "tone": "danger"},)
            if rated is not None
            else ()
        )
        t.series = SeriesPlan(ref["stream"], s, e, bucket, "mA", lines=lines)
    return t


def _leakage_rising(ctx: Ctx) -> Text:
    d = ctx.lead
    rcd = _device(d, "rcd_") or "rcd_main"
    switch = ctx.names.of(rcd)
    rated = evn(d, "rated_residual")
    trend = evn(d, "trend")
    if trend is not None:
        median = evn(d, "daily_median_residual")
        days = evn(d, "days_to_non_operating_limit")
        what = (
            f"Leakage to earth on your {switch} has been rising by about {smart(trend)} mA a day"
            + (f", now around {smart(median)} mA" if median is not None else "")
            + "."
            + (
                f" At this rate it would reach the level where the switch may trip in about "
                f"{num(days)} days."
                if days is not None
                else ""
            )
        )
        stats = [
            ("Daily median now", f"{smart(median)} mA" if median is not None else NONE),
            ("Rising by", f"{smart(trend)} mA a day"),
            ("Days until it may trip", num(days) if days is not None else NONE),
        ]
    else:
        base, recent = evn(d, "baseline_direct_leakage"), evn(d, "recent_direct_leakage")
        what = (
            f"Leakage on the circuits behind your {switch} has risen"
            + (
                f" from {smart(base)} mA to {smart(recent)} mA compared with its own normal"
                if base is not None and recent is not None
                else " above its own normal"
            )
            + ". It's still well below the trip level."
        )
        stats = [
            ("Normal for this switch", f"{smart(base)} mA" if base is not None else NONE),
            ("Recently", f"{smart(recent)} mA" if recent is not None else NONE),
        ]
    cands = [c["object_id"] for c in d["candidates"] if ctx.index.exists(c["object_id"])]
    where = (
        f" It is on one of these circuits: {ctx.names.listing(cands, limit=4)}."
        if len(cands) > 1
        else (f" It is on the {ctx.names.of(cands[0])} circuit." if cands else "")
    )
    t = Text(
        title=f"Earth leakage is rising on your {switch}",
        location=ctx.names.where(rcd),
        summary=(
            what + where + " A steady rise is an early sign of moisture getting into a fitting, "
            "or a cable or appliance wearing out. Caught early, it's usually a small repair."
        ),
        confidence_note=f"{certainty(d)}: a sustained rise measured at the safety switch.",
        headline="An early warning, before anything trips",
        impact_lines=[f"Circuits behind it: {_rcd_scope(ctx, rcd)}."],
        actions=_electrical_actions(ctx, rcd, urgent_trade=d["severity"] != "heads_up"),
        evidence_title=f"Earth leakage at your {switch}, last 7 days",
        stats=stats + ([("Switch rating", f"{num(rated)} mA")] if rated is not None else []),
        explanation=(
            "Penjaga compares the leakage with this switch's own normal level, day by day."
        ),
        focus_layer="electrical",
        focus_ids=[rcd, "db_main", *cands],
        focus_label=switch,
    )
    end = until(ctx, d)
    s, e, bucket = window(end - timedelta(days=7), end, 3600.0)
    lines = (
        ({"v": rated, "label": f"Trips at up to {num(rated)} mA", "tone": "danger"},)
        if rated is not None
        else ()
    )
    t.series = SeriesPlan(f"elec.rcd.{rcd}.residual_ma", s, e, bucket, "mA", lines=lines)
    return t


def _trip_headline(ctx: Ctx, device: str | None, circ: str | None) -> str:
    if circ:
        return f"Power was cut on the {ctx.names.of(circ)} circuit"
    if device and set(ctx.index.circuits_under(device)) == set(ctx.index.ids(ElementKind.circuit)):
        return "Power was cut to the whole house"
    if device:
        return f"Power was cut on {_rcd_scope(ctx, device)}"
    return "Power was cut"


def _trip(ctx: Ctx) -> Text:
    d = ctx.lead
    device = next(iter(d["object_ids"]), None)
    repeated = d["type"] == "repeated_trips"
    trips = evn(d, "trips_in_24h")
    is_mcb = device is not None and ctx.index.kind_of(device) is ElementKind.mcb
    circ = _circuit(ctx, d) if is_mcb else None
    thing = ctx.names.of(device) if device else "breaker"
    when = clock(d["first_seen"])
    if repeated:
        title = f"The {thing} keeps tripping"
        summary = (
            f"The {thing} has tripped"
            + (f" {num(trips)} times" if trips else " again")
            + f" in 24 hours. Repeated tripping means a fault or an overload that an "
            f"{ST_CONTRACTOR} needs to find."
        )
    elif is_mcb:
        title = f"The {thing} tripped"
        summary = (
            f"The {thing} switched off by itself at {when}, so everything on that circuit lost "
            "power. A one-off trip often comes from a faulty appliance or too much load. You can "
            "switch it back on once. If it trips again, leave it off and unplug what is on it."
        )
    else:
        title = f"Your {thing} tripped"
        summary = (
            f"Your {thing} tripped at {when}. A safety switch trips when electricity leaks to "
            "earth, often from moisture or a faulty appliance; it is protecting you from electric "
            "shock. You can switch it back on once. If it trips again, leave it off."
        )
    earlier = sorted(
        str(r["first_seen"])
        for r in ctx.case.related
        if r["type"] in ("protective_device_trip", "repeated_trips")
        and r["object_ids"] == d["object_ids"]
        and r["first_seen"] < d["first_seen"]
    )
    if earlier and not repeated:
        summary += f" It also tripped at {clock(earlier[0])}, so this is not the first time."
    t = Text(
        title=title,
        location=ctx.names.where(device) if device else "Distribution board",
        summary=summary,
        confidence_note="Detected: your distribution board reported the trip.",
        headline=_trip_headline(ctx, device, circ),
        actions=_electrical_actions(ctx, device, urgent_trade=True),
        evidence_title="Around the trip",
        stats=[("Tripped at", when)]
        + ([("Trips in 24 hours", num(trips))] if trips is not None else []),
        explanation=(
            "Your board monitor records every trip. Trips that repeat point to a fault that "
            "resetting won't fix."
        ),
        focus_layer="electrical",
        focus_ids=[i for i in ("db_main", device, circ) if i],
        focus_label=ctx.names.of(device) if device else "Distribution board",
    )
    trip_at = house_dt(d["first_seen"])
    end = min(trip_at + timedelta(hours=1), house_dt(ctx.now))
    s, e, bucket = window(trip_at - timedelta(hours=1), end, 60.0)
    markers = tuple(
        (str(r["first_seen"]), "Trip")
        for r in [d, *ctx.case.related]
        if r["type"] in ("protective_device_trip", "repeated_trips")
    )
    if circ:
        t.series = SeriesPlan(f"elec.circuit.{circ}.current_a", s, e, bucket, "A", markers=markers)
    elif device:
        t.series = SeriesPlan(f"elec.rcd.{device}.residual_ma", s, e, bucket, "mA", markers=markers)
    return t


# ---------------------------------------------------------------------- L3 appliances


def _energy_impact(t: Text, ctx: Ctx) -> None:
    extra = qty(ctx.lead, "extra_energy_kwh_per_day")
    if extra is None:
        t.headline = "Using more energy than it should"
        return
    t.headline = f"About {smart(extra)} kWh extra a day"
    t.impact_lines.append(f"About {smart(extra * 30)} kWh extra over a month.")
    t.how = "Extra energy compared with this appliance's own normal, from its measured use."
    _money(t, ctx, t.how)


def _appliance_actions(ctx: Ctx, app: str) -> list[dict[str, Any]]:
    actions = []
    if ctx.index.element(app).model.kind == "air_conditioner":
        actions.append(
            act(
                "book_vendor",
                "Book an aircon service",
                vendor_category="aircon_service",
                primary=True,
            )
        )
    actions.append(
        show("Show me where it is", "electrical", [app], ctx.case.severity, ctx.names.of(app))
    )
    return actions


def _aircon(ctx: Ctx) -> Text:
    d = ctx.lead
    app = next(iter(d["object_ids"]))
    name = ctx.names.of(app)
    e, exp = evn(d, "energy_per_running_hour"), evn(d, "expected_energy_per_running_hour")
    temp = evn(d, "outdoor_temperature_while_running")
    gap = qty(d, "efficiency_gap_pct")
    t = Text(
        title=f"{name} is losing efficiency",
        location=ctx.names.where(app),
        summary=(
            "For the same outdoor temperature"
            + (f" (about {smart(temp)} °C while it ran)" if temp is not None else "")
            + ", it now uses "
            + (
                f"{smart(e)} kWh per running hour against an expected {smart(exp)} kWh"
                if e is not None and exp is not None
                else "more energy per running hour than it used to"
            )
            + (f", about {num(gap)}% more" if gap is not None else "")
            + ". That usually means dirty filters or coils, or low refrigerant. A service "
            "usually fixes it."
        ),
        confidence_note=(
            f"{certainty(d)}: a sustained rise against this aircon's own first weeks, corrected "
            "for outdoor temperature."
        ),
        headline="",
        actions=_appliance_actions(ctx, app),
        evidence_title="Daily energy used by this aircon, last 4 weeks",
        stats=[
            ("Energy per running hour", f"{smart(e)} kWh" if e is not None else NONE),
            ("Expected", f"{smart(exp)} kWh" if exp is not None else NONE),
            ("Running hours (last day)", smart(evn(d, "running_hours") or 0)),
        ],
        explanation=(
            "Penjaga learns how much energy this aircon needs at each outdoor temperature, then "
            "watches for a lasting rise."
        ),
        focus_layer="electrical",
        focus_ids=[app],
        focus_label=name,
    )
    _energy_impact(t, ctx)
    trend = qty(d, "efficiency_gap_trend_pct_per_week")
    if trend is not None:
        t.impact_lines.append(f"The gap is growing by about {smart(trend)}% a week.")
    circ = ctx.index.circuit_of_appliance(app)
    if circ:
        end = until(ctx, d)
        s, e2, bucket = window(end - timedelta(days=28), end, 86400.0)
        # energy_wh is Wh per one-minute sample: mean x 1440 / 1000 = kWh a day
        t.series = SeriesPlan(
            f"elec.circuit.{circ}.energy_wh", s, e2, bucket, "kWh/day", scale=1.44
        )
    return t


def _fridge(ctx: Ctx) -> Text:
    d = ctx.lead
    app = next(iter(d["object_ids"]))
    name = ctx.names.of(app)
    duty, base_duty = evn(d, "duty_cycle"), evn(d, "baseline_duty_cycle")
    power, base_power = evn(d, "running_power"), evn(d, "baseline_running_power")
    run, base_run = evn(d, "mean_run"), evn(d, "baseline_mean_run")
    wear = d["type"] == "fridge_compressor_wear"
    if wear:
        title = "Your fridge's compressor may be wearing"
        summary = (
            "Its compressor now draws "
            + (
                f"{num(power)} W when running (normally {num(base_power)} W)"
                if power and base_power
                else "more power when running"
            )
            + (
                f" and runs in {smart(run)}-minute bursts (normally {smart(base_run)})"
                if run and base_run
                else ""
            )
            + ". Changes like this can be early signs of compressor wear. Worth having it "
            "checked before it fails."
        )
    else:
        title = "Your fridge is running more than usual"
        summary = (
            "The compressor is now running "
            + (
                f"{pct(duty)} of the time, up from {pct(base_duty)}"
                if duty is not None and base_duty is not None
                else "more of the time than usual"
            )
            + ", even after allowing for the weather. Common causes: a door seal that doesn't "
            "close fully, dusty coils at the back, or a very full fridge."
        )
    t = Text(
        title=title,
        location=ctx.names.where(app),
        summary=summary,
        confidence_note=(
            f"{certainty(d)}: a sustained change against your fridge's own first weeks, "
            "corrected for room temperature."
        ),
        headline="",
        actions=_appliance_actions(ctx, app),
        evidence_title="Fridge power, last 2 weeks",
        stats=[
            (
                "Running time",
                f"{pct(duty)} (normally {pct(base_duty)})"
                if duty is not None and base_duty is not None
                else NONE,
            ),
            (
                "Power when running",
                f"{num(power)} W (normally {num(base_power)} W)"
                if power is not None and base_power is not None
                else NONE,
            ),
            (
                "Typical run",
                f"{smart(run)} min (normally {smart(base_run)})"
                if run is not None and base_run is not None
                else NONE,
            ),
        ],
        explanation=(
            "Penjaga learns how your fridge normally cycles at each temperature and watches for "
            "a lasting change."
        ),
        focus_layer="electrical",
        focus_ids=[app],
        focus_label=name,
    )
    _energy_impact(t, ctx)
    end = until(ctx, d)
    s, e, bucket = window(end - timedelta(days=14), end, 21600.0)
    t.series = SeriesPlan(f"elec.appliance.{app}.power_w", s, e, bucket, "W")
    return t


# ---------------------------------------------------------------------- L5 supply


def _water_supply(ctx: Ctx) -> Text:
    d = ctx.lead
    pressure, limit = evn(d, "mean_inlet_pressure_10min"), evn(d, "threshold")
    tank_l, hours = qty(d, "tank_water_l"), qty(d, "tank_hours_left")
    main = ctx.index.main_fed_fixtures
    if hours is not None:
        headline = f"Your roof tank had about {smart(hours)} hours of water at your usual use"
    elif tank_l is not None:
        headline = f"Your roof tank had about {num(tank_l)} L when the supply stopped"
    else:
        headline = "Taps fed straight from the mains won't run"
    t = Text(
        title="Water supply to your house has stopped",
        location=f"Main water supply · {ctx.names.where(ctx.index.meter_id)}",
        summary=(
            "Water pressure at your inlet dropped"
            + (f" to {smart(pressure)} bar" if pressure is not None else "")
            + (f", below {smart(limit)} bar" if limit is not None else "")
            + ", so water isn't reaching your house from the mains. Your roof tank keeps the "
            "bathrooms supplied for now."
        ),
        confidence_note="Detected: measured directly at your water inlet.",
        headline=headline,
        impact_lines=[
            f"Taps fed straight from the mains ({ctx.names.listing(main, limit=3)}) won't run "
            "until the supply returns."
        ]
        + (
            [f"Tank level when it stopped: about {num(tank_l)} L."]
            if tank_l is not None and hours is not None
            else []
        ),
        actions=[management(primary=True, detail="They can check with the water utility")],
        evidence_title="Water pressure at your inlet",
        stats=[
            ("Pressure", f"{smart(pressure)} bar" if pressure is not None else NONE),
            ("Low-pressure limit", f"{smart(limit)} bar" if limit is not None else NONE),
        ]
        + ([("Roof tank", f"{num(tank_l)} L")] if tank_l is not None else []),
        explanation="Pressure near zero at the inlet means water is not reaching your house.",
        focus_ids=[ctx.index.meter_id, "pipe_supply_main", "tank_roof_main"],
        focus_label="Main water supply",
    )
    start = house_dt(d["first_seen"]) - timedelta(hours=3)
    s, e, bucket = window(start, until(ctx, d, timedelta(hours=1)), 60.0)
    lines = (
        ({"v": limit, "label": "Low-pressure limit", "tone": "warn"},) if limit is not None else ()
    )
    t.series = SeriesPlan("water.main.pressure_bar", s, e, bucket, "bar", lines=lines)
    return t


def _power_supply(ctx: Ctx) -> Text:
    d = ctx.lead
    when = clock(d["first_seen"])
    t = Text(
        title="Your power supply cut out",
        location=ctx.names.where("db_main"),
        summary=(
            f"Mains power to your house was lost at {when}. Your board monitor reports that the "
            "incoming supply is off, which is different from a tripped breaker inside your home."
        ),
        confidence_note="Detected: reported by the monitor at your distribution board.",
        headline="Lights and appliances are off until power returns",
        impact_lines=["Penjaga notes when the supply comes back."],
        actions=[management(primary=True, detail="They can check whether it affects the street")],
        evidence_title="Mains voltage",
        stats=[("Supply lost at", when)],
        explanation="When the incoming supply is lost, the monitor stops seeing mains voltage.",
        focus_layer="electrical",
        focus_ids=["db_main"],
        focus_label="Distribution board",
    )
    start = house_dt(d["first_seen"]) - timedelta(hours=2)
    s, e, bucket = window(start, until(ctx, d, timedelta(hours=1)), 60.0)
    t.series = SeriesPlan(
        "elec.main.voltage_v", s, e, bucket, "V", markers=((d["first_seen"], "Supply lost"),)
    )
    return t


TEMPLATES: dict[str, Callable[[Ctx], Text]] = {
    "night_flow_unexplained": _night_flow,
    "dripping_fixture_suspected": _leak_model,
    "running_cistern_suspected": _leak_model,
    "tank_overflow_suspected": _leak_model,
    "concealed_leak_suspected": _leak_model,
    "burst_pipe_suspected": _burst,
    "circuit_overload": _overload,
    "earth_leakage_elevated": _leakage_elevated,
    "earth_leakage_rising": _leakage_rising,
    "protective_device_trip": _trip,
    "repeated_trips": _trip,
    "aircon_efficiency_decline": _aircon,
    "fridge_duty_cycle_rise": _fridge,
    "fridge_compressor_wear": _fridge,
    "water_supply_interruption": _water_supply,
    "power_supply_interruption": _power_supply,
}


# ---------------------------------------------------------------------- the Case


def _safety(ctx: Ctx) -> list[str] | None:
    """The reviewed guidance for an urgent case: the lead's ``safety_guidance_key``, else the
    newest one in the case's history (a safety floor can hold a case at urgent after detection
    lowered the revision that carried the key)."""
    docs = [ctx.lead] + [d for t in ctx.case.tracks for d in reversed(t.revisions)]
    key = next((d["safety_guidance_key"] for d in docs if d.get("safety_guidance_key")), None)
    if key is None:
        return None
    circuits = [i for i in ctx.case.object_ids() if i.startswith("circ_")]
    circuit = f"the {ctx.names.of(circuits[0])} circuit" if circuits else "that circuit"
    return safety_lines(key, circuit=circuit)


def render(
    case: CaseState,
    index: HouseIndex,
    names: Names,
    now: str,
    costs: Mapping[str, CostEstimate | str] | None = None,
    *,
    extras: CaseExtras | None = None,
) -> Rendered:
    """The app's ``Case`` for ``case``; ``costs`` holds the cost of waiting by finding ID, or
    the reason there is no figure."""
    ctx = Ctx(index, names, now, case, (costs or {}).get(case.lead["id"]))
    lead = case.lead
    t = TEMPLATES[lead["type"]](ctx)
    extras = extras or CaseExtras()
    timeline = [{"t": epoch_ms(e.t), "actor": e.actor, "text": e.text} for e in case.timeline]
    status, severity = _notices(t, case, extras, timeline)
    summary = t.summary
    others = [d for d in case.related if d["type"] != lead["type"]]
    if others:
        labels = sorted({finding_label(d["type"]) for d in others})
        summary += f" Penjaga also noticed {' and '.join(labels)}; that's part of this case."
    actions = list(t.actions)
    outcome = case.outcome
    guided: dict[str, Any] | None = None
    if lead["type"] in GUIDED_TYPES and case.status in ("open", "monitoring", "action_pending"):
        guided, located = _guided(ctx, extras)
        if located:
            fixture, valve = located
            t.title = f"The {names.of(fixture)} is leaking"
            t.location = f"{names.where(fixture)} \u00b7 {names.of(fixture)}"
            t.confidence_note = "Confirmed by your check: closing its valve stopped the flow."
            t.focus_ids = [fixture, valve]
            t.focus_label = names.of(fixture)
            actions = [a for a in actions if a["kind"] != "guided_check"]
    booking = _booking(extras, now)
    if booking and booking["status"] != "completed":
        actions = [a for a in actions if a["kind"] != "book_vendor"]
    if status == "resolved":
        summary += (
            f" This has stopped: readings back to normal since {clock(case.resolved_at or now)}."
        )
        actions = []
        outcome = outcome or "Readings back to normal"
    elif status == "dismissed":
        actions = []
    elif status == "monitoring" and extras.notice is None:
        summary += " Penjaga is keeping an eye on it and will tell you if it gets worse."
    if status in ("open", "monitoring") and dismissable(case):
        actions.append(act("dismiss", "Not an issue"))
    price = job_price(lead["type"])
    for action in actions:
        if action["kind"] == "book_vendor" and price:
            action["detail"] = price
    focus_ids = list(validate_ids(index, t.focus_ids))
    for action in actions:
        if "focus" in action:
            action["focus"]["object_ids"] = list(validate_ids(index, action["focus"]["object_ids"]))
    doc: dict[str, Any] = {
        "id": case.id,
        "status": status,
        "layer": case.layer,
        "severity": severity,
        "title": t.title,
        "location": t.location,
        "summary": summary,
        "confidence": float(lead["confidence"]),
        "confidence_note": t.confidence_note,
        "impact": {"headline": t.headline, "lines": t.impact_lines},
        "actions": actions,
        "evidence": {
            "title": t.evidence_title,
            "series": {"unit": t.series.unit if t.series else "", "points": []},
            "stats": [{"label": label, "value": value} for label, value in t.stats],
            "explanation": t.explanation,
        },
        "timeline": timeline,
        "focus": {
            "layer": t.focus_layer,
            "object_ids": focus_ids,
            "severity": severity,
            "label": t.focus_label,
        },
        "opened_at": epoch_ms(case.opened_at),
    }
    if t.how:
        doc["impact"]["how"] = t.how
    safety = _safety(ctx) if severity == "urgent" else None
    if safety:
        doc["safety"] = safety
    if guided is not None:
        doc["guided"] = guided
    if booking is not None:
        doc["booking"] = booking
    if case.resolved_at:
        doc["resolved_at"] = epoch_ms(case.resolved_at)
    if outcome:
        doc["outcome"] = outcome
    return Rendered(doc, t.series)


def _notices(
    t: Text, case: CaseState, extras: CaseExtras, timeline: list[dict[str, Any]]
) -> tuple[str, str]:
    """What management notices change about a case; its (status, severity) as shown."""
    live = case.status in ("open", "monitoring", "action_pending")
    kind = case.lead["type"]
    if kind in SUPPLY_FINDINGS and extras.notice is not None:
        return _matched(t, extras.notice, case, timeline, live)
    if kind in SUPPLY_FINDINGS and extras.no_notice and live:
        t.impact_lines.append(
            "No notice from management covers this, so it may be a problem with the utility's "
            "supply. Management can check."
        )
    if extras.recovery is not None and kind in GUIDED_TYPES and live:
        t.summary += (
            " This began while water was coming back after the works in a management notice "
            f"({extras.recovery['title']}): your roof tank refilling also shows as water running. "
            "Penjaga will see whether it stops once the tank is full."
        )
    return case.status, case.severity


def _matched(
    t: Text, match: NoticeMatch, case: CaseState, timeline: list[dict[str, Any]], live: bool
) -> tuple[str, str]:
    """A supply drop a management notice explains: say so, show the works on the chart and the
    match on the timeline. Until the notice's recovery time it is expected (monitoring, info);
    after that it is late, and the case keeps its severity and the management action."""
    n = match.notice
    water = n["utility"] == "water"
    works = "scheduled works" if water else "planned outage"
    back = back_by(n)
    t.title = (
        "Water is off for the scheduled works" if water else "Power is off for the planned outage"
    )
    t.confidence_note = f"Matches a notice from {match.issuer}: {n['title']}."
    t.summary += f" This matches the {works} in that notice, so it isn't a fault in your home."
    note = str(n.get("recovery_note") or "").strip()
    expected = f"The notice expects supply back by {clock(back.isoformat())}."
    t.impact_lines.append(f"{expected} {note}" if note else expected)
    if match.late and live:
        t.impact_lines.append(
            "That time has passed, so it's worth asking management whether the works overran."
        )
    else:
        t.actions = [a for a in t.actions if a["kind"] != "send_management"]
    if t.series is not None:
        band = (n["starts_at"], back.isoformat(), "Scheduled works" if water else "Planned outage")
        t.series = replace(t.series, bands=(*t.series.bands, band))
    posted = max(epoch_ms(n.get("posted_at") or case.opened_at), epoch_ms(case.opened_at))
    text = f"Matched to the notice: {n['title']}"
    timeline.append({"t": posted, "actor": "penjaga", "text": text})
    timeline.sort(key=lambda e: int(e["t"]))
    if case.status == "open" and not match.late:
        return "monitoring", "info"
    return case.status, case.severity


def series_points(plan: SeriesPlan, summary: dict[str, Any]) -> dict[str, Any]:
    """The app's ``Series`` from a stream summary and its plan."""
    points = [
        {"t": epoch_ms(p["t"]), "v": round(float(p["mean"]) * plan.scale, 6)}
        for p in summary.get("points", [])
        if p.get("mean") is not None
    ]
    series: dict[str, Any] = {"unit": plan.unit, "points": points}
    if plan.lines:
        series["lines"] = [dict(line) for line in plan.lines]
    if plan.bands:
        series["bands"] = [
            {"from": epoch_ms(a), "to": epoch_ms(b), "label": label} for a, b, label in plan.bands
        ]
    if plan.markers:
        series["markers"] = [{"t": epoch_ms(t), "label": label} for t, label in plan.markers]
    return series
