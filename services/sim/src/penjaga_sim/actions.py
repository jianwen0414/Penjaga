"""Household actions during a guided check: closing a valve, holding off water use.

A guided check (PRD §18.2) asks the household to do something and measures the effect in the
readings: "please don't use any water for the next 25 minutes; now close the valve under the
Bathroom 2 cistern". The simulator applies such actions to the physics so the evaluation can
measure what a check would really show.

Valve semantics follow ``HouseIndex.isolated_by`` (docs/design/contract.md): an angle valve
stops its own fixture; any other valve stops its pipe and what is downstream of it, but never
across the roof tank, because stored water keeps flowing. A valve that cuts the supply to the
tank (the main stop valve) stops all flow through the meter while the network pressure behind
it remains. A leak on a valve's own pipe counts as downstream of it (the valve sits where its
branch starts). The float valve and the roof-space gate valve are not owner-operable; the float
valve cannot be closed at all.

Holding off water use removes the household's planned uses in the window, including showers'
water heater runs. Automatic flows continue: leaks, the purifier refilling its storage, and the
roof tank refilling through its float valve. Drinking from the purifier's storage also
continues, since it is not using a tap.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from penjaga_contract.core import Valve, ValveKind
from penjaga_contract.house import ElementKind, HouseIndex
from penjaga_sim.faults import DayEffects
from penjaga_sim.household import DayPlan, N, WaterUse

US = 1_000_000
Mask = npt.NDArray[np.bool_]


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class CheckAction:
    """From ``start_us`` until ``end_us`` (exclusive): the valves are closed and, if
    ``hold_water_use``, nobody uses water."""

    start_us: int
    end_us: int
    close_valves: tuple[str, ...] = ()
    hold_water_use: bool = True


@dataclass(frozen=True)
class Isolation:
    elements: frozenset[str]  # fixtures and pipes that stop receiving water
    stops_inlet: bool  # no water passes the main: the tank stops refilling


def isolation(index: HouseIndex, valve_id: str) -> Isolation:
    valve: Valve = index.get(valve_id, Valve)
    if valve.kind is ValveKind.float:
        raise ActionError(f"{valve_id} is a float valve: it cannot be closed by hand")
    if valve.kind is ValveKind.angle:
        return Isolation(frozenset(index.isolated_by(valve_id)), False)
    nodes = index.network_downstream(valve.pipe_id, through_tanks=False)
    pipes = {n for n in nodes if index.kind_of(n) is not ElementKind.tank}
    fixtures = set(index.fixtures_fed_by(valve.pipe_id, through_tanks=False))
    return Isolation(frozenset(pipes | fixtures), index.stops_tank_refill(valve_id))


def _seconds(action: CheckAction, day_start_us: int) -> slice | None:
    """The part of ``action`` inside this day, as a slice of its seconds."""
    a = max(0, -(-(action.start_us - day_start_us) // US))  # first whole second at or after
    b = min(N, -(-(action.end_us - day_start_us) // US))
    return slice(a, b) if b > a else None


@dataclass(frozen=True)
class DayActions:
    """The actions in force on one day, per second."""

    hold: Mask
    inlet_closed: Mask
    closed: dict[str, Mask]  # element -> seconds during which it is isolated

    def blocked(self, element: str) -> Mask | None:
        return self.closed.get(element)


def day_actions(
    index: HouseIndex, actions: list[CheckAction], day_start_us: int
) -> DayActions | None:
    hold = np.zeros(N, np.bool_)
    inlet = np.zeros(N, np.bool_)
    closed: dict[str, Mask] = {}
    touched = False
    for action in actions:
        sl = _seconds(action, day_start_us)
        if sl is None:
            continue
        touched = True
        if action.hold_water_use:
            hold[sl] = True
        for valve in action.close_valves:
            iso = isolation(index, valve)
            if iso.stops_inlet:
                inlet[sl] = True
            for element in iso.elements:
                closed.setdefault(element, np.zeros(N, np.bool_))[sl] = True
    return DayActions(hold, inlet, closed) if touched else None


def _segments(mask: Mask) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


def restrict_plan(plan: DayPlan, acts: DayActions) -> DayPlan:
    """The plan without the water uses the actions prevent (each use keeps its allowed parts)."""
    kept: list[WaterUse] = []
    for use in plan.water:
        allowed = np.zeros(N, np.bool_)
        allowed[use.start : use.start + use.dur] = True
        allowed &= ~acts.hold
        blocked = acts.blocked(use.fixture)
        if blocked is not None:
            allowed &= ~blocked
        for a, b in _segments(allowed):
            kept.append(dataclasses.replace(use, start=a, dur=b - a))
    return dataclasses.replace(plan, water=kept)


def isolate_leaks(effects: DayEffects, acts: DayActions) -> None:
    """Stop the leaks that a closed valve isolates (in place)."""
    for location, tank_side, lps in effects.water_leaks:
        blocked = acts.blocked(location)
        if blocked is None:
            continue
        target = effects.tank_leak_lps if tank_side else effects.main_leak_lps
        target -= np.where(blocked, lps, 0.0)
        np.maximum(target, 0.0, out=target)  # guard against rounding below zero
