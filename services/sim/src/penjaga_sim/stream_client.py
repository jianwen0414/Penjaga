"""The simulator's view of the M4 stream service: its clock, resets, and the ingest socket.

``StreamClient`` is the interface the live publisher uses; ``HttpStreamClient`` talks to a real
M4 over HTTP and WebSocket (bearer tokens per role, D-017). Tests substitute an in-memory fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx2
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from penjaga_contract.core import ClockState, RejectedMessage, ResetCommand

log = logging.getLogger("penjaga_sim.stream")


class StreamUnavailableError(RuntimeError):
    """M4 could not be reached or refused the request."""


class Ingest(Protocol):
    async def send(self, messages: list[dict[str, Any]]) -> None: ...


class StreamClient(Protocol):
    async def clock(self) -> ClockState: ...

    async def reset(self, command: ResetCommand) -> ClockState: ...

    def ingest(self) -> contextlib.AbstractAsyncContextManager[Ingest]: ...


class _WsIngest:
    """Sends IngestMessages; M4 only answers when it refuses one (RejectedMessage)."""

    def __init__(self, ws: ClientConnection, rejections: list[RejectedMessage]) -> None:
        self.ws = ws
        self.rejections = rejections

    async def send(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            await self.ws.send(json.dumps(message, separators=(",", ":")))

    async def receive_rejections(self) -> None:
        with contextlib.suppress(ConnectionClosed):
            async for text in self.ws:
                rejected = RejectedMessage.model_validate_json(text)
                log.warning("M4 rejected %s", rejected.data.model_dump_json())
                self.rejections.append(rejected)


class HttpStreamClient:
    def __init__(
        self, base_url: str, *, device_token: str, operator_token: str, timeout_s: float = 10.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_token = device_token
        self.operator_token = operator_token
        self.timeout_s = timeout_s
        self.rejections: list[RejectedMessage] = []

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        url = f"{self.base_url}/stream/v1/{path}"
        try:
            async with httpx2.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.request(
                    method, url, json=body, headers=self._headers(self.operator_token)
                )
        except httpx2.HTTPError as exc:
            raise StreamUnavailableError(f"{method} {url}: {exc}") from exc
        if response.status_code >= 400:
            raise StreamUnavailableError(f"{method} {url}: {response.status_code} {response.text}")
        return response.json()

    async def clock(self) -> ClockState:
        return ClockState.model_validate(await self._call("GET", "clock"))

    async def reset(self, command: ResetCommand) -> ClockState:
        body = command.model_dump(mode="json", exclude_none=True)
        return ClockState.model_validate(await self._call("POST", "reset", body))

    @contextlib.asynccontextmanager
    async def ingest(self) -> AsyncIterator[Ingest]:
        ws_url = self.base_url.replace("http", "ws", 1) + "/stream/v1/ingest"
        try:
            async with connect(
                ws_url, additional_headers=self._headers(self.device_token), max_size=2**24
            ) as ws:
                ingest = _WsIngest(ws, self.rejections)
                receiver = asyncio.create_task(ingest.receive_rejections())
                try:
                    yield ingest
                finally:
                    receiver.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await receiver
        except (OSError, WebSocketException) as exc:
            raise StreamUnavailableError(f"ingest {ws_url}: {exc}") from exc
