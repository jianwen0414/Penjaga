"""``/sim/v1``: the simulator control plane the Lab calls (operator role only).

Fault and anomaly bodies are the contract's lab types (FaultSpec, AnomalySpec). The remaining
request and response shapes are defined here and published in the OpenAPI document
(``/sim/v1/openapi.json``) until they are agreed for the contract (D-019).

Errors: 401 without the operator token; 404 unknown fault; 409 conflicts with published house
time; 422 invalid request; 502 the stream service failed; 503 no episode yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from penjaga_contract.core import ClockMode, ClockState
from penjaga_contract.lab import AnomalySpec, FaultSpec, FaultType
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.faults import catalogue
from penjaga_sim.service import NoWorldError, SimService, WorldReset, fault_view
from penjaga_sim.stream_client import StreamUnavailableError
from penjaga_sim.world import UnknownFaultError, WorldConflictError, WorldError

PREFIX = "/sim/v1"
BEARER = HTTPBearer(auto_error=False)  # module level: FastAPI resolves annotations here


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResetRequest(_Body):
    """Start a new episode: M4's clock and store are reset, and a fresh world begins."""

    start: AwareDatetime
    mode: ClockMode = ClockMode.accelerated
    speed: Annotated[float, Field(gt=0, le=86_400)] = 60.0
    seed: Annotated[int | None, Field(ge=0, le=2**53 - 1)] = None
    archetype: str | None = None
    enabled_sensor_ids: list[str] | None = Field(
        default=None, description="Sensor-kit ablation; omit for the full kit."
    )


class RandomFaultRequest(_Body):
    fault_type: FaultType
    start_at: AwareDatetime | None = Field(default=None, description="Omit for 'now'.")


class FaultView(BaseModel):
    fault_id: str
    spec: dict[str, Any]
    start: str
    end: str | None
    state: str = Field(description="scheduled, active or ended, relative to published time")


class AnomalyCreated(BaseModel):
    record_id: str


class ResetResponse(BaseModel):
    clock: ClockState
    status: dict[str, Any]


def create_app(service: SimService, *, operator_token: str, autostart: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(service.run(stop)) if autostart else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task

    app = FastAPI(
        title="Penjaga simulator control plane (M2)",
        version=GENERATOR_VERSION,
        lifespan=lifespan,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=None,
    )

    def operator(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> None:
        # only the operator token is known here, so any other token is unauthenticated (401)
        token = "" if credentials is None else credentials.credentials
        if not secrets.compare_digest(token.encode(), operator_token.encode()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "operator bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(operator)]

    def error(code: int) -> Any:
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=code)

        return handler

    # Starlette picks the handler of the nearest class in the exception's MRO
    app.add_exception_handler(NoWorldError, error(status.HTTP_503_SERVICE_UNAVAILABLE))
    app.add_exception_handler(UnknownFaultError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(WorldConflictError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(WorldError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))
    app.add_exception_handler(StreamUnavailableError, error(status.HTTP_502_BAD_GATEWAY))

    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, Any]:
        # unauthenticated: nothing here may help reconstruct the world (no seed, no faults)
        return {"status": "ok", "episode": service.episode, "generator": GENERATOR_VERSION}

    @app.get(f"{PREFIX}/status", dependencies=auth)
    async def get_status() -> dict[str, Any]:
        return service.status()

    @app.get(f"{PREFIX}/faults/catalogue", dependencies=auth)
    async def get_catalogue() -> list[dict[str, Any]]:
        world = service.require_world()
        return catalogue(world.model)

    @app.post(f"{PREFIX}/faults", dependencies=auth, status_code=201, response_model=FaultView)
    async def post_fault(spec: FaultSpec) -> dict[str, Any]:
        fault = await service.inject(spec)
        world = service.require_world()
        return fault_view(world, fault, world.published_until_us)

    @app.post(
        f"{PREFIX}/faults/random", dependencies=auth, status_code=201, response_model=FaultView
    )
    async def post_random_fault(body: RandomFaultRequest) -> dict[str, Any]:
        fault = await service.inject_random(body.fault_type.value, body.start_at)
        world = service.require_world()
        return fault_view(world, fault, world.published_until_us)

    @app.get(f"{PREFIX}/faults", dependencies=auth, response_model=list[FaultView])
    async def get_faults() -> list[dict[str, Any]]:
        return service.faults()

    @app.delete(f"{PREFIX}/faults/{{fault_id}}", dependencies=auth, response_model=FaultView)
    async def delete_fault(fault_id: str) -> dict[str, Any]:
        fault = await service.clear(fault_id)
        world = service.require_world()
        return fault_view(world, fault, world.published_until_us)

    @app.post(
        f"{PREFIX}/anomalies", dependencies=auth, status_code=201, response_model=AnomalyCreated
    )
    async def post_anomaly(spec: AnomalySpec) -> AnomalyCreated:
        return AnomalyCreated(record_id=await service.impose(spec))

    @app.post(f"{PREFIX}/world/reset", dependencies=auth, response_model=ResetResponse)
    async def post_reset(body: ResetRequest) -> ResetResponse:
        sensors = None if body.enabled_sensor_ids is None else frozenset(body.enabled_sensor_ids)
        request = WorldReset(
            start=body.start,
            mode=body.mode,
            speed=body.speed,
            seed=body.seed,
            archetype=body.archetype,
            enabled_sensors=sensors,
        )
        clock = await service.reset(request)
        return ResetResponse(clock=clock, status=service.status())

    @app.get(f"{PREFIX}/truth", dependencies=auth)
    async def get_truth() -> list[dict[str, Any]]:
        return service.truth_records()

    return app
