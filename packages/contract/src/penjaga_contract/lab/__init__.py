"""LAB NAMESPACE: simulator and evaluation types (fault specs, ground truth, episodes).

Detection code (``penjaga_detect``) must never import this package or
``penjaga_contract.generated.lab``: ground truth stays on the simulator/evaluation side of the
wall (PRD §8.1). import-linter enforces the rule in CI.
"""

from penjaga_contract.generated.lab.episode_manifest_schema import (
    AnomalySpec,
    EpisodeManifest,
    Split,
)
from penjaga_contract.generated.lab.fault_spec_schema import (
    FaultMagnitude,
    FaultSpec,
    FaultType,
    Progression,
    ProgressionKind,
)
from penjaga_contract.generated.lab.sim_api_schema import (
    AnomalyCreated,
    CatalogueEntry,
    FaultState,
    FaultView,
    RandomFaultRequest,
    ResetRequest,
    ResetResponse,
    SimHealth,
    SimStatus,
)
from penjaga_contract.generated.lab.truth_record_schema import AnomalyType, TruthKind, TruthRecord

__all__ = [
    "AnomalyCreated",
    "AnomalySpec",
    "AnomalyType",
    "CatalogueEntry",
    "EpisodeManifest",
    "FaultMagnitude",
    "FaultSpec",
    "FaultState",
    "FaultType",
    "FaultView",
    "Progression",
    "ProgressionKind",
    "RandomFaultRequest",
    "ResetRequest",
    "ResetResponse",
    "SimHealth",
    "SimStatus",
    "Split",
    "TruthKind",
    "TruthRecord",
]
