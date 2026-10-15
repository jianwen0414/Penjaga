"""Derived artifacts are fresh, public facades are complete, and import rules hold."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from penjaga_contract import core, lab
from penjaga_contract.cli import main as cli_main

CONTRACT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CONTRACT_DIR.parents[1]
GENERATE = CONTRACT_DIR / "tools" / "generate.py"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )


def test_python_types_and_catalogue_are_up_to_date():
    result = _run([sys.executable, str(GENERATE), "--check", "--skip-ts"], REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    shutil.which("npx") is None and shutil.which("npx.cmd") is None, reason="Node.js not installed"
)
def test_typescript_types_are_up_to_date():
    result = _run([sys.executable, str(GENERATE), "--check"], REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_facades_export_every_generated_top_level_model():
    for name in ["House", "StreamCatalogue", "Reading", "ReadingBatch", "Event", "Finding"]:
        assert name in core.__all__
    for name in ["FaultSpec", "TruthRecord", "EpisodeManifest"]:
        assert name in lab.__all__


def test_import_rules():
    lint_imports = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    assert lint_imports is not None, "import-linter is a dev dependency"
    result = _run([lint_imports], REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_validate_passes(capsys):
    assert cli_main(["validate"]) == 0
    assert "contract valid" in capsys.readouterr().out
