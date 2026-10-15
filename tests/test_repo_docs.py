"""Repository-level documentation stays in step with the code (PRD §0.4, §8.2, §25)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _workspace_pyprojects() -> list[Path]:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = root["tool"]["uv"]["workspace"]["members"]
    return [ROOT / "pyproject.toml", *(ROOT / m / "pyproject.toml" for m in members)]


def _direct_requirements() -> set[NormalizedName]:
    names: set[NormalizedName] = set()
    workspace_names: set[NormalizedName] = set()
    for path in _workspace_pyprojects():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        workspace_names.add(canonicalize_name(data["project"]["name"]))
        specs = list(data["project"].get("dependencies", []))
        for group in data.get("dependency-groups", {}).values():
            specs += [s for s in group if isinstance(s, str)]
        names |= {canonicalize_name(Requirement(s).name) for s in specs}
    return names - workspace_names


def _locked_versions() -> dict[NormalizedName, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {canonicalize_name(p["name"]): p["version"] for p in lock["package"] if "version" in p}


def test_every_direct_python_dependency_is_documented_with_its_locked_version():
    doc = (DOCS / "DEPENDENCIES.md").read_text(encoding="utf-8")
    locked = _locked_versions()
    rows = {
        canonicalize_name(m.group(1)): m.group(2)
        for m in re.finditer(r"^\|\s*([A-Za-z0-9_.\-]+)\s*\|\s*([0-9][^\s|]*)\s*\|", doc, re.M)
    }
    missing = []
    for name in sorted(_direct_requirements()):
        if name not in rows:
            missing.append(f"{name}: not in DEPENDENCIES.md")
        elif rows[name] != locked[name]:
            missing.append(f"{name}: documented {rows[name]}, locked {locked[name]}")
    assert not missing, "\n".join(missing)


def test_every_cited_data_source_exists():
    sources = (DOCS / "DATA_SOURCES.md").read_text(encoding="utf-8")
    defined = set(re.findall(r"^## (DS-\d{3})\b", sources, re.M))
    cited: set[str] = set()
    for path in (ROOT / "packages").rglob("*.json"):
        if "node_modules" in path.parts:
            continue
        cited |= set(re.findall(r"\bDS-\d{3}\b", path.read_text(encoding="utf-8")))
    undefined = sorted(cited - defined)
    assert not undefined, f"cited but not defined in DATA_SOURCES.md: {undefined}"


def test_required_documents_exist():
    # PRD §25: required documents (EVALUATION.md, PRIVACY.md and model cards arrive with
    # their modules; this list grows as they land).
    for name in ["PRD.md", "DECISIONS.md", "DEPENDENCIES.md", "DATA_SOURCES.md"]:
        assert (DOCS / name).is_file(), name
