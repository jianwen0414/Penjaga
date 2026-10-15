from __future__ import annotations

import copy
from typing import Any

import pytest

from penjaga_contract.house import HouseIndex
from penjaga_contract.resources import load_house_document, load_streams_document
from penjaga_contract.validate import ContractContext, load_context


@pytest.fixture
def house_doc() -> dict[str, Any]:
    """A fresh deep copy of the canonical house.json (safe to mutate)."""
    return copy.deepcopy(load_house_document())


@pytest.fixture
def streams_doc() -> dict[str, Any]:
    return copy.deepcopy(load_streams_document())


@pytest.fixture(scope="session")
def ctx() -> ContractContext:
    return load_context()


@pytest.fixture(scope="session")
def index(ctx: ContractContext) -> HouseIndex:
    return ctx.index()
