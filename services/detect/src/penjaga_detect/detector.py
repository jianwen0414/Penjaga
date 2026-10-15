"""The interface every detector implements, for both the live service and the evaluation harness.

A detector is fed chunks of house time in order and returns the Finding revisions they produce.
Its decisions must be stamped at house times that do not depend on how time was chunked (for
example "end of the night window"), so live and replayed runs agree (proposal §3.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo

from penjaga_contract.core import Finding
from penjaga_contract.house import HouseIndex
from penjaga_contract.validate import load_context
from penjaga_detect.chunk import Chunk


@dataclass(frozen=True)
class DetectorInfo:
    name: str  # snake_case; Finding.source.detector
    layer: str  # L1, L2, L3 or L5
    kind: str  # rule, model or baseline
    version: str  # semver of the detector's logic and parameters
    model: str | None = None

    def source(self) -> dict[str, str]:
        out = {"detector": self.name, "kind": self.kind, "version": self.version}
        if self.model is not None:
            out["model"] = self.model
        return out


@dataclass(frozen=True)
class HouseContext:
    """Everything a detector may know about the house: the contract, not the simulator."""

    index: HouseIndex
    zone: ZoneInfo

    @property
    def house_id(self) -> str:
        return self.index.house_id

    @staticmethod
    def canonical() -> HouseContext:
        ctx = load_context()
        return HouseContext(ctx.index(), ZoneInfo(ctx.house.location.timezone))


class Detector(Protocol):
    info: DetectorInfo

    def update(self, chunk: Chunk) -> list[Finding]:
        """Findings (new revisions) produced by the house time in ``chunk``."""
        ...


DetectorFactory = Callable[[HouseContext], Detector]
