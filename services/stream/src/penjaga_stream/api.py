"""HTTP and WebSocket API of the stream service (M4). Spec: docs/design/interfaces.md.

Authentication: ``Authorization: Bearer <token>`` (HTTP and WebSocket), or ``?token=`` on
WebSockets, for browser clients that cannot set headers. Tokens map to roles (config.Role).
Datetimes in query strings must be URL-encoded (``+08:00`` -> ``%2B08:00``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime

from penjaga_contract.core import (
    ClockCommand,
    ClockState,
    Event,
    RawResponse,
    ResetCommand,
    StreamStatus,
    SummaryResponse,
)
from penjaga_stream.clock import ClockError
from penjaga_stream.config import Role
from penjaga_stream.hub import LiveSubscriber
from penjaga_stream.service import QueryError, StreamService

PREFIX = "/stream/v1"
ANY_ROLE = frozenset(Role)
QUERY_ROLES = frozenset({Role.app, Role.operator, Role.detector})
RAW_ROLES = frozenset({Role.operator, Role.detector})
LIVE_ROLES = frozenset({Role.app, Role.operator})
WS_TRY_AGAIN_LATER = 1013


def _token_from(headers: Any, query: Any) -> str | None:
    auth = headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return str(auth[7:].strip())
    token = query.get("token")
    return str(token) if token else None


def http_role(allowed: frozenset[Role]) -> Callable[[Request], Role]:
    def dependency(request: Request) -> Role:
        service: StreamService = request.app.state.service
        role = service.config.role_for(_token_from(request.headers, {}))
        if role is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {role.value!r} not allowed")
        return role

    return dependency


def ws_role(allowed: frozenset[Role]) -> Callable[[WebSocket], Role]:
    def dependency(websocket: WebSocket) -> Role:
        service: StreamService = websocket.app.state.service
        role = service.config.role_for(_token_from(websocket.headers, websocket.query_params))
        if role is None or role not in allowed:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        return role

    return dependency


async def _until_disconnect(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            await websocket.receive_text()


async def _serve(websocket: WebSocket, sender: Coroutine[Any, Any, None]) -> None:
    """Run ``sender`` until it finishes or the client disconnects, whichever is first."""
    tasks = {asyncio.create_task(sender), asyncio.create_task(_until_disconnect(websocket))}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await task
    for task in done:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            task.result()


def create_app(service: StreamService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        background = asyncio.create_task(service.run_background())
        try:
            yield
        finally:
            background.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await background
            await service.close()

    app = FastAPI(
        title="Penjaga stream service (M4)",
        version="0.1.0",
        lifespan=lifespan,
        description="House-time clock, reading ingest, storage, fan-out and summaries.",
    )
    app.state.service = service

    @app.exception_handler(QueryError)
    async def _query_error(_: Request, exc: QueryError) -> JSONResponse:
        code = status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_422_UNPROCESSABLE_CONTENT
        return JSONResponse({"detail": str(exc)}, status_code=code)

    @app.exception_handler(ClockError)
    async def _clock_error(_: Request, exc: ClockError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)

    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "episode": service.clock.episode,
            "house_time": service.clock.state().house_time.isoformat(),
            "subscribers": {"lossless": len(service.hub.lossless), "live": len(service.hub.live)},
            "rejected": service.rejected,
        }

    @app.get(f"{PREFIX}/clock", response_model=ClockState)
    async def get_clock(_: Annotated[Role, Depends(http_role(ANY_ROLE))]) -> ClockState:
        return service.clock_state()

    @app.post(f"{PREFIX}/clock", response_model=ClockState)
    async def post_clock(
        command: ClockCommand, _: Annotated[Role, Depends(http_role(frozenset({Role.operator})))]
    ) -> ClockState:
        return await service.command_clock(command)

    @app.post(f"{PREFIX}/reset", response_model=ClockState)
    async def post_reset(
        command: ResetCommand, _: Annotated[Role, Depends(http_role(frozenset({Role.operator})))]
    ) -> ClockState:
        return await service.reset(command)

    @app.get(f"{PREFIX}/streams", response_model=list[StreamStatus])
    async def get_streams(_: Annotated[Role, Depends(http_role(ANY_ROLE))]) -> list[StreamStatus]:
        return service.status()

    @app.get(f"{PREFIX}/readings/summary", response_model=SummaryResponse)
    async def get_summary(
        _: Annotated[Role, Depends(http_role(QUERY_ROLES))],
        stream: str,
        start: Annotated[AwareDatetime, Query(alias="from")],
        to: AwareDatetime,
        bucket_s: Annotated[float, Query(gt=0)],
    ) -> SummaryResponse:
        return await service.summary(stream, start, to, bucket_s)

    @app.get(f"{PREFIX}/readings/raw", response_model=RawResponse)
    async def get_raw(
        _: Annotated[Role, Depends(http_role(RAW_ROLES))],
        stream: str,
        start: Annotated[AwareDatetime, Query(alias="from")],
        to: AwareDatetime,
    ) -> RawResponse:
        return await service.raw(stream, start, to)

    @app.get(f"{PREFIX}/events", response_model=list[Event])
    async def get_events(
        _: Annotated[Role, Depends(http_role(QUERY_ROLES))],
        start: Annotated[AwareDatetime, Query(alias="from")],
        to: AwareDatetime,
        stream: Annotated[list[str] | None, Query()] = None,
    ) -> list[Event]:
        return await service.events(stream, start, to)

    _add_websocket_routes(app, service)
    return app


def _add_websocket_routes(app: FastAPI, service: StreamService) -> None:
    @app.websocket(f"{PREFIX}/ingest")
    async def ws_ingest(
        websocket: WebSocket, _: Annotated[Role, Depends(ws_role(frozenset({Role.device})))]
    ) -> None:
        await websocket.accept()
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                result = await service.ingest(await websocket.receive_text())
                if result.rejection is not None:
                    await websocket.send_text(result.rejection)

    @app.websocket(f"{PREFIX}/subscribe")
    async def ws_subscribe(
        websocket: WebSocket, _: Annotated[Role, Depends(ws_role(frozenset({Role.detector})))]
    ) -> None:
        await websocket.accept()
        sub = service.hub.add_lossless()

        async def sender() -> None:
            await websocket.send_text(service.clock_message())
            while True:
                try:
                    text = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
                except TimeoutError:
                    if sub.closed:
                        await websocket.close(
                            code=WS_TRY_AGAIN_LATER, reason="too slow; reconnect and backfill"
                        )
                        return
                    continue
                await websocket.send_text(text)

        try:
            await _serve(websocket, sender())
        finally:
            service.hub.remove_lossless(sub)

    @app.websocket(f"{PREFIX}/live")
    async def ws_live(
        websocket: WebSocket,
        _: Annotated[Role, Depends(ws_role(LIVE_ROLES))],
        streams: str | None = None,
        max_hz: float = 2.0,
    ) -> None:
        lo, hi = service.config.live_max_hz
        wanted = None if not streams else frozenset(s for s in streams.split(",") if s)
        unknown = sorted((wanted or frozenset()) - set(service.registry.ids))
        if not lo <= max_hz <= hi or unknown:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=f"max_hz must be in [{lo}, {hi}]" if not unknown else f"unknown {unknown}",
            )
        await websocket.accept()
        sub = LiveSubscriber(streams=wanted, max_hz=max_hz, zone=service.zone)
        service.hub.add_live(sub)

        async def sender() -> None:
            await websocket.send_text(service.clock_message())
            await sub.run_sender(websocket.send_text, service.clock.now_us)

        try:
            await _serve(websocket, sender())
        finally:
            service.hub.remove_live(sub)
