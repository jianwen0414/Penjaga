"""The agent's tools (PRD §18.3): read-only views of the Core API's facts, plus two that make chips
for the app (show in 3D, open a case). Nothing here acts on the outside world: actions are only
proposed, and run in step 7 after the owner confirms.

Schemas stay within JSON Schema Draft 7 with ``additionalProperties: false``. Every ID an argument
names is validated against the house contract; an unknown ID comes back to the model as an error
it can correct. Outputs are derived data only (summaries, never raw readings).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from penjaga_api.costs import CostEstimate
from penjaga_api.ids import UnknownIdError, validate_ids
from penjaga_api.knowledge import get_house_record, get_safety_text, get_tariff, get_vendors
from penjaga_api.upstream import UpstreamUnavailableError
from penjaga_contract.house import ElementKind

if TYPE_CHECKING:
    from penjaga_api.service import ApiService

MAX_POINTS = 24
HOUSE_TZ = timezone(timedelta(hours=8))  # Asia/Kuala_Lumpur, no DST
READING_KINDS: dict[str, tuple[str, str | None]] = {
    # kind: (stream template, element kind the object_id must be)
    "water_flow": ("water.main.flow_lpm", None),
    "water_pressure": ("water.main.pressure_bar", None),
    "tank_level": ("water.tank.level_pct", None),
    "total_power": ("elec.main.power_w", None),
    "circuit_current": ("elec.circuit.{id}.current_a", "circuit"),
    "earth_leakage": ("elec.rcd.{id}.residual_ma", "rccb"),
    "outdoor_temperature": ("weather.outdoor.temp_c", None),
    "rainfall": ("weather.precip_mm", None),
}
ELEMENT_KINDS = [k.value for k in ElementKind]


def _fn(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


ID_LIST = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12}

TOOLS: list[dict[str, Any]] = [
    _fn("list_cases", "The home's current cases (problems Penjaga is tracking).", {}, []),
    _fn(
        "get_case",
        "Everything known about one case: what, where, how sure, cost, safety, actions, evidence.",
        {"case_id": {"type": "string"}},
        ["case_id"],
    ),
    _fn(
        "find_elements",
        "Find parts of the house (rooms, pipes, fixtures, circuits, appliances...) by kind and/or "
        "room. Returns IDs to use with other tools; never show IDs to the owner.",
        {
            "kind": {"type": "string", "enum": ELEMENT_KINDS},
            "room_id": {"type": "string"},
        },
        [],
    ),
    _fn(
        "trace_supply",
        "How an element is supplied: an appliance's circuit and breakers, a circuit's appliances "
        "and rooms, a fixture's water source, pipes and the valves that isolate it.",
        {"object_id": {"type": "string"}},
        ["object_id"],
    ),
    _fn(
        "get_house_record",
        "The house record for specific elements (install dates, ratings, nameplates).",
        {"object_ids": ID_LIST},
        ["object_ids"],
    ),
    _fn(
        "query_readings",
        "A summary of recent readings (at most 24 averaged points), e.g. water flow at the meter "
        "or current on one circuit (object_id = the circuit) or earth leakage (object_id = RCCB).",
        {
            "kind": {"type": "string", "enum": sorted(READING_KINDS)},
            "hours": {"type": "integer", "minimum": 1, "maximum": 168},
            "object_id": {"type": "string"},
        },
        ["kind", "hours"],
    ),
    _fn(
        "estimate_cost",
        "The cost of waiting for a case: ringgit a month from the official tariffs, and how it "
        "was worked out. Never do tariff arithmetic yourself.",
        {"case_id": {"type": "string"}},
        ["case_id"],
    ),
    _fn(
        "get_tariff",
        "The official tariff in force now.",
        {"kind": {"type": "string", "enum": ["electricity", "water"]}},
        ["kind"],
    ),
    _fn(
        "get_safety_text",
        "Reviewed safety guidance; quote it, never write your own safety instructions.",
        {
            "key": {
                "type": "string",
                "enum": ["sg_burst_pipe", "sg_circuit_overload", "sg_repeated_trips"],
            }
        },
        ["key"],
    ),
    _fn(
        "get_vendors",
        "Vendors near the home with typical price ranges and visit slots.",
        {"category": {"type": "string", "enum": ["plumber", "electrician", "aircon_service"]}},
        ["category"],
    ),
    _fn(
        "get_notices",
        "Current notices from management that reach this house (planned water or power works), "
        "each with what it means for this home.",
        {},
        [],
    ),
    _fn(
        "get_maintenance",
        "The home's maintenance reminders, soonest first: what, when it's due, how often, and "
        "the source with its confidence.",
        {},
        [],
    ),
    _fn(
        "focus_3d",
        "Offer the owner a 'Show me' button that points the 3D view at these elements.",
        {
            "object_ids": ID_LIST,
            "layer": {"type": "string", "enum": ["water", "electrical", "structure"]},
            "label": {"type": "string", "maxLength": 60},
        },
        ["object_ids", "layer", "label"],
    ),
    _fn(
        "open_case",
        "Offer the owner a button that opens one case.",
        {"case_id": {"type": "string"}, "label": {"type": "string", "maxLength": 60}},
        ["case_id", "label"],
    ),
]


@dataclass
class ToolBox:
    """Runs tool calls for one answer against one snapshot of the home."""

    service: ApiService
    now: str
    cases: list[dict[str, Any]]
    costs: Mapping[str, CostEstimate | str]  # by case ID: the cost of waiting, or why there is none
    chips: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)  # everything the model saw, for the guardrails
    viewed: list[str] = field(default_factory=list)  # cases the model looked up, in order

    async def run(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            handler = HANDLERS.get(name)
            if handler is None:
                raise ValueError(f"unknown tool {name!r}")
            result = await handler(self, args)
        except UnknownIdError as exc:
            result = {"error": f"{exc}. Use find_elements to look up real IDs."}
        except (KeyError, ValueError, TypeError) as exc:
            result = {"error": f"bad request: {exc}"}
        except UpstreamUnavailableError:
            result = {"error": "readings are unavailable right now"}
        text = json.dumps(result, ensure_ascii=False, default=str)
        self.outputs.append(text)
        return text

    # ------------------------------------------------------------------ handlers

    def _case(self, case_id: str) -> dict[str, Any]:
        for case in self.cases:
            if case["id"] == case_id:
                return case
        raise KeyError(f"no case {case_id!r}; call list_cases")

    def _named(self, ids: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
        return [{"id": i, "name": self.service.names.of(i)} for i in ids]


async def _list_cases(tb: ToolBox, _: dict[str, Any]) -> Any:
    return [
        {
            "case_id": c["id"],
            "title": c["title"],
            "status": c["status"],
            "severity": c["severity"],
            "where": c["location"],
        }
        for c in tb.cases
    ]


async def _get_case(tb: ToolBox, args: dict[str, Any]) -> Any:
    case = tb._case(str(args["case_id"]))
    if case["id"] not in tb.viewed:
        tb.viewed.append(case["id"])
    return case_facts(case)


async def _find_elements(tb: ToolBox, args: dict[str, Any]) -> Any:
    index = tb.service.index
    kind = args.get("kind")
    room = args.get("room_id")
    if room is not None:
        validate_ids(index, [room])
    ids = index.ids(ElementKind(kind)) if kind else index.ids()
    found = [i for i in ids if room is None or room in index.rooms_of(i)]
    return [
        {
            "id": i,
            "name": tb.service.names.of(i),
            "kind": index.kind_of(i).value,
            "where": tb.service.names.where(i),
        }
        for i in found[:40]
    ]


async def _trace_supply(tb: ToolBox, args: dict[str, Any]) -> Any:
    index = tb.service.index
    (object_id,) = validate_ids(index, [str(args["object_id"])])
    kind = index.kind_of(object_id)
    out: dict[str, Any] = {"element": tb._named([object_id])[0], "kind": kind.value}
    if kind in (ElementKind.appliance, ElementKind.socket_group, ElementKind.circuit):
        circuit = object_id if kind is ElementKind.circuit else None
        if kind is ElementKind.appliance:
            circuit = index.circuit_of_appliance(object_id)
        elif kind is ElementKind.socket_group:
            circuit = index.element(object_id).model.circuit_id
        if circuit:
            out["circuit"] = tb._named([circuit])[0]
            out["rated_current_a"] = index.rated_current_a(circuit)
            out["protected_by"] = tb._named(index.protection_chain(circuit))
            out["appliances_on_circuit"] = tb._named(index.appliances_on_circuit(circuit))
            out["serves_rooms"] = tb._named(index.rooms_of(circuit))
            out["board"] = tb._named(["db_main"])[0] if index.exists("db_main") else None
    elif kind is ElementKind.fixture:
        out["water_source"] = index.supply_source(object_id)
        out["supply_path"] = tb._named(index.upstream_path(object_id))
        valves = [v for v in index.ids(ElementKind.valve) if object_id in index.isolated_by(v)]
        out["isolating_valves"] = tb._named(valves)
    elif kind in (ElementKind.rccb, ElementKind.mcb):
        out["circuits"] = tb._named(index.circuits_under(object_id))
    elif kind is ElementKind.room:
        out["contains"] = await _find_elements(tb, {"room_id": object_id})
    return out


async def _house_record(tb: ToolBox, args: dict[str, Any]) -> Any:
    return get_house_record(tb.service.index, list(args["object_ids"]))


async def _query_readings(tb: ToolBox, args: dict[str, Any]) -> Any:
    template, needs = READING_KINDS[str(args["kind"])]
    stream = template
    if needs is not None:
        object_id = args.get("object_id")
        if not object_id:
            raise ValueError(f"{args['kind']} needs object_id (a {needs})")
        (object_id,) = validate_ids(tb.service.index, [str(object_id)])
        if tb.service.index.kind_of(object_id).value != needs:
            raise ValueError(f"{object_id} is not a {needs}")
        stream = template.format(id=object_id)
    hours = int(args["hours"])
    end = datetime.fromisoformat(tb.now)
    start = end - timedelta(hours=hours)
    bucket = float(math.ceil(hours * 3600 / MAX_POINTS))
    doc = await tb.service.upstream.readings_summary(stream, start.isoformat(), tb.now, bucket)
    points = [
        {"t": p["t"], "mean": round(float(p["mean"]), 3), "max": round(float(p["max"]), 3)}
        for p in doc.get("points", [])
        if p.get("mean") is not None
    ]
    return {"what": args["kind"], "unit": doc.get("unit"), "points": points}


async def _estimate_cost(tb: ToolBox, args: dict[str, Any]) -> Any:
    case = tb._case(str(args["case_id"]))
    found = tb.costs.get(case["id"])
    if isinstance(found, CostEstimate):
        return found.as_dict()
    return {"cost": None, "reason": found or case["impact"].get("how") or "no billable quantity"}


async def _get_tariff(tb: ToolBox, args: dict[str, Any]) -> Any:
    return get_tariff(str(args["kind"]), datetime.fromisoformat(tb.now).date())


async def _safety(tb: ToolBox, args: dict[str, Any]) -> Any:
    return get_safety_text(str(args["key"]))


async def _vendors(tb: ToolBox, args: dict[str, Any]) -> Any:
    return get_vendors(str(args["category"]), tb.now)


def _day(ms: int) -> str:
    return f"{datetime.fromtimestamp(ms / 1000, tz=HOUSE_TZ):%a %d %b %Y}"


def _when(ms: int) -> str:
    t = datetime.fromtimestamp(ms / 1000, tz=HOUSE_TZ)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}, {t:%a %d %b}"


async def _notices(tb: ToolBox, _: dict[str, Any]) -> Any:
    consent = await tb.service._consent_map()
    if not consent.get("L5", True):
        return {"notices": [], "note": "The owner has switched management notices off."}
    notices = await tb.service.notices(tb.cases, tb.now)
    return {
        "notices": [
            {
                "title": n["title"],
                "from": n["issuer"],
                "area": n["area"],
                "starts": _when(n["starts_at"]),
                "ends": _when(n["ends_at"]),
                "supply_back_by": _when(n["expected_recovery"]) if n["expected_recovery"] else None,
                "details": n["body"],
                "for_this_home": n.get("impact"),
            }
            for n in notices
        ]
    }


async def _maintenance(tb: ToolBox, _: dict[str, Any]) -> Any:
    consent = await tb.service._consent_map()
    if not consent.get("L4", True):
        return {"items": [], "note": "The owner has switched the maintenance schedule off."}
    return {
        "items": [
            {
                "task": m["title"],
                "due": _day(m["due"]),
                "overdue": m["overdue"],
                "how_often": m["interval"],
                "source": m["source"],
                "confidence": m["confidence"],
                **({"other_advice": m["conflict"]} if m["conflict"] else {}),
            }
            for m in tb.service.maintenance(tb.now)[:12]
        ]
    }


async def _focus(tb: ToolBox, args: dict[str, Any]) -> Any:
    ids = list(validate_ids(tb.service.index, list(args["object_ids"])))
    label = str(args["label"]).strip()[:60]
    for lead in ("show me", "show"):  # the button already says "Show me:"
        if label.lower().startswith(lead + " ") or label.lower().startswith(lead + ":"):
            label = label[len(lead) :].lstrip(" :")
            break
    focus = {"layer": args["layer"], "object_ids": ids, "severity": "info", "label": label}
    button = f"Show me: {label}"
    tb.chips.append({"label": button, "focus": focus})
    return {"ok": True, "button": button}


async def _open_case(tb: ToolBox, args: dict[str, Any]) -> Any:
    case = tb._case(str(args["case_id"]))
    label = str(args.get("label") or case["title"])[:60]
    tb.chips.append({"label": label, "to": f"/cases/{case['id']}"})
    return {"ok": True, "button": label}


HANDLERS: dict[str, Callable[[ToolBox, dict[str, Any]], Any]] = {
    "list_cases": _list_cases,
    "get_case": _get_case,
    "find_elements": _find_elements,
    "trace_supply": _trace_supply,
    "get_house_record": _house_record,
    "query_readings": _query_readings,
    "estimate_cost": _estimate_cost,
    "get_tariff": _get_tariff,
    "get_safety_text": _safety,
    "get_vendors": _vendors,
    "get_notices": _notices,
    "get_maintenance": _maintenance,
    "focus_3d": _focus,
    "open_case": _open_case,
}


def case_facts(case: Mapping[str, Any]) -> dict[str, Any]:
    """The facts of one rendered case the LLM may use: no contract IDs, no raw readings."""
    return {
        "case_id": case["id"],
        "status": case["status"],
        "severity": case["severity"],
        "layer": case["layer"],
        "title": case["title"],
        "where": case["location"],
        "what_we_know": case["summary"],
        "confidence": case["confidence"],
        "how_sure": case["confidence_note"],
        "impact": case["impact"],
        "safety": case.get("safety", []),
        "actions": [
            {"label": a["label"], **({"detail": a["detail"]} if a.get("detail") else {})}
            for a in case["actions"]
        ],
        "evidence": {
            "title": case["evidence"]["title"],
            "stats": case["evidence"]["stats"],
            "explanation": case["evidence"]["explanation"],
        },
        "timeline": [e["text"] for e in case["timeline"]],
        "show_in_3d": case["focus"].get("label", ""),
    }
