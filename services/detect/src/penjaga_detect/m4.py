"""The detection service's view of M4: its clock, the lossless subscription, and stored readings.

``HttpM4`` talks to a real M4 with the detector role's bearer token (the only token detection
holds; it can never reach the simulator or ground truth). Tests substitute an in-memory fake
with the same protocol.

Backfill pages through M4's limits:
- Raw readings are capped at 100,000 samples per request (422 beyond), so a stream's window is
  sized from its period.
- Events are capped at 10,000 per request and silently truncated, so a full page means "ask
  again from the last timestamp"; the engine drops repeats by ``seq``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx2
import numpy as np
import numpy.typing as npt
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from penjaga_contract.core import ClockState
from penjaga_detect.chunk import QUALITY_CODES, US, EventRecord, event_record

log = logging.getLogger("penjaga_detect.m4")

RAW_PAGE_SAMPLES = 90_000  # below M4's 100,000 cap, leaving room for irregular streams
EVENT_PAGE = 10_000  # M4's cap

Samples = tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], npt.NDArray[np.uint8]]


class M4UnavailableError(RuntimeError):
    """M4 could not be reached or refused the request."""


class Subscription(Protocol):
    def __aiter__(self) -> AsyncIterator[dict[str, Any]]: ...


class M4(Protocol):
    async def clock(self) -> ClockState: ...

    def subscribe(self) -> contextlib.AbstractAsyncContextManager[Subscription]: ...

    async def raw(self, stream: str, from_us: int, to_us: int, period_s: float) -> Samples:
        """Samples with from <= ts < to."""
        ...

    async def events(
        self, streams: Sequence[str], from_us: int, to_us: int
    ) -> list[tuple[EventRecord, int]]:
        """(event, seq) with from <= ts < to, oldest first."""
        ...


def iso(us: int, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(us // US, tz=zone).replace(microsecond=us % US).isoformat()


class _WsSubscription:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async for text in self._ws:
            yield json.loads(text)


class HttpM4:
    def __init__(
        self, base_url: str, *, token: str, zone: ZoneInfo, timeout_s: float = 30.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._zone = zone
        self._client = httpx2.AsyncClient(timeout=timeout_s, headers=self._headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: Sequence[tuple[str, str]] = ()) -> Any:
        url = f"{self.base_url}/stream/v1/{path}"
        try:
            response = await self._client.get(url, params=list(params))
        except httpx2.HTTPError as exc:
            raise M4UnavailableError(f"GET {url}: {exc}") from exc
        if response.status_code >= 400:
            raise M4UnavailableError(f"GET {url}: {response.status_code} {response.text[:300]}")
        return response.json()

    async def clock(self) -> ClockState:
        return ClockState.model_validate(await self._get("clock"))

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        url = self.base_url.replace("http", "ws", 1) + "/stream/v1/subscribe"
        try:
            async with connect(url, additional_headers=self._headers, max_size=2**26) as ws:
                yield _WsSubscription(ws)
        except (OSError, WebSocketException) as exc:
            raise M4UnavailableError(f"subscribe {url}: {exc}") from exc

    async def raw(self, stream: str, from_us: int, to_us: int, period_s: float) -> Samples:
        page_us = max(US, round(RAW_PAGE_SAMPLES * period_s * US))
        ts: list[int] = []
        values: list[float] = []
        quality: list[int] = []
        t = from_us
        while t < to_us:
            t1 = min(to_us, t + page_us)
            doc = await self._get(
                "readings/raw",
                [("stream", stream), ("from", iso(t, self._zone)), ("to", iso(t1, self._zone))],
            )
            for sample in doc["samples"]:
                moment = datetime.fromisoformat(sample["t"])
                ts.append(int(moment.replace(microsecond=0).timestamp()) * US + moment.microsecond)
                values.append(np.nan if sample["value"] is None else float(sample["value"]))
                quality.append(QUALITY_CODES[sample["quality"]])
            t = t1
        return (
            np.asarray(ts, dtype=np.int64),
            np.asarray(values, dtype=np.float64),
            np.asarray(quality, dtype=np.uint8),
        )

    async def events(
        self, streams: Sequence[str], from_us: int, to_us: int
    ) -> list[tuple[EventRecord, int]]:
        out: list[tuple[EventRecord, int]] = []
        if not streams:
            return out
        t = from_us
        while t < to_us:
            params = [("from", iso(t, self._zone)), ("to", iso(to_us, self._zone))]
            params += [("stream", s) for s in streams]
            page = await self._get("events", params)
            out += [(event_record(doc), int(doc["seq"])) for doc in page]
            if len(page) < EVENT_PAGE:
                break
            last = out[-1][0].ts_us
            if last <= t:  # a whole page at one instant: cannot page further by time
                raise M4UnavailableError(f"more than {EVENT_PAGE} events at one instant")
            t = last
        return out
