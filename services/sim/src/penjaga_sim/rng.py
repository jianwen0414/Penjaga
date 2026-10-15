"""Deterministic, independent random streams.

Every stochastic component draws from its own generator keyed by (seed, *keys), e.g.
(seed, "day", date_ordinal, "household"). Streams never share state, so

* generation does not depend on how time is chunked,
* injecting a fault (which draws from its own stream) never perturbs household behaviour,
  sensor noise or anything else, which is what makes the null-fault canary possible.
"""

from __future__ import annotations

import hashlib

import numpy as np


def stable_key(key: str | int) -> int:
    """Stable 64-bit integer for a key (Python's hash() is salted per process)."""
    if isinstance(key, int):
        if key < 0:
            raise ValueError("integer keys must be non-negative")
        return key
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little")


def rng(seed: int, *keys: str | int) -> np.random.Generator:
    """Independent generator for (seed, *keys)."""
    if seed < 0:
        raise ValueError("seed must be non-negative")
    sequence = np.random.SeedSequence(entropy=seed, spawn_key=tuple(stable_key(k) for k in keys))
    return np.random.Generator(np.random.PCG64(sequence))
