"""Penjaga's voice: Google Cloud Text-to-Speech over plain HTTP (checked 2026-10-20).

- ``POST https://texttospeech.googleapis.com/v1/text:synthesize``
  (https://docs.cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize). The API
  key goes in the ``X-Goog-Api-Key`` header, never the URL, so it can't end up in a log line.
- Voice: a Chirp 3: HD voice (https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd),
  named ``<locale>-Chirp3-HD-<voice>``; the locale is read from the name. MP3 out: small, and every
  browser decodes it.
- Input is plain ``text``: at most 5000 bytes per request (SynthesisInput reference); the app
  sends one or two sentences at a time so the first one plays while the next is made.
- Custom pronunciations are en-US only, so units and ringgit are written out the way they are
  said (:func:`spoken`) before the text goes out.
- Privacy: the text is something Penjaga is about to say on the owner's screen. It is never
  logged (only its length), and the request carries no household identifier.
- Errors: 429 and 5xx are retried once; anything else raises :class:`SpeechUnavailableError`, and
  the app falls back to the browser's own voice.
- The same sentence (an urgent prompt, a greeting) is cached in memory, so it costs one request.

Configuration (backend ``.env`` only):

GOOGLE_TTS_API_KEY      an API key restricted to the Text-to-Speech API (never logged or printed)
PENJAGA_TTS_VOICE       the voice (default ``en-GB-Chirp3-HD-Aoede``)
PENJAGA_TTS_RATE        speaking rate, 0.25 to 2.0 (default 1.0)
PENJAGA_TTS_TIMEOUT_S   per-request timeout in seconds (default 15)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass

import httpx2

log = logging.getLogger("penjaga_api.voice")

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_VOICE = "en-GB-Chirp3-HD-Aoede"
MAX_TEXT_BYTES = 5000  # Text-to-Speech's limit per request
MAX_TEXT_CHARS = 600  # what the app may send at once: a sentence or two
RETRYABLE = frozenset({429, 500, 502, 503, 504})
CACHE_ENTRIES = 96
CACHE_BYTES = 12 * 1024 * 1024
VOICE_NAME = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$")


class SpeechUnavailableError(RuntimeError):
    """Penjaga can't speak right now (off, refused, failed): the app uses the browser's voice."""


@dataclass(frozen=True)
class VoiceConfig:
    api_key: str | None
    voice: str = DEFAULT_VOICE
    rate: float = 1.0
    timeout_s: float = 15.0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def language(self) -> str:
        """``en-GB`` from ``en-GB-Chirp3-HD-Aoede``."""
        return "-".join(self.voice.split("-")[:2])

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> VoiceConfig:
        e = os.environ if env is None else env
        voice = e.get("PENJAGA_TTS_VOICE", "").strip() or DEFAULT_VOICE
        if not VOICE_NAME.match(voice):
            raise ValueError(
                f"PENJAGA_TTS_VOICE {voice!r} is not a voice name like {DEFAULT_VOICE}"
            )
        rate = float(e.get("PENJAGA_TTS_RATE", "") or 1.0)
        if not 0.25 <= rate <= 2.0:
            raise ValueError("PENJAGA_TTS_RATE must be between 0.25 and 2.0")
        return cls(
            api_key=e.get("GOOGLE_TTS_API_KEY", "").strip() or None,
            voice=voice,
            rate=rate,
            timeout_s=float(e.get("PENJAGA_TTS_TIMEOUT_S", "") or 15.0),
        )

    def __repr__(self) -> str:  # never show the key
        key = "set" if self.api_key else "unset"
        return f"VoiceConfig(api_key={key}, voice={self.voice!r}, rate={self.rate})"


# ---------------------------------------------------------------------- spoken form

_UNITS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bL/min\b"), "litres a minute"),
    (re.compile(r"\bL/h\b"), "litres an hour"),
    (re.compile(r"\bkWh\b"), "kilowatt hours"),
    (re.compile(r"\bkW\b"), "kilowatts"),
    (re.compile(r"\bmA\b"), "milliamps"),
    (re.compile(r"m³"), "cubic metres"),
    (re.compile(r"°C\b"), " degrees"),
    (re.compile(r"(?<=\d)\s?A\b"), " amps"),
    (re.compile(r"(?<=\d)\s?W\b"), " watts"),
    (re.compile(r"(?<=\d)\s?L\b"), " litres"),
    (re.compile(r"(?<=\d)\s?hp\b"), " horsepower"),
]
_RINGGIT = re.compile(r"\bRM\s?(\d[\d,]*)(?:\.(\d{2}))?")
_SPACES = re.compile(r"\s+")
_DOT = re.compile(r"\s*·\s*")


def _ringgit(m: re.Match[str]) -> str:
    whole = m.group(1).replace(",", "")
    sen = m.group(2)
    words = f"{int(whole):,} ringgit"
    if sen and int(sen):
        words += f" {int(sen)} sen"
    return words


def spoken(text: str) -> str:
    """``text`` the way it is said: ``RM41.20`` is "41 ringgit 20 sen", ``0.5 L/min`` is "0.5
    litres a minute", and so on (the voice reads symbols and units letter by letter)."""
    out = _RINGGIT.sub(_ringgit, text)
    out = _DOT.sub(", ", out).replace("\u2013", " to ").replace("\u2014", ", ")
    for pattern, words in _UNITS:
        out = pattern.sub(words, out)
    return _SPACES.sub(" ", out).strip()


# ---------------------------------------------------------------------- client


class _Cache:
    """A small LRU of synthesised audio by (voice, rate, text), bounded by entries and bytes."""

    def __init__(self, entries: int = CACHE_ENTRIES, max_bytes: int = CACHE_BYTES) -> None:
        self._items: OrderedDict[tuple[str, float, str], bytes] = OrderedDict()
        self._entries = entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: tuple[str, float, str]) -> bytes | None:
        audio = self._items.get(key)
        if audio is not None:
            self._items.move_to_end(key)
        return audio

    def put(self, key: tuple[str, float, str], audio: bytes) -> None:
        if len(audio) > self._max_bytes or key in self._items:
            return
        self._items[key] = audio
        self._bytes += len(audio)
        while len(self._items) > self._entries or self._bytes > self._max_bytes:
            _, old = self._items.popitem(last=False)
            self._bytes -= len(old)


class GoogleSpeech:
    """Text in, MP3 out."""

    def __init__(
        self, config: VoiceConfig, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        if not config.api_key:
            raise ValueError("GoogleSpeech needs GOOGLE_TTS_API_KEY")
        self.config = config
        self._http = httpx2.AsyncClient(timeout=config.timeout_s, transport=transport)
        self._cache = _Cache()
        self._inflight: dict[tuple[str, float, str], asyncio.Task[bytes]] = {}

    @property
    def voice(self) -> str:
        return self.config.voice

    async def synthesize(self, text: str) -> bytes:
        said = spoken(text)
        if not said:
            raise ValueError("nothing to say")
        if len(said.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("text too long for one request")
        key = (self.config.voice, self.config.rate, said)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        # the same sentence asked twice at once (two tabs, a quick repeat) is made once
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._request(said))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        audio = await task
        self._cache.put(key, audio)
        return audio

    async def _request(self, said: str) -> bytes:
        assert self.config.api_key is not None
        body = {
            "input": {"text": said},
            "voice": {"languageCode": self.config.language, "name": self.config.voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": self.config.rate},
        }
        headers = {"X-Goog-Api-Key": self.config.api_key, "Content-Type": "application/json"}
        for attempt in (1, 2):
            try:
                r = await self._http.post(ENDPOINT, json=body, headers=headers)
            except httpx2.HTTPError as exc:
                if attempt == 1:
                    await asyncio.sleep(0.4)
                    continue
                raise SpeechUnavailableError(f"network: {type(exc).__name__}") from exc
            if r.status_code in RETRYABLE and attempt == 1:
                await asyncio.sleep(0.4)
                continue
            if r.status_code != 200:
                raise SpeechUnavailableError(f"http {r.status_code}: {_reason(r)}")
            try:
                audio = base64.b64decode(r.json()["audioContent"], validate=True)
            except (ValueError, KeyError, TypeError) as exc:
                raise SpeechUnavailableError("unreadable response") from exc
            if not audio:
                raise SpeechUnavailableError("empty audio")
            log.info("spoke %d characters (%d bytes of audio)", len(said), len(audio))
            return audio
        raise SpeechUnavailableError("retries exhausted")  # pragma: no cover

    async def aclose(self) -> None:
        await self._http.aclose()


def _reason(r: httpx2.Response) -> str:
    """Google's error reason (e.g. API_KEY_INVALID), never the request or the key."""
    try:
        err = r.json().get("error", {})
        reasons = [d.get("reason") for d in err.get("details", []) if d.get("reason")]
        return str(reasons[0] if reasons else err.get("status", "error"))
    except ValueError:
        return "error"
