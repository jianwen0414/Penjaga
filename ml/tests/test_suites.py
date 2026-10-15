from __future__ import annotations

from pathlib import Path

import pytest

from penjaga_contract import schema_registry
from penjaga_eval.sealing import SealError, check, seal
from penjaga_eval.suites import SuiteError, load_suite, manifests, manifests_digest
from penjaga_sim.export import manifest_document


def test_the_shipped_suite_loads_and_is_deterministic() -> None:
    suite = load_suite("l1_v0")
    a = manifests(suite, "validation")
    b = manifests(suite, "validation")
    assert a == b
    assert manifests_digest(a) == manifests_digest(b)
    assert len(a) == 5 * 8 + 8
    for m in a:
        schema_registry.validate("lab", "episode_manifest", manifest_document(m))


def test_splits_are_disjoint() -> None:
    suite = load_suite("l1_v0")
    by_split = {split: manifests(suite, split) for split in ("train", "validation", "test")}
    seeds = [{m.seed for m in ms} for ms in by_split.values()]
    for i, a in enumerate(seeds):
        for b in seeds[i + 1 :]:
            assert not a & b
    years = {split: {m.start.year for m in ms} for split, ms in by_split.items()}
    assert years == {"train": {2023}, "validation": {2024}, "test": {2025}}
    held_out = {m.household_archetype for m in by_split["test"]} - {
        m.household_archetype for m in by_split["train"]
    }
    assert held_out == {"shift_family"}
    for ms in by_split.values():
        for m in ms:
            for f in m.faults:
                assert f.start_at is not None
                assert m.start < f.start_at  # inside the episode, after its start
            assert len({m.episode_id for m in ms}) == len(ms)


def test_bad_suites_are_rejected(tmp_path: Path, suites_dir: Path) -> None:
    text = (suites_dir / "tiny.toml").read_text(encoding="utf-8")
    (tmp_path / "overlap.toml").write_text(
        text.replace('name = "tiny"', 'name = "overlap"').replace(
            "years = [2024]", "years = [2023]"
        ),
        encoding="utf-8",
    )
    with pytest.raises(SuiteError, match="share weather years"):
        load_suite("overlap", tmp_path)
    with pytest.raises(SuiteError, match="no suite"):
        load_suite("missing", tmp_path)


def test_sealing(tmp_path: Path, tiny: object) -> None:
    from penjaga_eval.suites import Suite

    assert isinstance(tiny, Suite)
    with pytest.raises(SealError, match="not sealed"):
        check(tiny, manifests(tiny, "test"), tmp_path)
    record = seal(tiny, tmp_path)
    assert record["episodes"] == 3
    check(tiny, manifests(tiny, "test"), tmp_path)
    assert seal(tiny, tmp_path) == record  # idempotent
    with pytest.raises(SealError, match="differ"):
        check(tiny, manifests(tiny, "test")[:2], tmp_path)
    (tmp_path / "tiny.json").write_text(
        (tmp_path / "tiny.json").read_text().replace(record["digest"], "0" * 64)
    )
    with pytest.raises(SealError, match="changed since they were sealed"):
        seal(tiny, tmp_path)
    assert seal(tiny, tmp_path, reseal=True)["digest"] == record["digest"]
