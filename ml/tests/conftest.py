from __future__ import annotations

from pathlib import Path

import pytest

from penjaga_eval.suites import Suite, load_suite

TINY = """
name = "tiny"
layers = ["L1"]
days = 3
fault_types = ["W2", "W5"]
fault_start_days = [0.5, 1.0]
anomaly_prob = 0.0
anomaly_types = []
anomaly_days = [1, 1]

[split.train]
years = [2023]
archetypes = ["working_family"]
episodes_per_fault_type = 1
fault_free_episodes = 1

[split.validation]
years = [2024]
archetypes = ["working_family"]
episodes_per_fault_type = 1
fault_free_episodes = 1

[split.test]
years = [2025]
archetypes = ["working_family", "young_couple"]
episodes_per_fault_type = 1
fault_free_episodes = 1
"""


@pytest.fixture(scope="session")
def suites_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("suites")
    (directory / "tiny.toml").write_text(TINY, encoding="utf-8")
    return directory


@pytest.fixture(scope="session")
def tiny(suites_dir: Path) -> Suite:
    return load_suite("tiny", suites_dir)


@pytest.fixture(scope="session")
def episodes_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Shared across tests: episodes are generated once per session."""
    return tmp_path_factory.mktemp("episodes")
