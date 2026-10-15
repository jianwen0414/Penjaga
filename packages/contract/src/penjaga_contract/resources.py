"""Locations of the contract's canonical files (schemas and data) inside the package."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

_PACKAGE_ROOT = Path(str(files("penjaga_contract")))

SCHEMAS_DIR = _PACKAGE_ROOT / "schemas"
DATA_DIR = _PACKAGE_ROOT / "data"
HOUSE_PATH = DATA_DIR / "house.json"
STREAMS_PATH = DATA_DIR / "streams.json"

NAMESPACES = ("core", "lab")


def schema_paths() -> list[Path]:
    """All contract schema files, sorted for deterministic iteration."""
    return sorted(SCHEMAS_DIR.glob("*/*.schema.json"))


def schema_path(namespace: str, name: str) -> Path:
    """Path of ``<namespace>/<name>.schema.json``; raises if it does not exist."""
    if namespace not in NAMESPACES:
        raise ValueError(f"unknown schema namespace {namespace!r}")
    path = SCHEMAS_DIR / namespace / f"{name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def read_json(path: Path) -> Any:
    """Read a UTF-8 JSON file (explicit encoding: Windows defaults are not UTF-8)."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_house_document() -> dict[str, Any]:
    """The canonical house record as a plain JSON document."""
    doc = read_json(HOUSE_PATH)
    if not isinstance(doc, dict):
        raise TypeError("house.json must contain a JSON object")
    return doc


def load_streams_document() -> dict[str, Any]:
    """The committed stream catalogue as a plain JSON document."""
    doc = read_json(STREAMS_PATH)
    if not isinstance(doc, dict):
        raise TypeError("streams.json must contain a JSON object")
    return doc


def dump_json(doc: Any) -> str:
    """Canonical JSON text used for every file this package writes (2-space indent, LF)."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
