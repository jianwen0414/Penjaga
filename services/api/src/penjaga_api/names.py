"""Plain-English names for house elements, from the contract's own ``name`` fields.

Case text never shows a raw object ID (PRD §6.2 "specific", §18.5 plain English). Names come
from the house record; only the technical device names are reworded for an owner ("Main RCCB
100 mA" → "main safety switch (RCCB)").
"""

from __future__ import annotations

from penjaga_contract.house import ElementKind, HouseIndex

PLAIN_KIND = {ElementKind.board: "distribution board", ElementKind.meter: "water meter"}


class Names:
    def __init__(self, index: HouseIndex) -> None:
        self.index = index

    def raw(self, object_id: str) -> str:
        return str(self.index.element(object_id).model.name)

    def room(self, room_id: str) -> str:
        return self.raw(room_id)

    def of(self, object_id: str) -> str:
        """What an owner would call the element, without an article (e.g. "Bathroom 2 toilet
        cistern", "main safety switch (RCCB)", 'breaker for the "Sockets, Flexi-Suite" circuit').
        Circuit names are quoted, as on the labels in a distribution board."""
        kind = self.index.kind_of(object_id)
        model = self.index.element(object_id).model
        if kind is ElementKind.mcb:
            circuits = self.index.circuits_under(object_id)
            if len(circuits) == 1:
                return f"breaker for the {self.of(circuits[0])} circuit"
            return "circuit breaker"
        if kind is ElementKind.rccb:
            if model.upstream_device_id is None:
                return "main safety switch (RCCB)"
            circuits = self.index.circuits_under(object_id)
            if len(circuits) == 1:
                return f"safety switch (RCCB) for the {self.of(circuits[0])} circuit"
            return f"safety switch (RCCB) for {_rccb_scope(self.raw(object_id))}"
        if kind is ElementKind.circuit:
            return f'"{self.raw(object_id)}"'
        plain = PLAIN_KIND.get(kind)
        if plain is not None:
            return plain
        # "Dry kitchen sink tap (direct from main)" → "Dry kitchen sink tap" in a sentence
        name = self.raw(object_id).split(" (")[0]
        return name.replace("WC cistern", "toilet cistern")

    def where(self, object_id: str) -> str:
        """Where the element is, in words: a room, or what a circuit serves."""
        kind = self.index.kind_of(object_id)
        if kind is ElementKind.circuit:
            return self.raw(object_id)
        if kind in (ElementKind.mcb, ElementKind.rccb, ElementKind.board):
            board = self.index.element(object_id).model
            board_id = object_id if kind is ElementKind.board else board.board_id
            rooms = self.index.rooms_of(board_id)
            return f"Distribution board · {self.room(rooms[0])}" if rooms else "Distribution board"
        rooms = self.index.rooms_of(object_id)
        return self.room(rooms[0]) if rooms else self.of(object_id)

    def listing(self, object_ids: list[str] | tuple[str, ...], *, limit: int = 4) -> str:
        """ "A, B and C" (or "A, B, C and 2 more")."""
        names = [self.of(i) for i in object_ids]
        if len(names) > limit:
            names = [*names[:limit], f"{len(object_ids) - limit} more"]
        if len(names) <= 1:
            return "".join(names)
        return f"{', '.join(names[:-1])} and {names[-1]}"


def _rccb_scope(name: str) -> str:
    """ "Socket-outlet RCCB 30 mA" → "socket-outlet circuits"."""
    head = name.split(" RCCB", maxsplit=1)[0].strip()
    return f"{head[:1].lower()}{head[1:]} circuits" if head else "several circuits"
