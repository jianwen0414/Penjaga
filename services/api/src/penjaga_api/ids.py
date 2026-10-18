"""Contract ID validation: the only door through which an object ID reaches a case, a focus
command or an agent reply (PRD §10.1 🔒: the backend validates every ID the agent emits)."""

from __future__ import annotations

from collections.abc import Iterable

from penjaga_contract.house import HouseIndex


class UnknownIdError(ValueError):
    """One or more IDs are not elements of the house contract."""

    def __init__(self, unknown: list[str]) -> None:
        super().__init__(f"not in the house contract: {', '.join(unknown)}")
        self.unknown = unknown


def validate_ids(index: HouseIndex, ids: Iterable[str]) -> tuple[str, ...]:
    """``ids`` in order without duplicates, if every one exists in the contract."""
    seen: dict[str, None] = {}
    unknown: list[str] = []
    for object_id in ids:
        if not isinstance(object_id, str) or not index.exists(object_id):
            unknown.append(str(object_id))
        else:
            seen.setdefault(object_id, None)
    if unknown:
        raise UnknownIdError(unknown)
    return tuple(seen)
