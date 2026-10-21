"""``/api/v1``: the Core API's HTTP surface.

Authentication: ``Authorization: Bearer <token>``, one token per role: ``homeowner`` (the app),
``management`` (the portal), ``operator`` (the Lab). Health is public and reveals nothing personal.

Errors: 401 without a valid token; 403 for a role that may not call the route; 404 unknown item;
409 consent decided on an outdated text, a case action its state doesn't allow, or a layer the
owner switched off; 422 invalid request, unknown contract ID or an unreadable notice; 502 detection
or the clock failed; 503 no episode followed yet, the AI notice reader is unavailable, or Penjaga's
voice is off or unavailable (the app then speaks with the browser's own voice).

No ``from __future__ import annotations`` here: FastAPI resolves route annotations at runtime,
and the role guards they name are local to ``create_app``.
"""

import asyncio
import contextlib
import json
import re
import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from penjaga_api.actions import ActionKind, ActionRefusedError
from penjaga_api.agent.notices import NoticeUnreadableError
from penjaga_api.agent.openrouter import LLMUnavailableError
from penjaga_api.ids import UnknownIdError
from penjaga_api.knowledge import get_vendors
from penjaga_api.notices import NoticeDraft, draft_issues, preset_names
from penjaga_api.service import (
    EVENT_AUDIENCE,
    ApiService,
    AppLayer,
    CaseActionRefusedError,
    CaseNotFoundError,
    LayerOffError,
    MaintenanceItemNotFoundError,
    NoEpisodeError,
    NoticeNotFoundError,
    Role,
    StaleConsentTextError,
)
from penjaga_api.store import DEFAULT_RETENTION_DAYS
from penjaga_api.upstream import UpstreamUnavailableError
from penjaga_api.voice import MAX_TEXT_CHARS, GoogleSpeech, SpeechUnavailableError

PREFIX = "/api/v1"
BEARER = HTTPBearer(auto_error=False)  # module level: FastAPI resolves annotations here
SSE_KEEPALIVE_S = 15.0


class ConsentUpdate(BaseModel):
    enabled: bool
    text_version: str = Field(min_length=1, max_length=32)


class RetentionUpdate(BaseModel):
    days: int = Field(ge=1, le=3650)


class IdsRequest(BaseModel):
    ids: list[str] = Field(max_length=200)


class DismissRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class ContainRequest(BaseModel):
    what: Literal["main_stop_valve", "breaker"]


class PreviewRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class TicketRequest(BaseModel):
    note: str = Field(default="", max_length=1000)
    contact: str = "in_app"
    confirmation: str = Field(min_length=10, max_length=64)


class BookingRequest(BaseModel):
    vendor_id: str = Field(max_length=32)
    slot: int
    confirmation: str = Field(min_length=10, max_length=64)


class CheckRequest(BaseModel):
    fixture_id: str = Field(max_length=64)
    confirmation: str = Field(min_length=10, max_length=64)


class StatusRequest(BaseModel):
    status: str


class ReplyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class NoticeText(BaseModel):
    text: str = Field(min_length=20, max_length=6000)


class SeedNoticeRequest(BaseModel):
    preset: str = Field(max_length=40)
    starts_in_h: float | None = Field(default=None, ge=-48, le=240)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, pattern=r"^conv_[0-9a-f]{16}$")
    case_id: str | None = Field(default=None, max_length=64)


def _sentences(text: str) -> list[str]:
    """Split a checked reply into sentence-sized chunks for streaming."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p + " " for p in parts[:-1]] + parts[-1:] if parts else []


@dataclass(frozen=True)
class Guards:
    """Role checks, each usable as ``Annotated[Role, Depends(guard)]``."""

    homeowner: Callable[..., Role]
    management: Callable[..., Role]
    operator: Callable[..., Role]
    app: Callable[..., Role]  # homeowner or operator (the Lab sees what the app sees)
    anyone: Callable[..., Role]


def _guards(tokens: Mapping[Role, str]) -> Guards:
    if not tokens or any(len(t) < 16 for t in tokens.values()):
        raise ValueError("need role tokens of at least 16 characters")
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("each role needs its own token")
    accepted = [(role, token.encode()) for role, token in tokens.items()]

    def caller(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> Role:
        token = ("" if credentials is None else credentials.credentials).encode()
        found: Role | None = None
        for role, expected in accepted:  # constant time per token, no early exit
            if secrets.compare_digest(token, expected):
                found = role
        if found is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return found

    def allow(*roles: Role) -> Callable[..., Role]:
        permitted = frozenset(roles)

        def check(role: Annotated[Role, Depends(caller)]) -> Role:
            if role not in permitted:
                raise HTTPException(status.HTTP_403_FORBIDDEN, f"not allowed for {role.value}")
            return role

        return check

    return Guards(
        allow(Role.homeowner),
        allow(Role.management),
        allow(Role.operator),
        allow(Role.homeowner, Role.operator),
        allow(*Role),
    )


def create_app(
    service: ApiService,
    *,
    tokens: Mapping[Role, str],
    autostart: bool = True,
    speech: GoogleSpeech | None = None,
) -> FastAPI:
    guards = _guards(tokens)

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
            if speech is not None:
                await speech.aclose()

    app = FastAPI(
        title="Penjaga Core API",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=None,
    )

    def error(code: int) -> Any:
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=code)

        return handler

    app.add_exception_handler(NoEpisodeError, error(status.HTTP_503_SERVICE_UNAVAILABLE))
    app.add_exception_handler(UpstreamUnavailableError, error(status.HTTP_502_BAD_GATEWAY))
    app.add_exception_handler(StaleConsentTextError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(UnknownIdError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))
    app.add_exception_handler(CaseNotFoundError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(CaseActionRefusedError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(ActionRefusedError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(LayerOffError, error(status.HTTP_409_CONFLICT))
    app.add_exception_handler(NoticeNotFoundError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(MaintenanceItemNotFoundError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(NoticeUnreadableError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))
    app.add_exception_handler(LLMUnavailableError, error(status.HTTP_503_SERVICE_UNAVAILABLE))
    app.add_exception_handler(SpeechUnavailableError, error(status.HTTP_503_SERVICE_UNAVAILABLE))

    _add_house_routes(app, service, guards)
    _add_case_routes(app, service, guards)
    _add_chat_routes(app, service, guards)
    _add_voice_routes(app, speech, guards)
    _add_action_routes(app, service, guards)
    _add_notice_routes(app, service, guards)
    _add_privacy_routes(app, service, guards)
    _add_event_routes(app, service, guards)
    return app


def _add_house_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, Any]:
        h = service.health()
        return {"status": h["status"], "detection_connected": h["detection_connected"]}

    @app.get(f"{PREFIX}/diagnostics")
    async def diagnostics(_: Annotated[Role, Depends(g.operator)]) -> dict[str, Any]:
        return service.health()

    @app.get(f"{PREFIX}/house")
    async def house(_: Annotated[Role, Depends(g.anyone)]) -> dict[str, Any]:
        return service.house_document()

    @app.post(f"{PREFIX}/house/validate-ids")
    async def check_ids(body: IdsRequest, _: Annotated[Role, Depends(g.anyone)]) -> dict[str, Any]:
        return {"ids": list(service.validate_ids(body.ids))}

    @app.get(f"{PREFIX}/findings")
    async def findings(
        _: Annotated[Role, Depends(g.operator)], layer: AppLayer | None = None
    ) -> list[dict[str, Any]]:
        return service.findings(None if layer is None else layer.value)


def _add_case_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    @app.get(f"{PREFIX}/view")
    async def view(_: Annotated[Role, Depends(g.app)]) -> dict[str, Any]:
        """Everything the homeowner app shows, in the shape of the app's stand-in ``View``."""
        return await service.view()

    @app.get(f"{PREFIX}/cases")
    async def cases(_: Annotated[Role, Depends(g.app)]) -> list[dict[str, Any]]:
        return await service.cases()

    @app.get(f"{PREFIX}/cases/{{case_id}}")
    async def case(case_id: str, _: Annotated[Role, Depends(g.app)]) -> dict[str, Any]:
        return await service.case(case_id)

    @app.get(f"{PREFIX}/outlook")
    async def outlooks(_: Annotated[Role, Depends(g.app)]) -> list[dict[str, Any]]:
        """Each open case's measurements carried forward ("what if they wait?"), app and Lab."""
        return await service.outlooks()

    @app.post(f"{PREFIX}/cases/{{case_id}}/contained")
    async def contained(
        case_id: str, body: ContainRequest, role: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        """The owner shut the supply off in an emergency: the case waits for the repair."""
        return await service.contain(case_id, body.what, role)

    @app.post(f"{PREFIX}/cases/{{case_id}}/dismiss")
    async def dismiss(
        case_id: str, body: DismissRequest, role: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        return await service.dismiss(case_id, body.reason, role)

    @app.get(f"{PREFIX}/live")
    async def live(_: Annotated[Role, Depends(g.app)]) -> dict[str, Any]:
        """Latest water flow (L/min) and power (kW), so the app needs no stream token."""
        return await service.live()

    @app.get(f"{PREFIX}/vendors")
    async def vendors(
        _: Annotated[Role, Depends(g.app)],
        category: Annotated[str, Query(pattern="^(plumber|electrician|aircon_service)$")],
    ) -> dict[str, Any]:
        """The illustrative vendor directory for one category, with visit slots after now."""
        return get_vendors(category, await service.house_time())

    @app.post(f"{PREFIX}/toured", status_code=status.HTTP_204_NO_CONTENT)
    async def toured(_: Annotated[Role, Depends(g.homeowner)]) -> Response:
        await service.mark_toured()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _add_action_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    actions = service.actions

    def found(record_id: str, kind: str) -> dict[str, Any]:
        try:
            return actions.ticket(record_id) if kind == "ticket" else {}
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {kind} {record_id!r}") from None

    # the owner: preview what would be sent, then confirm with the returned token
    @app.post(f"{PREFIX}/cases/{{case_id}}/actions/{{kind}}/preview")
    async def preview(
        case_id: str,
        kind: ActionKind,
        body: PreviewRequest,
        _: Annotated[Role, Depends(g.homeowner)],
    ) -> dict[str, Any]:
        return await actions.preview(case_id, kind, body.params)

    @app.post(f"{PREFIX}/cases/{{case_id}}/tickets", status_code=status.HTTP_201_CREATED)
    async def send_ticket(
        case_id: str, body: TicketRequest, _: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        params = {"note": body.note, "contact": body.contact}
        return await actions.send_ticket(case_id, params, body.confirmation)

    @app.post(f"{PREFIX}/cases/{{case_id}}/bookings", status_code=status.HTTP_201_CREATED)
    async def book(
        case_id: str, body: BookingRequest, _: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        params = {"vendor_id": body.vendor_id, "slot": body.slot}
        return await actions.book(case_id, params, body.confirmation)

    @app.post(f"{PREFIX}/cases/{{case_id}}/checks", status_code=status.HTTP_201_CREATED)
    async def start_check(
        case_id: str, body: CheckRequest, _: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        return await actions.start_check(
            case_id, {"fixture_id": body.fixture_id}, body.confirmation
        )

    # management (the portal)
    @app.get(f"{PREFIX}/manage/tickets")
    async def tickets(
        _: Annotated[Role, Depends(g.management)],
        status_filter: Annotated[str | None, Query(alias="status")] = None,
    ) -> list[dict[str, Any]]:
        return actions.tickets(status_filter)

    @app.get(f"{PREFIX}/manage/tickets/{{ticket_id}}")
    async def ticket(ticket_id: str, _: Annotated[Role, Depends(g.management)]) -> dict[str, Any]:
        return found(ticket_id, "ticket")

    @app.post(f"{PREFIX}/manage/tickets/{{ticket_id}}/status")
    async def ticket_status(
        ticket_id: str, body: StatusRequest, _: Annotated[Role, Depends(g.management)]
    ) -> dict[str, Any]:
        found(ticket_id, "ticket")
        return await actions.set_ticket_status(ticket_id, body.status)

    @app.post(f"{PREFIX}/manage/tickets/{{ticket_id}}/replies")
    async def ticket_reply(
        ticket_id: str, body: ReplyRequest, _: Annotated[Role, Depends(g.management)]
    ) -> dict[str, Any]:
        found(ticket_id, "ticket")
        return await actions.reply(ticket_id, body.text)

    # the operator (the Lab): an illustrative vendor finishes the job
    @app.post(f"{PREFIX}/bookings/{{booking_id}}/complete")
    async def complete_booking(
        booking_id: str, _: Annotated[Role, Depends(g.operator)]
    ) -> dict[str, Any]:
        try:
            return await actions.complete_booking(booking_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no booking {booking_id!r}") from None


def _add_notice_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    # management: paste a notice, check the fields Penjaga reads from it, publish
    @app.post(f"{PREFIX}/manage/notices/parse")
    async def parse_notice(
        body: NoticeText, _: Annotated[Role, Depends(g.management)]
    ) -> dict[str, Any]:
        return await service.read_notice(body.text)

    @app.post(f"{PREFIX}/manage/notices", status_code=status.HTTP_201_CREATED)
    async def publish_notice(
        body: NoticeDraft, role: Annotated[Role, Depends(g.management)]
    ) -> dict[str, Any]:
        notice = await service.publish_notice(body, actor=role)
        return {**notice, "issues": draft_issues(body, notice["posted_at"])}

    @app.get(f"{PREFIX}/manage/notices")
    async def manage_notices(_: Annotated[Role, Depends(g.management)]) -> list[dict[str, Any]]:
        return list(reversed(service.notice_docs()))

    @app.post(f"{PREFIX}/manage/notices/{{notice_id}}/withdraw")
    async def withdraw_notice(
        notice_id: str, _: Annotated[Role, Depends(g.management)]
    ) -> dict[str, Any]:
        return await service.withdraw_notice(notice_id)

    # the Lab: a notice adapted from a past public one, placed relative to house time
    @app.post(f"{PREFIX}/notices/seed", status_code=status.HTTP_201_CREATED)
    async def seed_notice(
        body: SeedNoticeRequest, _: Annotated[Role, Depends(g.operator)]
    ) -> dict[str, Any]:
        if body.preset not in preset_names():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"unknown preset {body.preset!r}; one of {', '.join(preset_names())}",
            )
        return await service.seed_notice(body.preset, body.starts_in_h)

    # the owner: every maintenance reminder (the view lists the soonest), and marking one done
    @app.get(f"{PREFIX}/maintenance")
    async def maintenance(_: Annotated[Role, Depends(g.app)]) -> list[dict[str, Any]]:
        return await service.maintenance_items()

    @app.post(f"{PREFIX}/maintenance/{{item_id}}/done", status_code=status.HTTP_201_CREATED)
    async def maintenance_done(
        item_id: str, _: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        return await service.mark_maintenance_done(item_id)


def _add_chat_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    @app.post(f"{PREFIX}/chat")
    async def chat(
        body: ChatRequest, _: Annotated[Role, Depends(g.homeowner)]
    ) -> StreamingResponse:
        """ "Ask Penjaga", answered over SSE: ``delta`` events with the reply's text, then
        ``done`` with the whole message (chips included). The reply has passed the guardrails
        before the first ``delta`` is sent."""

        async def stream() -> AsyncIterator[str]:
            yield ": thinking\n\n"
            reply = await service.chat(
                body.message, conversation_id=body.conversation_id, case_id=body.case_id
            )
            for chunk in _sentences(reply["message"]["text"]):
                yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
            yield f"event: done\ndata: {json.dumps(reply, separators=(',', ':'))}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{PREFIX}/chat/history")
    async def chat_history(
        _: Annotated[Role, Depends(g.homeowner)],
        conversation_id: Annotated[str, Query(pattern=r"^conv_[0-9a-f]{16}$")],
    ) -> list[dict[str, Any]]:
        return service.store.chat_history(conversation_id)


def _add_voice_routes(app: FastAPI, speech: GoogleSpeech | None, g: Guards) -> None:
    @app.get(f"{PREFIX}/voice")
    async def voice(_: Annotated[Role, Depends(g.app)]) -> dict[str, Any]:
        """Whether Penjaga has its own voice (Google Text-to-Speech) and which one."""
        return {"speech": speech is not None, "voice": speech.voice if speech else None}

    @app.post(f"{PREFIX}/voice/speech")
    async def say(body: SpeechRequest, _: Annotated[Role, Depends(g.app)]) -> Response:
        """A sentence Penjaga is about to say, as MP3. 503 when the voice is off or failing."""
        if speech is None:
            raise SpeechUnavailableError("Penjaga's voice is off (no GOOGLE_TTS_API_KEY)")
        try:
            audio = await speech.synthesize(body.text)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        return Response(
            audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"}
        )


def _add_privacy_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    @app.get(f"{PREFIX}/consent")
    async def consent(_: Annotated[Role, Depends(g.homeowner)]) -> list[dict[str, Any]]:
        return await service.consent()

    @app.put(f"{PREFIX}/consent/{{layer}}")
    async def put_consent(
        layer: AppLayer, body: ConsentUpdate, role: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, Any]:
        return await service.set_consent(layer, body.enabled, body.text_version, role)

    @app.get(f"{PREFIX}/consent/history")
    async def consent_history(
        _: Annotated[Role, Depends(g.homeowner)],
    ) -> list[dict[str, Any]]:
        return service.store.consent_history()

    @app.get(f"{PREFIX}/disclosures")
    async def disclosures(_: Annotated[Role, Depends(g.homeowner)]) -> list[dict[str, Any]]:
        return service.store.disclosures()

    @app.get(f"{PREFIX}/retention")
    async def retention(_: Annotated[Role, Depends(g.homeowner)]) -> dict[str, int]:
        return service.store.retention()

    @app.put(f"{PREFIX}/retention/{{data_class}}")
    async def put_retention(
        data_class: str, body: RetentionUpdate, _: Annotated[Role, Depends(g.homeowner)]
    ) -> dict[str, int]:
        if data_class not in DEFAULT_RETENTION_DAYS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown data class {data_class!r}")
        service.store.set_retention(data_class, body.days)
        return service.store.retention()

    @app.delete(f"{PREFIX}/account", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_account(_: Annotated[Role, Depends(g.homeowner)]) -> Response:
        service.store.erase_all()
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _add_event_routes(app: FastAPI, service: ApiService, g: Guards) -> None:
    @app.get(f"{PREFIX}/events")
    async def events(
        request: Request,
        role: Annotated[Role, Depends(g.anyone)],
        after: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """Server-sent events for the caller's role; ``id`` is the cursor. Resume with
        ``Last-Event-ID`` (or ``?after=``); by default only new events are sent."""
        if last_event_id is not None and last_event_id.isdigit():
            cursor = int(last_event_id)
        elif after is not None:
            cursor = after
        else:
            cursor = service.store.last_event_seq()

        async def stream() -> AsyncIterator[str]:
            nonlocal cursor
            yield ": connected\n\n"
            while not await request.is_disconnected():
                batch = await service.wait_for_events(cursor, SSE_KEEPALIVE_S)
                if not batch:
                    yield ": keep-alive\n\n"
                    continue
                for event in batch:
                    cursor = event.seq
                    if role not in EVENT_AUDIENCE.get(event.kind, frozenset({Role.operator})):
                        continue
                    data = json.dumps(
                        {"kind": event.kind, "house_time": event.house_time, **event.payload},
                        separators=(",", ":"),
                    )
                    yield f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
