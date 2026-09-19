"""
Sarvam TTS as a real LiveKit `tts.TTS` plugin.

PORTED from Voice-AI-Agent-master's `SarvamTTSService` (batch HTTP,
`app.text-to-speech`) and `SarvamTTSStreamingService` (per-turn WebSocket,
`api.sarvam.ai/text-to-speech/ws`). Digit-spelling (`spell_digits`, ported
verbatim as `app/context/num_to_words.py`) is applied before every synthesis
call, batch or streaming — Sarvam's own documented behavior is to mis-speak
or drop bare digits, especially Devanagari numerals, and the source's fix
for that is preserved exactly rather than dropped as "cosmetic."

VERIFICATION STATUS:
- SDK/API verified: livekit.agents.tts base classes (TTS, ChunkedStream,
  SynthesizeStream, AudioEmitter, TTSCapabilities) inspected via
  `inspect.signature`/`inspect.getsource` against the real installed
  `livekit-agents` package; Deepgram's real plugin read as the reference
  pattern for `output_emitter` usage (`initialize`/`start_segment`/`push`/
  `end_segment`/`flush`).
- Adapter tested locally: unit tests (tests/test_sarvam_tts.py) mock the
  HTTP/WS boundary and verify request shape, digit-spelling application,
  and empty/failed-response handling (never fabricates audio bytes on
  failure).
- Live Sarvam call tested: NOT DONE — same constraint as the STT plugin
  (see its module docstring). Correct against the real protocol, not yet
  run against the real service.
"""
from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import urlencode

import aiohttp

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
    utils,
)

from app.context.num_to_words import spell_digits

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_TTS_WS = "wss://api.sarvam.ai/text-to-speech/ws"
TTS_CHAR_LIMIT = 450
NUM_CHANNELS = 1


def _truncate(text: str) -> str:
    return text[:TTS_CHAR_LIMIT]


class TTS(tts.TTS):
    def __init__(
        self, *, api_key: str, model: str = "bulbul:v2", speaker: str = "anushka",
        target_language: str = "en-IN", sample_rate: int = 22050,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._api_key = api_key
        self._model = model
        self._speaker = speaker
        self._language = target_language
        self._session: aiohttp.ClientSession | None = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def update_language(self, language: str) -> None:
        """Called by the agent when LID (romanized_lid.py or Sarvam's own
        detection) determines the conversation's active language has
        changed — same 'Feature 7' behavior as the source's
        LanguageDetectedFrame handling."""
        self._language = language

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "ChunkedStream":
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> "SynthesizeStream":
        return SynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        pass


class ChunkedStream(tts.ChunkedStream):
    """Batch path — ported from SarvamTTSService._call_tts_api."""

    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        spoken = spell_digits(self._input_text, self._tts._language)
        tts_text = _truncate(spoken)

        try:
            async with self._tts._ensure_session().post(
                SARVAM_TTS_URL,
                headers={"api-subscription-key": self._tts._api_key, "Content-Type": "application/json"},
                json={
                    "inputs": [tts_text],
                    "target_language_code": self._tts._language,
                    "speaker": self._tts._speaker,
                    "model": self._tts._model,
                },
                timeout=aiohttp.ClientTimeout(total=30, sock_connect=self._conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIStatusError(message=body[:200], status_code=resp.status, request_id=None, body=None)
                resp_json = await resp.json()

        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except APIStatusError:
            raise
        except Exception as e:
            raise APIConnectionError() from e

        audios_b64 = [a for a in (resp_json.get("audios") or []) if a]
        if not audios_b64:
            # Never fabricate audio — an empty/failed response yields no
            # audio output at all, not silent fake bytes.
            raise APIStatusError(message="Sarvam TTS returned no audio", status_code=200, request_id=None, body=None)

        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=self._tts.sample_rate,
            num_channels=NUM_CHANNELS, mime_type="audio/wav",
        )
        for audio_b64 in audios_b64:
            output_emitter.push(base64.b64decode(audio_b64))
        output_emitter.flush()


class SynthesizeStream(tts.SynthesizeStream):
    """Streaming path — ported from SarvamTTSStreamingService. One WS
    connection per stream lifetime (matching the source's own per-turn
    connection design, not a simplification of it)."""

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions):
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=self._tts.sample_rate,
            num_channels=NUM_CHANNELS, mime_type="audio/pcm", stream=True,
        )
        segment_id = utils.shortuuid()
        output_emitter.start_segment(segment_id=segment_id)

        url = f"{SARVAM_TTS_WS}?{urlencode({'model': self._tts._model, 'send_completion_event': 'true'})}"

        try:
            session = self._tts._ensure_session()
            ws = await session.ws_connect(url, headers={"Api-Subscription-Key": self._tts._api_key})
        except Exception as e:
            raise APIConnectionError() from e

        async def sender() -> None:
            async for data in self._input_ch:
                if isinstance(data, str):
                    spoken = spell_digits(data, self._tts._language)
                    tts_text = _truncate(spoken)
                    if tts_text.strip():
                        await ws.send_str(json.dumps({"type": "text", "data": {"text": tts_text}}))
                else:  # flush sentinel
                    await ws.send_str(json.dumps({"type": "flush"}))

        try:
            await ws.send_str(json.dumps({
                "type": "config",
                "data": {
                    "model": self._tts._model,
                    "target_language_code": self._tts._language,
                    "speaker": self._tts._speaker,
                    "output_audio_codec": "linear16",
                    "speech_sample_rate": self._tts.sample_rate,
                },
            }))
            sender_task = asyncio.create_task(sender())

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                ev = json.loads(msg.data)
                etype = ev.get("type")
                if etype == "audio":
                    b64 = (ev.get("data") or {}).get("audio", "")
                    if b64:
                        output_emitter.push(base64.b64decode(b64))
                elif etype == "event" and (ev.get("data") or {}).get("event_type") == "final":
                    break
                elif etype in {"complete", "completed"}:
                    break
                elif etype == "error":
                    raise APIStatusError(message=json.dumps(ev)[:200], status_code=-1, request_id=None, body=None)

            if not sender_task.done():
                sender_task.cancel()

        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except APIStatusError:
            raise
        except Exception as e:
            raise APIConnectionError() from e
        finally:
            output_emitter.end_segment()
            try:
                await ws.close()
            except Exception:
                pass
