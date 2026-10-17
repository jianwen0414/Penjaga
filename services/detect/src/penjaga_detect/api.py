"""``/detect/v1``: findings, layer consent, on-demand runs, guided checks and localisation.

Specified in docs/design/interfaces.md (M5).

Bodies are contract types (``core/detect_api.schema.json``, ``finding.schema.json``). Responses
are the service's own documents, checked against the JSON Schema before they leave; a violation
is a server bug (500). Findings were fully validated when they were made, so they are not
checked again.

Authentication: ``Authorization: Bearer <token>`` with the app role (Core API, agent) or the
operator role (Lab), on everything except health.

Errors: 401 without a valid token; 404 unknown finding, check, detector or layer; 422 invalid
request; 502 M4 failed; 503 no episode followed yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections.abc import AsyncIterator, Collection
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from penjaga_contract import schema_registry
from penjaga_contract.core import (
    Check,
    CircuitLoadCheck,
    DetectHealth,
    DetectorStatus,
    Finding,
    FindingStatus,
    FlowStopCheck,
    Layer,
    LayerState,
    LayerUpdate,
    LocaliseRequest,
    LocaliseResponse,
    ResidualDropCheck,
    RunRequest,
    RunResponse,
    TankLevelTrendCheck,
)
from penjaga_detect.m4 import M4UnavailableError
from penjaga_detect.service import DetectService, NoRunError, NotFoundError, RequestError

PREFIX = "/detect/v1"
BEARER = HTTPBearer(auto_error=False)  # module level: FastAPI resolves annotations here
SSE_KEEPALIVE_S = 15.0

CheckRequest = Annotated[
    FlowStopCheck | TankLevelTrendCheck | CircuitLoadCheck | ResidualDropCheck,
    Field(discriminator="kind"),
]


class ContractViolationError(RuntimeError):
    """The service produced a response that breaks the contract (a bug, never the caller's)."""


def document(doc: Any, definition: str, *, many: bool = False, code: int = 200) -> JSONResponse:
    """``doc`` as the response, after full JSON Schema validation (core/detect_api)."""
    for item in doc if many else [doc]:
        problems = schema_registry.error_messages("core", "detect_api", item, definition)
        if problems:
            raise ContractViolationError(f"{definition}: {'; '.join(problems)}")
    return JSONResponse(doc, status_code=code)


def create_app(
    service: DetectService, *, tokens: Collection[str], autostart: bool = True
) -> FastAPI:
    if not tokens or any(len(t) < 16 for t in tokens):
        raise ValueError("need app/operator tokens of at least 16 characters")
    accepted = [t.encode() for t in tokens]

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(service.serve(stop)) if autostart else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task

    app = FastAPI(
        title="Penjaga detection service (M5)",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=None,
    )

    def caller(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> None:
        token = "" if credentials is None else credentials.credentials
        ok = False
        for expected in accepted:  # constant time per token, no early exit
            ok |= secrets.compare_digest(token.encode(), expected)
        if not ok:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "app or operator bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(caller)]

    def error(code: int) -> Any:
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=code)

        return handler

    app.add_exception_handler(NoRunError, error(status.HTTP_503_SERVICE_UNAVAILABLE))
    app.add_exception_handler(NotFoundError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(RequestError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))
    app.add_exception_handler(M4UnavailableError, error(status.HTTP_502_BAD_GATEWAY))

    @app.get(f"{PREFIX}/health", response_model=DetectHealth)
    async def health() -> JSONResponse:
        return document(service.health(), "DetectHealth")

    @app.get(f"{PREFIX}/diagnostics", dependencies=auth)
    async def diagnostics() -> dict[str, Any]:
        """Operator detail beyond the contract's health document (not a contract type)."""
        return service.diagnostics()

    @app.get(f"{PREFIX}/detectors", dependencies=auth, response_model=list[DetectorStatus])
    async def detectors() -> JSONResponse:
        return document(service.detector_status(), "DetectorStatus", many=True)

    def layer_states() -> list[dict[str, Any]]:
        return [
            {"layer": layer, "enabled": layer in service.enabled_layers}
            for layer in service.deployed_layers
        ]

    @app.get(f"{PREFIX}/layers", dependencies=auth, response_model=list[LayerState])
    async def layers() -> JSONResponse:
        return document(layer_states(), "LayerState", many=True)

    @app.put(f"{PREFIX}/layers/{{layer}}", dependencies=auth, response_model=LayerState)
    async def put_layer(layer: Layer, body: LayerUpdate) -> JSONResponse:
        await service.set_layer(layer.value, body.enabled)
        return document({"layer": layer.value, "enabled": body.enabled}, "LayerState")

    _add_finding_routes(app, service, auth)
    _add_action_routes(app, service, auth)
    return app


def _add_finding_routes(app: FastAPI, service: DetectService, auth: list[Any]) -> None:
    @app.get(f"{PREFIX}/findings", dependencies=auth, response_model=list[Finding])
    async def findings(
        status_: Annotated[FindingStatus | None, Query(alias="status")] = None,
        layer: Layer | None = None,
        since: datetime | None = None,
    ) -> JSONResponse:
        docs = service.findings(
            None if status_ is None else status_.value,
            None if layer is None else layer.value,
            since,
        )
        return JSONResponse(docs)

    @app.get(f"{PREFIX}/findings/stream", dependencies=auth)
    async def findings_stream(
        request: Request,
        after: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """Server-sent events: one ``finding`` event per new revision, ``id`` = store cursor.
        Resume with the ``Last-Event-ID`` header (or ``?after=``); default: new ones only."""
        if last_event_id is not None and last_event_id.isdigit():
            cursor = int(last_event_id)
        elif after is not None:
            cursor = after
        else:
            cursor = service.store.last_seq()

        async def events() -> AsyncIterator[str]:
            nonlocal cursor
            yield ": connected\n\n"
            while not await request.is_disconnected():
                revisions = await service.wait_for_revisions(cursor, SSE_KEEPALIVE_S)
                if not revisions:
                    yield ": keep-alive\n\n"
                    continue
                run = service.run
                for r in revisions:
                    cursor = r.seq
                    if run is not None and r.run == run.key:
                        data = json.dumps(r.doc, separators=(",", ":"))
                        yield f"id: {r.seq}\nevent: finding\ndata: {data}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{PREFIX}/findings/{{finding_id}}", dependencies=auth, response_model=Finding)
    async def finding(finding_id: str) -> JSONResponse:
        return JSONResponse(service.finding(finding_id)[-1])

    @app.get(
        f"{PREFIX}/findings/{{finding_id}}/history",
        dependencies=auth,
        response_model=list[Finding],
    )
    async def finding_history(finding_id: str) -> JSONResponse:
        return JSONResponse(service.finding(finding_id))


def _add_action_routes(app: FastAPI, service: DetectService, auth: list[Any]) -> None:
    @app.post(f"{PREFIX}/run", dependencies=auth, response_model=RunResponse)
    async def run(body: RunRequest) -> JSONResponse:
        result = await service.run_detector(body.detector, body.from_, body.to)
        return document(result, "RunResponse")

    @app.post(f"{PREFIX}/checks", dependencies=auth, status_code=201, response_model=Check)
    async def post_check(body: CheckRequest) -> JSONResponse:
        request = body.model_dump(mode="json", by_alias=True, exclude_none=True)
        return document(await service.create_check(request), "Check", code=201)

    @app.get(f"{PREFIX}/checks/{{check_id}}", dependencies=auth, response_model=Check)
    async def get_check(check_id: str) -> JSONResponse:
        return document(service.check(check_id), "Check")

    @app.post(f"{PREFIX}/localise", dependencies=auth, response_model=LocaliseResponse)
    async def post_localise(body: LocaliseRequest) -> JSONResponse:
        request = body.model_dump(mode="json", by_alias=True)
        return document(service.localise(request), "LocaliseResponse")
