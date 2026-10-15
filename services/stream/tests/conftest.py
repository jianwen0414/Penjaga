from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from penjaga_contract.core import ClockMode
from penjaga_contract.validate import ContractContext, load_context
from penjaga_stream.clock import HouseClock
from penjaga_stream.config import Role, StreamConfig
from penjaga_stream.service import StreamService
from penjaga_stream.testing import START, FakeMonotonic
from penjaga_stream.timeutil import house_zone, to_us

TOKENS = {role: f"test-token-{role.value}-0123456789" for role in Role}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def ctx() -> ContractContext:
    return load_context()


@pytest.fixture
def mono() -> FakeMonotonic:
    return FakeMonotonic()


@pytest.fixture
def config(tmp_path: Path) -> StreamConfig:
    return StreamConfig(data_dir=tmp_path / "stream", tokens=TOKENS, lossless_put_timeout_s=0.5)


ServiceFactory = Callable[..., StreamService]


@pytest.fixture
def make_service(
    config: StreamConfig, ctx: ContractContext, mono: FakeMonotonic
) -> Iterator[ServiceFactory]:
    created: list[StreamService] = []

    def factory(speed: float = 600.0, mode: ClockMode = ClockMode.accelerated) -> StreamService:
        clock = HouseClock(
            start_us=to_us(START),
            mode=mode,
            speed=speed if mode is ClockMode.accelerated else 1.0,
            zone=house_zone(ctx.house.location.timezone),
            monotonic=mono,
        )
        service = StreamService(config, ctx=ctx, clock=clock, monotonic=mono)
        created.append(service)
        return service

    yield factory
    for service in created:
        service.store.close()
