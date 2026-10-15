from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from penjaga_contract.validate import ContractContext, load_context
from penjaga_sim.faults import catalogue
from penjaga_sim.testing import START, new_world
from penjaga_sim.world import World


@pytest.fixture(scope="session")
def ctx() -> ContractContext:
    return load_context()


@pytest.fixture(scope="session")
def baseline() -> World:
    """Fault-free reference world with its first two days generated. Do not mutate it."""
    world = new_world()
    world.output(START)
    world.output(START + timedelta(days=1))
    return world


@pytest.fixture(scope="session")
def location() -> Callable[[str], str]:
    """First valid location for a fault type."""
    entries = {e["fault_type"]: e for e in catalogue(new_world().model)}

    def first(fault_type: str) -> str:
        return str(entries[fault_type]["locations"][0])

    return first


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
