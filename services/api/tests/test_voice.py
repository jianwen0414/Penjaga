"""Penjaga's voice: the Text-to-Speech client over a mocked transport, the spoken form of numbers
and units, and the routes (roles, limits, the browser-voice fallback when it's off)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from penjaga_api.api import create_app
from penjaga_api.service import ApiService, Role
from penjaga_api.store import ApiStore
from penjaga_api.testing import FakeUpstream
from penjaga_api.voice import (
    DEFAULT_VOICE,
    GoogleSpeech,
    SpeechUnavailableError,
    VoiceConfig,
    spoken,
)

KEY = "AIza-test-not-a-real-key"
CONFIG = VoiceConfig(api_key=KEY)
MP3 = b"ID3\x04fake-mp3-bytes"
OK = {"audioContent": base64.b64encode(MP3).decode()}
TOKENS = {
    Role.homeowner: "homeowner-token-0123456789",
    Role.management: "management-token-0123456789",
    Role.operator: "operator-token-0123456789",
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    real = asyncio.sleep

    async def fast(seconds: float) -> None:
        await real(0)

    monkeypatch.setattr(asyncio, "sleep", fast)


def speech(responses: list[httpx2.Response], seen: list[httpx2.Request]) -> GoogleSpeech:
    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return responses.pop(0)

    return GoogleSpeech(CONFIG, transport=httpx2.MockTransport(handle))


# ---------------------------------------------------------------------- config


def test_config_reads_the_environment_and_never_shows_the_key() -> None:
    c = VoiceConfig.from_env({"GOOGLE_TTS_API_KEY": KEY, "PENJAGA_TTS_RATE": "1.1"})
    assert c.enabled
    assert (c.voice, c.language, c.rate) == (DEFAULT_VOICE, "en-GB", 1.1)
    assert KEY not in repr(c)
    assert "api_key=set" in repr(c)
    assert not VoiceConfig.from_env({}).enabled


@pytest.mark.parametrize(
    "env",
    [{"PENJAGA_TTS_VOICE": "Aoede"}, {"PENJAGA_TTS_RATE": "3"}, {"PENJAGA_TTS_RATE": "0.1"}],
)
def test_config_refuses_a_bad_voice_or_rate(env: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="PENJAGA_TTS_"):
        VoiceConfig.from_env(env)


def test_the_locale_comes_from_the_voice_name() -> None:
    assert VoiceConfig(api_key=KEY, voice="en-IN-Chirp3-HD-Kore").language == "en-IN"


# ---------------------------------------------------------------------- spoken form


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("about RM41.20 a month", "about 41 ringgit 20 sen a month"),
        ("Over a year: about RM1,494.", "Over a year: about 1,494 ringgit."),
        ("RM 80.00", "80 ringgit"),
        ("0.5 L/min at night", "0.5 litres a minute at night"),
        ("About 720 L a day", "About 720 litres a day"),
        ("12.4 mA on a 30 mA switch", "12.4 milliamps on a 30 milliamps switch"),
        ("25 A on a 20 A breaker", "25 amps on a 20 amps breaker"),
        ("3.2 kWh · 31.5°C", "3.2 kilowatt hours, 31.5 degrees"),
        ("10:00\u201315:00", "10:00 to 15:00"),
        ("Bathroom 2 toilet", "Bathroom 2 toilet"),
    ],
)
def test_numbers_and_units_are_written_out_the_way_they_are_said(text: str, said: str) -> None:
    assert spoken(text) == said


# ---------------------------------------------------------------------- client


def test_a_request_sends_text_voice_and_mp3_with_the_key_in_a_header() -> None:
    seen: list[httpx2.Request] = []
    s = speech([httpx2.Response(200, json=OK)], seen)
    assert asyncio.run(s.synthesize("About RM41.20 a month.")) == MP3
    (request,) = seen
    assert request.headers["X-Goog-Api-Key"] == KEY
    assert KEY not in str(request.url)  # never in the URL, so never in a log
    body = json.loads(request.content)
    assert body["input"] == {"text": "About 41 ringgit 20 sen a month."}
    assert body["voice"] == {"languageCode": "en-GB", "name": DEFAULT_VOICE}
    assert body["audioConfig"] == {"audioEncoding": "MP3", "speakingRate": 1.0}


def test_the_same_sentence_is_made_once() -> None:
    seen: list[httpx2.Request] = []
    s = speech([httpx2.Response(200, json=OK)], seen)

    async def twice() -> tuple[bytes, bytes, bytes]:
        a, b = await asyncio.gather(
            s.synthesize("Where are you now?"), s.synthesize("Where are you now?")
        )
        return a, b, await s.synthesize("Where are you now?")

    assert asyncio.run(twice()) == (MP3, MP3, MP3)
    assert len(seen) == 1


def test_a_busy_service_is_retried_once() -> None:
    seen: list[httpx2.Request] = []
    s = speech([httpx2.Response(503), httpx2.Response(200, json=OK)], seen)
    assert asyncio.run(s.synthesize("Hello.")) == MP3
    assert len(seen) == 2


@pytest.mark.parametrize(
    "responses",
    [
        [
            httpx2.Response(
                400,
                json={
                    "error": {
                        "status": "INVALID_ARGUMENT",
                        "details": [{"reason": "API_KEY_INVALID"}],
                    }
                },
            )
        ],
        [httpx2.Response(503), httpx2.Response(503)],
        [httpx2.Response(200, json={"nothing": True})],
        [httpx2.Response(200, json={"audioContent": ""})],
        [httpx2.Response(200, text="not json")],
    ],
)
def test_failures_say_the_voice_is_unavailable_without_the_key(
    responses: list[httpx2.Response],
) -> None:
    s = speech(responses, [])
    with pytest.raises(SpeechUnavailableError) as exc:
        asyncio.run(s.synthesize("Hello."))
    assert KEY not in str(exc.value)


def test_a_bad_key_is_named_by_googles_reason() -> None:
    body = {"error": {"status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}}
    s = speech([httpx2.Response(400, json=body)], [])
    with pytest.raises(SpeechUnavailableError, match="API_KEY_INVALID"):
        asyncio.run(s.synthesize("Hello."))


def test_network_errors_are_retried_then_unavailable() -> None:
    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ConnectError("offline", request=request)

    s = GoogleSpeech(CONFIG, transport=httpx2.MockTransport(handle))
    with pytest.raises(SpeechUnavailableError, match="network"):
        asyncio.run(s.synthesize("Hello."))
    assert calls == 2


def test_nothing_to_say_is_refused() -> None:
    s = speech([], [])
    with pytest.raises(ValueError, match="nothing to say"):
        asyncio.run(s.synthesize("   "))


def test_it_needs_a_key() -> None:
    with pytest.raises(ValueError, match="GOOGLE_TTS_API_KEY"):
        GoogleSpeech(VoiceConfig(api_key=None))


# ---------------------------------------------------------------------- routes


def _h(role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ApiStore]:
    s = ApiStore(tmp_path / "api.sqlite")
    yield s
    s.close()


def app(store: ApiStore, s: GoogleSpeech | None) -> TestClient:
    service = ApiService(store, FakeUpstream(episode_start="2025-11-26T00:00:00+08:00"))
    return TestClient(create_app(service, tokens=TOKENS, autostart=False, speech=s))


def test_speech_route_returns_mp3_for_the_app_roles_only(store: ApiStore) -> None:
    client = app(store, speech([httpx2.Response(200, json=OK) for _ in range(2)], []))
    for role in (Role.homeowner, Role.operator):
        r = client.post(
            "/api/v1/voice/speech", json={"text": f"Hello {role.value}."}, headers=_h(role)
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"
        assert r.content == MP3
    r = client.post("/api/v1/voice/speech", json={"text": "Hello."}, headers=_h(Role.management))
    assert r.status_code == 403
    assert client.post("/api/v1/voice/speech", json={"text": "Hello."}).status_code == 401


def test_speech_route_limits_the_text(store: ApiStore) -> None:
    client = app(store, speech([], []))
    h = _h(Role.homeowner)
    assert client.post("/api/v1/voice/speech", json={"text": ""}, headers=h).status_code == 422
    long = {"text": "word " * 200}
    assert client.post("/api/v1/voice/speech", json=long, headers=h).status_code == 422
    assert client.post("/api/v1/voice/speech", json={"text": "  "}, headers=h).status_code == 422


def test_without_a_key_the_app_is_told_to_use_the_browser_voice(store: ApiStore) -> None:
    client = app(store, None)
    h = _h(Role.homeowner)
    assert client.get("/api/v1/voice", headers=h).json() == {"speech": False, "voice": None}
    r = client.post("/api/v1/voice/speech", json={"text": "Hello."}, headers=h)
    assert r.status_code == 503


def test_a_failing_voice_is_a_503(store: ApiStore) -> None:
    client = app(store, speech([httpx2.Response(403), httpx2.Response(403)], []))
    h = _h(Role.homeowner)
    assert client.get("/api/v1/voice", headers=h).json() == {"speech": True, "voice": DEFAULT_VOICE}
    assert (
        client.post("/api/v1/voice/speech", json={"text": "Hello."}, headers=h).status_code == 503
    )
