"""Core contract types, safe for every module (simulator, stream, detection, agent).

Re-exports the generated Pydantic models of ``schemas/core``. Never import
``penjaga_contract.lab`` from here.
"""

from penjaga_contract.generated.core.common_schema import (
    Layer,
    Quality,
    Quantity,
    Severity,
    SpecBasis,
    TimeWindow,
)
from penjaga_contract.generated.core.event_schema import (
    Event,
    MainsState,
    ProtectionDeviceState,
    ProtectionState,
    SupplyState,
)
from penjaga_contract.generated.core.finding_schema import (
    Candidate,
    ConfidenceKind,
    DetectionLayer,
    EvidenceItem,
    Finding,
    FindingStatus,
    FindingType,
    SeriesRef,
    Source,
    SourceKind,
)
from penjaga_contract.generated.core.house_schema import (
    Appliance,
    ApplianceKind,
    Board,
    Circuit,
    CircuitKind,
    ElectricalSystem,
    Fixture,
    FixtureKind,
    House,
    Pipe,
    PipeKind,
    PipeRouting,
    ProtectiveDevice,
    ProtectiveDeviceKind,
    Room,
    RoomKind,
    Sensor,
    SensorKind,
    SocketGroup,
    Tank,
    Valve,
    ValveKind,
    Wall,
    WallKind,
    WaterMeter,
    WaterSystem,
)
from penjaga_contract.generated.core.reading_batch_schema import ReadingBatch
from penjaga_contract.generated.core.reading_schema import Reading
from penjaga_contract.generated.core.streams_schema import (
    Aggregation,
    EventPayloadType,
    StreamCatalogue,
    StreamDef,
    StreamKind,
    Unit,
)

SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.info,
    Severity.heads_up,
    Severity.act_soon,
    Severity.urgent,
)
"""Severities from least to most severe; use ``severity_rank`` to compare."""


def severity_rank(severity: Severity | str) -> int:
    """Position of ``severity`` on the ordered scale (info=0 ... urgent=3)."""
    return SEVERITY_ORDER.index(Severity(severity))


__all__ = [
    "SEVERITY_ORDER",
    "Aggregation",
    "Appliance",
    "ApplianceKind",
    "Board",
    "Candidate",
    "Circuit",
    "CircuitKind",
    "ConfidenceKind",
    "DetectionLayer",
    "ElectricalSystem",
    "Event",
    "EventPayloadType",
    "EvidenceItem",
    "Finding",
    "FindingStatus",
    "FindingType",
    "Fixture",
    "FixtureKind",
    "House",
    "Layer",
    "MainsState",
    "Pipe",
    "PipeKind",
    "PipeRouting",
    "ProtectionDeviceState",
    "ProtectionState",
    "ProtectiveDevice",
    "ProtectiveDeviceKind",
    "Quality",
    "Quantity",
    "Reading",
    "ReadingBatch",
    "Room",
    "RoomKind",
    "Sensor",
    "SensorKind",
    "SeriesRef",
    "Severity",
    "SocketGroup",
    "Source",
    "SourceKind",
    "SpecBasis",
    "StreamCatalogue",
    "StreamDef",
    "StreamKind",
    "SupplyState",
    "Tank",
    "TimeWindow",
    "Unit",
    "Valve",
    "ValveKind",
    "Wall",
    "WallKind",
    "WaterMeter",
    "WaterSystem",
    "severity_rank",
]
