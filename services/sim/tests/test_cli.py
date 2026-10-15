from __future__ import annotations

import json
from pathlib import Path

import pytest

from penjaga_contract.lab import FaultSpec
from penjaga_sim.cli import main
from penjaga_sim.testing import manifest


def test_catalogue(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["catalogue"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert len(entries) == 15


def test_random_fault_is_a_valid_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["random-fault", "U1", "--start", "2026-10-16T09:00:00+08:00", "--seed", "4"]) == 0
    spec = FaultSpec.model_validate_json(capsys.readouterr().out)
    assert spec.fault_type.value == "U1"
    assert spec.duration_s is not None


def test_episode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "m.json"
    path.write_text(manifest(duration_days=0.05).model_dump_json(), encoding="utf-8")
    assert main(["episode", str(path), str(tmp_path / "ep")]) == 0
    assert json.loads(capsys.readouterr().out)["days"] == 1
    assert main(["episode", str(path), str(tmp_path / "ep")]) == 1  # exists
    path.write_text("{}", encoding="utf-8")
    assert main(["episode", str(path), str(tmp_path / "other")]) == 2
