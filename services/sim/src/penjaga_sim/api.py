"""``/sim/v1``: the simulator control plane the Lab calls (operator role only).

Every request and response body is a contract lab type (``lab/sim_api.schema.json``,
``FaultSpec``, ``AnomalySpec``, ``TruthRecord``), so the Lab UI uses the generated TypeScript
types. The OpenAPI document is served at ``/sim/v1/openapi.json``.

Responses are the service's own contract documents, checked against the JSON Schema before
they leave (a violation is a server bug: 500). They are not re-serialised through the
generated models, which would add nulls the schema forbids.

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
from pydantic import BaseModel, ConfigDict, Field

from penjaga_contract import schema_registry
from penjaga_contract.core import ClockMode
from penjaga_contract.lab import (
    AnomalyCreated,
    AnomalySpec,
    CatalogueEntry,
    FaultSpec,
    FaultView,
    RandomFaultRequest,
    ResetRequest,
    ResetResponse,
    ScenarioApplied,
    ScenarioPreset,
    ScenarioRequest,
    SimHealth,
    SimStatus,
    TruthRecord,
)
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.faults import ActiveFault, catalogue
from penjaga_sim.scenarios import UnknownScenarioError
from penjaga_sim.service import NoWorldError, SimService, WorldReset, fault_view
from penjaga_sim.stream_client import StreamUnavailableError
from penjaga_sim.world import UnknownFaultError, WorldConflictError, WorldError

PREFIX = "/sim/v1"
BEARER = HTTPBearer(auto_error=False)  # module level: FastAPI resolves annotations here


# The generated ResetRequest also accepts an explicit null for optional fields
# (datamodel-codegen makes them nullable); null means the service default.
DEFAULT_MODE: ClockMode = WorldReset.mode
DEFAULT_SPEED: float = WorldReset.speed


class ContractViolationError(RuntimeError):
    """The simulator produced a response that breaks the contract (a bug, never the caller's)."""


def document(
    doc: Any, schema: str, definition: str | None = None, *, many: bool = False, code: int = 200
) -> JSONResponse:
    """``doc`` as the response, after full JSON Schema validation against the lab contract."""
    for item in doc if many else [doc]:
        problems = schema_registry.error_messages("lab", schema, item, definition)
        if problems:
            raise ContractViolationError(f"{definition or schema}: {'; '.join(problems)}")
    return JSONResponse(doc, status_code=code)


class ActionRequest(BaseModel):
    """POST /sim/v1/actions (not in the lab contract yet; a candidate for sim_api.schema.json)."""

    model_config = ConfigDict(extra="forbid")

    close_valves: list[str] = Field(default_factory=list, max_length=12)
    switch_off_circuits: list[str] = Field(default_factory=list, max_length=24)
    hold_water_use: bool = True
    duration_s: float = Field(gt=0, le=86400)


class ActionApplied(BaseModel):
    start: str
    end: str
    close_valves: list[str]
    switch_off_circuits: list[str]
    hold_water_use: bool


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
    app.add_exception_handler(UnknownScenarioError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(WorldConflictError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(WorldError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))
    app.add_exception_handler(StreamUnavailableError, error(status.HTTP_502_BAD_GATEWAY))

    api = "sim_api"

    @app.get(f"{PREFIX}/health", response_model=SimHealth)
    async def health() -> JSONResponse:
        # unauthenticated: nothing here may help reconstruct the world (no seed, no faults)
        body = {"status": "ok", "episode": service.episode, "generator": GENERATOR_VERSION}
        return document(body, api, "SimHealth")

    @app.get(f"{PREFIX}/status", dependencies=auth, response_model=SimStatus)
    async def get_status() -> JSONResponse:
        return document(service.status(), api, "SimStatus")

    @app.get(f"{PREFIX}/faults/catalogue", dependencies=auth, response_model=list[CatalogueEntry])
    async def get_catalogue() -> JSONResponse:
        world = service.require_world()
        return document(catalogue(world.model), api, "CatalogueEntry", many=True)

    def view(fault: ActiveFault, code: int = 200) -> JSONResponse:
        world = service.require_world()
        return document(
            fault_view(world, fault, world.published_until_us), api, "FaultView", code=code
        )

    @app.post(f"{PREFIX}/faults", dependencies=auth, status_code=201, response_model=FaultView)
    async def post_fault(spec: FaultSpec) -> JSONResponse:
        return view(await service.inject(spec), 201)

    @app.post(
        f"{PREFIX}/faults/random", dependencies=auth, status_code=201, response_model=FaultView
    )
    async def post_random_fault(body: RandomFaultRequest) -> JSONResponse:
        return view(await service.inject_random(body.fault_type.value, body.start_at), 201)

    @app.get(f"{PREFIX}/faults", dependencies=auth, response_model=list[FaultView])
    async def get_faults() -> JSONResponse:
        return document(service.faults(), api, "FaultView", many=True)

    @app.delete(f"{PREFIX}/faults/{{fault_id}}", dependencies=auth, response_model=FaultView)
    async def delete_fault(fault_id: str) -> JSONResponse:
        return view(await service.clear(fault_id))

    @app.post(f"{PREFIX}/actions", dependencies=auth, status_code=201, response_model=ActionApplied)
    async def post_action(body: ActionRequest) -> ActionApplied:
        """A household action during a live guided check: close valves (and hold water use) or
        switch circuits off, from now for ``duration_s``. The Lab sends it when the owner
        confirms a check, so the simulated house does what the owner was asked to do."""
        applied = await service.act(
            close_valves=tuple(body.close_valves),
            switch_off_circuits=tuple(body.switch_off_circuits),
            hold_water_use=body.hold_water_use,
            duration_s=body.duration_s,
        )
        return ActionApplied(**applied)

    @app.post(
        f"{PREFIX}/anomalies", dependencies=auth, status_code=201, response_model=AnomalyCreated
    )
    async def post_anomaly(spec: AnomalySpec) -> JSONResponse:
        body = {"record_id": await service.impose(spec)}
        return document(body, api, "AnomalyCreated", code=201)

    @app.post(f"{PREFIX}/world/reset", dependencies=auth, response_model=ResetResponse)
    async def post_reset(body: ResetRequest) -> JSONResponse:
        sensors = None if body.enabled_sensor_ids is None else frozenset(body.enabled_sensor_ids)
        request = WorldReset(
            start=body.start,
            mode=DEFAULT_MODE if body.mode is None else body.mode,
            speed=DEFAULT_SPEED if body.speed is None else body.speed,
            seed=body.seed,
            archetype=body.archetype,
            enabled_sensors=sensors,
        )
        clock = await service.reset(request)
        response = {"clock": clock.model_dump(mode="json"), "status": service.status()}
        return document(response, api, "ResetResponse")

    @app.get(f"{PREFIX}/scenarios", dependencies=auth, response_model=list[ScenarioPreset])
    async def get_scenarios() -> JSONResponse:
        docs = [s.document() for s in service.scenarios().values()]
        return document(docs, api, "ScenarioPreset", many=True)

    @app.post(
        f"{PREFIX}/scenarios/{{name}}/apply", dependencies=auth, response_model=ScenarioApplied
    )
    async def post_scenario(name: str, body: ScenarioRequest) -> JSONResponse:
        applied = await service.apply_scenario(
            name,
            seed=body.seed,
            start=body.start,
            mode=DEFAULT_MODE if body.mode is None else body.mode,
            speed=DEFAULT_SPEED if body.speed is None else body.speed,
        )
        return document(applied, api, "ScenarioApplied")

    @app.get(f"{PREFIX}/truth", dependencies=auth, response_model=list[TruthRecord])
    async def get_truth() -> JSONResponse:
        return document(service.truth_records(), "truth_record", many=True)

    return app
