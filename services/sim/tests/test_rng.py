from __future__ import annotations

import subprocess
import sys
from typing import Any

import numpy as np
import pytest

from penjaga_sim.rng import rng, stable_key


def test_same_keys_same_stream() -> None:
    assert np.array_equal(rng(3, "day", 5, "x").random(10), rng(3, "day", 5, "x").random(10))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ((3, "day", 5), (3, "day", 6)),
        ((3, "a"), (4, "a")),
        ((3, "a", "b"), (3, "b", "a")),
        ((3,), (3, "a")),
    ],
)
def test_different_keys_independent_streams(a: tuple[Any, ...], b: tuple[Any, ...]) -> None:
    assert not np.array_equal(rng(*a).random(10), rng(*b).random(10))


def test_keys_are_stable_across_processes() -> None:
    """hash() is salted per process; stable_key must not be."""
    code = "from penjaga_sim.rng import stable_key; print(stable_key('household'))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert int(out.stdout) == stable_key("household")


def test_negative_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="seed"):
        rng(-1)
    with pytest.raises(ValueError, match="non-negative"):
        stable_key(-5)
