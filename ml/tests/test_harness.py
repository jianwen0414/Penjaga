"""End to end on a tiny suite: episodes, replay (with the leakage canary), runs, sealing, tuning."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from penjaga_detect.baselines.mnf import MnfBaseline
from penjaga_detect.detector import HouseContext
from penjaga_eval import episodes, registry
from penjaga_eval.markdown import render
from penjaga_eval.replay import replay
from penjaga_eval.run import evaluate
from penjaga_eval.sealing import SealError, seal
from penjaga_eval.suites import Suite, manifests
from penjaga_eval.tune import tune


@pytest.fixture(scope="module")
def validation(tiny: Suite, episodes_root: Path) -> list[Path]:
    return episodes.ensure(manifests(tiny, "validation"), episodes_root, workers=1)


def test_episodes_are_cached(tiny: Suite, episodes_root: Path, validation: list[Path]) -> None:
    stamp = [(p / "observed" / "readings.parquet").stat().st_mtime_ns for p in validation]
    again = episodes.ensure(manifests(tiny, "validation"), episodes_root, workers=1)
    assert again == validation
    assert [(p / "observed" / "readings.parquet").stat().st_mtime_ns for p in again] == stamp


def test_replay_never_needs_the_truth(validation: list[Path], tmp_path: Path) -> None:
    """Leakage canary: the same findings with truth/ and the manifest deleted."""
    house = HouseContext.canonical()
    for path in validation:
        blind = tmp_path / path.name
        shutil.copytree(path / "observed", blind / "observed")
        with_truth = replay(path, [MnfBaseline(house)])
        without = replay(blind, [MnfBaseline(house)])
        assert with_truth == without
        assert not (blind / "truth").exists()


def test_replay_does_not_depend_on_chunk_size(validation: list[Path]) -> None:
    house = HouseContext.canonical()
    for path in validation:
        assert replay(path, [MnfBaseline(house)], 24) == replay(path, [MnfBaseline(house)], 7.5)


def test_validation_run(tiny: Suite, episodes_root: Path) -> None:
    report = evaluate(
        tiny, "validation", ["l1_mnf_baseline"], episodes_root=episodes_root, workers=1
    )
    m = report["detectors"]["l1_mnf_baseline"]["metrics"]["L1"]
    assert m["faults"] == 2
    assert set(m["by_fault_type"]) == {"W2", "W5"}
    assert m["house_months"] == pytest.approx(3 * 3 / 30.4375, abs=1e-3)  # reported to 3 dp
    assert m["by_fault_type"]["W5"]["recall"] == 1.0  # a burst cannot be missed at night
    text = render(report)
    assert "| W5 |" in text
    assert json.loads(json.dumps(report)) == report


def test_parallel_replay_gives_the_same_report(tiny: Suite, episodes_root: Path) -> None:
    serial = evaluate(
        tiny, "validation", ["l1_mnf_baseline"], episodes_root=episodes_root, workers=1
    )
    parallel = evaluate(
        tiny, "validation", ["l1_mnf_baseline"], episodes_root=episodes_root, workers=2
    )
    assert parallel["detectors"] == serial["detectors"]


def test_test_runs_need_a_seal_and_are_logged(
    tiny: Suite, episodes_root: Path, tmp_path: Path
) -> None:
    log = tmp_path / "test-log.jsonl"
    with pytest.raises(SealError):
        evaluate(tiny, "test", ["l1_mnf_baseline"], episodes_root=episodes_root,
                 sealed_dir=tmp_path, test_log=log, workers=1)  # fmt: skip
    seal(tiny, tmp_path)
    with pytest.raises(ValueError, match="whole sealed split"):
        evaluate(tiny, "test", ["l1_mnf_baseline"], episodes_root=episodes_root,
                 sealed_dir=tmp_path, test_log=log, limit=1)  # fmt: skip
    evaluate(tiny, "test", ["l1_mnf_baseline"], episodes_root=episodes_root,
             sealed_dir=tmp_path, test_log=log, workers=1)  # fmt: skip
    (entry,) = [json.loads(line) for line in log.read_text().splitlines()]
    assert entry["suite"] == "tiny"
    assert "L1" in entry["detectors"]["l1_mnf_baseline"]["headline"]


def test_tuning_uses_validation_and_records_its_choice(
    tiny: Suite, episodes_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tune(
        tiny,
        "l1_mnf_baseline",
        {"threshold_lpm": [0.05, 50.0]},
        budget_fa_per_house_month=1.0,
        episodes_root=episodes_root,
        params_dir=tmp_path,
        workers=1,
    )
    assert record["tuned_on"]["split"] == "validation"
    assert record["params"] == {"threshold_lpm": 0.05}  # 50 L/min detects nothing
    monkeypatch.setattr(registry, "PARAMS_DIR", tmp_path)
    assert registry.tuned("l1_mnf_baseline", tmp_path) == {"threshold_lpm": 0.05}
    with pytest.raises(KeyError, match="unknown parameters"):
        registry.parameters("l1_mnf_baseline", {"nope": 1})


def test_tuning_scores_each_setting_as_a_separate_run_would(
    tiny: Suite, episodes_root: Path, tmp_path: Path
) -> None:
    grid = {"threshold_lpm": [0.02, 0.3], "nights": [1, 2]}
    record = tune(
        tiny,
        "l1_mnf_baseline",
        grid,
        budget_fa_per_house_month=1.0,
        episodes_root=episodes_root,
        params_dir=tmp_path,
        workers=2,
    )
    for candidate in record["candidates"]:
        report = evaluate(
            tiny,
            "validation",
            ["l1_mnf_baseline"],
            overrides={"l1_mnf_baseline": candidate["params"]},
            episodes_root=episodes_root,
            workers=1,
        )
        m = report["detectors"]["l1_mnf_baseline"]["metrics"]["L1"]
        for key in ("recall", "precision", "false_alarms_per_house_month"):
            assert candidate[key] == pytest.approx(m[key]), (candidate["params"], key)


def test_l1_training_records_provenance(tiny: Suite, episodes_root: Path, tmp_path: Path) -> None:
    from penjaga_eval.train_l1 import train

    record = train(
        tiny,
        root=episodes_root,
        artifact=tmp_path / "model.pkl",
        card_data=tmp_path / "card.json",
        workers=1,
        folds=2,
    )
    assert record["trained_on"]["split"] == "train"
    assert set(record["trained_on"]["labels"]) <= {"none", "W2", "W5"}
    assert (tmp_path / "model.pkl").exists()
    params = registry.parameters("l1_leak_model", {"artifact": str(tmp_path / "model.pkl")})
    detector = registry.build("l1_leak_model", HouseContext.canonical(), params)
    assert detector.info.kind == "model"


def test_generation_refuses_a_nearly_full_disk(tmp_path: Path) -> None:
    with pytest.raises(episodes.DiskSpaceError, match="GB free"):
        episodes.check_free_space(tmp_path, minimum=2**62)
    episodes.check_free_space(tmp_path, minimum=0)


def test_cache_follows_the_stream_filter(tiny: Suite, tmp_path: Path) -> None:
    items = manifests(tiny, "validation")[:1]
    (path,) = episodes.ensure(items, tmp_path, workers=1, streams=["water.*"])
    first = (path / "observed" / "meta.json").stat().st_mtime_ns
    episodes.ensure(items, tmp_path, workers=1, streams=["water.*"])
    assert (path / "observed" / "meta.json").stat().st_mtime_ns == first  # reused
    episodes.ensure(items, tmp_path, workers=1, streams=["water.main.*"])
    meta = json.loads((path / "observed" / "meta.json").read_text())
    assert meta["streams"] == ["water.main.flow_lpm", "water.main.pressure_bar"]  # regenerated
