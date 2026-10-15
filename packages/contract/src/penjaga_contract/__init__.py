"""Penjaga House Data Contract (M0).

The JSON Schemas under ``schemas/`` and the data files under ``data/`` are the single source
of truth. ``penjaga_contract.core.models`` and ``penjaga_contract.lab.models`` are generated
from the schemas (see ``tools/generate.py``) and must never be edited by hand.

``penjaga_contract.lab`` holds simulator/evaluation-only types (fault specs, ground truth,
episode manifests). Detection code must never import it; import-linter enforces this.
"""

CONTRACT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0"
