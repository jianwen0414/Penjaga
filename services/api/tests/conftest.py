"""Fixtures for the Core API tests; the fakes live in ``penjaga_api.testing``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from penjaga_api.store import ApiStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ApiStore]:
    s = ApiStore(tmp_path / "api.sqlite")
    yield s
    s.close()
