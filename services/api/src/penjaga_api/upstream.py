"""The Core API's view of the data/ML services: detection (`/detect/v1`) and the clock
(`/stream/v1`).

``HttpUpstream`` holds only the **app** role token. It can read findings, run guided checks, set
layer consent and read the clock and reading summaries. It can never reach the simulator or
ground truth. Tests substitute an in-memory fake with the same protocol.

Findings arrive over detection's SSE stream (``GET /detect/v1/findings/stream``): one ``finding``
event per new revision, whose ``id`` is detection's store cursor. Resuming with ``?after=<cursor>``
replays every later revision of the current run, so a reconnect loses nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx2


class UpstreamUnavailableError(RuntimeError):
    """Detection or the stream service could not be reached or refused the request."""


class Upstream(Protocol):
    async def clock(self) -> dict[str, Any]: ...

    async def findings(self) -> list[dict[str, Any]]:
        """Latest revision of every finding in the current run."""
        ...

    def finding_events(self, after: int) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """(cursor, finding revision) for every revision stored after ``after``, then live ones."""
        ...

    async def layers(self) -> list[dict[str, Any]]: ...

    async def set_layer(self, layer: str, enabled: bool) -> dict[str, Any]: ...

    async def readings_summary(
        self, stream: str, start: str, end: str, bucket_s: float
    ) -> dict[str, Any]:
        """Bucketed summary of one stream (``GET /stream/v1/readings/summary``, at most 500
        points): ``{stream, unit, from, to, bucket_s, points: [{t, n, min, max, mean, last}]}``."""
        ...

    async def start_check(self, request: dict[str, Any]) -> dict[str, Any]:
        """Start a guided check (``POST /detect/v1/checks``): a FlowStopCheck,
        TankLevelTrendCheck or CircuitLoadCheck. Returns the ``Check`` (status ``pending``)."""
        ...

    async def get_check(self, check_id: str) -> dict[str, Any]:
        """``GET /detect/v1/checks/{id}``: ``pending`` until house time passes ``ready_at``."""
        ...


def parse_sse(lines: list[str]) -> tuple[str | None, str | None, str]:
    """(id, event, data) of one SSE message given its lines (comments already skipped)."""
    event_id: str | None = None
    event: str | None = None
    data: list[str] = []
    for line in lines:
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "id":
            event_id = value
        elif field == "event":
            event = value
        elif field == "data":
            data.append(value)
    return event_id, event, "\n".join(data)


class HttpUpstream:
    def __init__(
        self, *, stream_url: str, detect_url: str, token: str, timeout_s: float = 30.0
    ) -> None:
        self.stream_url = stream_url.rstrip("/")
        self.detect_url = detect_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx2.AsyncClient(timeout=timeout_s, headers=headers)
        # SSE stays open; only connecting may time out.
        self._sse_timeout = httpx2.Timeout(timeout_s, read=None)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, body: Any = None) -> Any:
        try:
            response = await self._client.request(method, url, json=body)
        except httpx2.HTTPError as exc:
            raise UpstreamUnavailableError(f"{method} {url}: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamUnavailableError(
                f"{method} {url}: {response.status_code} {response.text[:300]}"
            )
        return response.json()

    async def clock(self) -> dict[str, Any]:
        doc: dict[str, Any] = await self._request("GET", f"{self.stream_url}/stream/v1/clock")
        return doc

    async def findings(self) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = await self._request(
            "GET", f"{self.detect_url}/detect/v1/findings"
        )
        return docs

    async def layers(self) -> list[dict[str, Any]]:
        url = f"{self.detect_url}/detect/v1/layers"
        docs: list[dict[str, Any]] = await self._request("GET", url)
        return docs

    async def set_layer(self, layer: str, enabled: bool) -> dict[str, Any]:
        doc: dict[str, Any] = await self._request(
            "PUT", f"{self.detect_url}/detect/v1/layers/{layer}", {"enabled": enabled}
        )
        return doc

    async def readings_summary(
        self, stream: str, start: str, end: str, bucket_s: float
    ) -> dict[str, Any]:
        url = f"{self.stream_url}/stream/v1/readings/summary"
        params = {"stream": stream, "from": start, "to": end, "bucket_s": str(bucket_s)}
        try:
            response = await self._client.get(url, params=params)
        except httpx2.HTTPError as exc:
            raise UpstreamUnavailableError(f"GET {url}: {exc}") from exc
        if response.status_code >= 400:
            raise UpstreamUnavailableError(
                f"GET {url}: {response.status_code} {response.text[:300]}"
            )
        doc: dict[str, Any] = response.json()
        return doc

    async def start_check(self, request: dict[str, Any]) -> dict[str, Any]:
        doc: dict[str, Any] = await self._request(
            "POST", f"{self.detect_url}/detect/v1/checks", request
        )
        return doc

    async def get_check(self, check_id: str) -> dict[str, Any]:
        doc: dict[str, Any] = await self._request(
            "GET", f"{self.detect_url}/detect/v1/checks/{check_id}"
        )
        return doc

    async def finding_events(self, after: int) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        url = f"{self.detect_url}/detect/v1/findings/stream"
        try:
            async with self._client.stream(
                "GET", url, params={"after": str(after)}, timeout=self._sse_timeout
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise UpstreamUnavailableError(f"GET {url}: {response.status_code} {body}")
                lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line:
                        lines.append(line)
                        continue
                    if not lines:
                        continue
                    event_id, event, data = parse_sse(lines)
                    lines = []
                    if event == "finding" and event_id is not None and event_id.isdigit():
                        yield int(event_id), json.loads(data)
        except httpx2.HTTPError as exc:
            raise UpstreamUnavailableError(f"GET {url}: {exc}") from exc
