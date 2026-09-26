"""
Sarvam STT as a real LiveKit `stt.STT` plugin.

PORTED, not reinvented, from Voice-AI-Agent-master's `SarvamSTTService`
(batch HTTP) and `SarvamSTTStreamingService` (per-utterance WebSocket) in
app/pipecat_pipeline.py. URLs, request/response shapes, and the documented
fail-safe design (any streaming failure falls back to the exact same batch
call) are carried over deliberately rather than redesigned â€” that fallback
behavior is a real, load-bearing reliability property of the original code,
not incidental.

Key fact carried over from the source, stated there explicitly and preserved
here: Sarvam's streaming response is a FINALIZED transcript per utterance,
not incremental word-by-word partials. This plugin therefore only ever emits
`FINAL_TRANSCRIPT` events â€” never fabricates `INTERIM_TRANSCRIPT` events that
the real API doesn't provide. `STTCapabilities.interim_results=False`
reflects this honestly rather than claiming a capability that isn't real.

Romanized-language override (app/context/romanized_lid.py) is applied to
every transcript, batch or streaming, before the language code reaches the
caller â€” this is what lets "kya aap mujhe Hyderabad ka weather batao sakte
hain" correctly resolve to hi-IN even though Sarvam's own auto-detect
returns en-IN for Latin-script text.

VERIFICATION STATUS (per the instruction to distinguish these explicitly):
- SDK/API verified: livekit.agents.stt base classes (STT, SpeechStream,
  SpeechEvent, SpeechData, STTCapabilities) inspected via `inspect.signature`
  / `inspect.getsource` against the real installed `livekit-agents` package.
  Deepgram's real plugin (also installed) was read as the reference pattern
  for how a conforming plugin implements `_recognize_impl` / `stream()` /
  `SpeechStream._run()`.
- Adapter tested locally: unit tests exist (see tests/test_sarvam_stt.py)
  that mock the HTTP/WS boundary and verify request shape, response
  parsing, romanized-override application, and the batch-fallback-on-
  streaming-failure path.
- Live Sarvam call tested: NOT DONE. No SARVAM_API_KEY exists in this
  environment and no network path to api.sarvam.ai exists from this
  sandbox. This code is correct against the real, documented protocol
  (extracted from working source, not guessed), but has not made a single
  real network call to Sarvam.
"""
from __future__ import annotations
from typing import Any

import asyncio
import base64
import json
import time
from urllib.parse import urlencode

import aiohttp

from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    DEFAULT_API_CONNECT_OPTIONS,
    stt,
    utils,
)
from livekit.agents import LanguageCode
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

from app.context.romanized_lid import apply_romanized_override

SARVAM_ASR_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_STT_WS = "wss://api.sarvam.ai/speech-to-text/ws"
# Same bound as the source â€” never block a turn indefinitely on a flush.
FINALIZE_TIMEOUT_SECS = 3.0


class STT(stt.STT):
    def __init__(self, *, api_key: str, model: str = "saaras:v3", mode: str | None = None):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                # Honest, not aspirational: see module docstring â€” Sarvam's
                # streaming response is finalized-per-utterance, not
                # word-by-word partials.
                interim_results=False,
            )
        )
        self._api_key = api_key
        self._model = model
        self._mode = mode
        self._session: aiohttp.ClientSession | None = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        """Batch path â€” ported from SarvamSTTService._transcribe. Always
        sends language_code="unknown" and lets Sarvam auto-detect, exactly
        as the source does, because locking to one language breaks
        mid-conversation switching (the source's own stated reason)."""
        wav_bytes = rtc.combine_audio_frames(buffer).to_wav_bytes()

        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self._model)
        form.add_field("language_code", "unknown")
        form.add_field("with_disfluencies", "false")
        if self._mode and self._model.startswith("saaras"):
            form.add_field("mode", self._mode)

        try:
            async with self._ensure_session().post(
                SARVAM_ASR_URL,
                headers={"api-subscription-key": self._api_key},
                data=form,
                timeout=aiohttp.ClientTimeout(total=30, sock_connect=conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIStatusError(
                        message=body[:200], status_code=resp.status, request_id=None, body=None
                    )
                resp_json = await resp.json()

        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except APIStatusError:
            raise
        except Exception as e:
            raise APIConnectionError() from e

        transcript = (resp_json.get("transcript") or "").strip()
        raw_lang = resp_json.get("language_code", "en")
        confidence = float(resp_json.get("confidence", 1.0))
        normalized_lang = apply_romanized_override(transcript, raw_lang)

        return _build_speech_event(transcript, normalized_lang, confidence)

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "SpeechStream":
        return SpeechStream(
            stt=self, api_key=self._api_key, model=self._model, mode=self._mode, conn_options=conn_options
        )

    async def aclose(self) -> None:
        # session is process-shared (utils.http_context), do not close it here
        pass


def _build_speech_event(transcript: str, language: str, confidence: float) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[stt.SpeechData(language=LanguageCode(language), text=transcript, confidence=confidence)],
    )


class SpeechStream(stt.SpeechStream):
    """Streaming Sarvam STT over a per-utterance WebSocket â€” ported from
    SarvamSTTStreamingService. Opens a fresh WS connection per utterance
    (matching the source's actual design, not a simplification of it: the
    source itself never keeps one long-lived connection across utterances).
    Any connect failure, mid-utterance drop, or finalize timeout falls back
    to the batch HTTP path using the same buffered audio â€” reliability can
    never regress below the pre-streaming (batch-only) behavior, exactly as
    documented in the source."""

    def __init__(self, *, stt: STT, api_key: str, model: str, mode: str | None, conn_options: APIConnectOptions):
        super().__init__(stt=stt, conn_options=conn_options)
        self._api_key = api_key
        self._model = model
        self._mode = mode
        self._batch_stt = stt  # reuse the same STT instance's _recognize_impl for fallback

    async def _run(self) -> None:
        """Single pass over `self._input_ch`, handling one or more
        utterances sequentially. Deliberately NOT wrapped in a `while True`
        retry loop around the whole connect+process sequence â€” an earlier
        version of this method had exactly that bug: if `ws_connect` failed
        before any frame was ever read, `has_ended` never became True, the
        fallback branch never ran, and the loop retried the same failing
        connect forever with no timeout and no emitted event. Caught by
        tests/test_sarvam_stt.py::test_streaming_falls_back_to_batch_on_ws_connect_failure
        hanging instead of passing. Fixed by connecting lazily per-utterance
        inside the single top-level loop over `_input_ch`, so the loop's own
        termination (when the channel closes) is what ends `_run()` â€” never
        an unbounded retry."""
        ws: aiohttp.ClientWebSocketResponse[Any] | None = None
        recv_task: asyncio.Task | None = None
        got_result = asyncio.Event()
        latest_result: dict | None = None
        stream_failed = False
        buffered_frames: list[rtc.AudioFrame] = []
        utterance_started = False

        session = self._batch_stt._ensure_session()

        params = {
            "language-code": "unknown",
            "model": self._model,
            "sample_rate": "16000",
            "input_audio_codec": "pcm_s16le",
            "flush_signal": "true",
        }
        if self._mode and self._model.startswith("saaras"):
            params["mode"] = self._mode
        url = f"{SARVAM_STT_WS}?{urlencode(params)}"

        async def receiver(sock: aiohttp.ClientWebSocketResponse[Any]) -> None:
            nonlocal latest_result, stream_failed
            async for msg in sock:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    ev = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "data":
                    data = ev.get("data") or {}
                    latest_result = {
                        "transcript": data.get("transcript", ""),
                        "language_code": data.get("language_code"),
                    }
                    got_result.set()
                    return
                elif etype == "error":
                    stream_failed = True
                    got_result.set()
                    return

        resampler: Any | None = None
        current_sample_rate: int | None = None

        async for data in self._input_ch:
            if isinstance(data, rtc.AudioFrame):
                if not utterance_started:
                    utterance_started = True
                    self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
                    got_result = asyncio.Event()
                    latest_result = None
                    stream_failed = False
                    buffered_frames = []
                    resampler = None
                    current_sample_rate = None
                    try:
                        ws = await session.ws_connect(url, headers={"Api-Subscription-Key": self._api_key})
                        recv_task = asyncio.create_task(receiver(ws))
                    except Exception:
                        ws = None
                        stream_failed = True

                buffered_frames.append(data)

                # Resample to 16kHz for Sarvam STT WebSocket if needed
                if data.sample_rate != 16000 or data.num_channels != 1:
                    if resampler is None or current_sample_rate != data.sample_rate:
                        if hasattr(rtc, "AudioResampler"):
                            try:
                                resampler = rtc.AudioResampler(
                                    input_rate=data.sample_rate,
                                    output_rate=16000,
                                    num_channels=1,
                                )
                                current_sample_rate = data.sample_rate
                            except Exception:
                                resampler = None
                    if resampler is not None:
                        frames_to_send = resampler.push(data)
                    else:
                        frames_to_send = [data]
                else:
                    frames_to_send = [data]

                if ws is not None and not stream_failed:
                    for frame in frames_to_send:
                        try:
                            pcm_bytes = frame.data.tobytes()
                            await ws.send_str(json.dumps({
                                "audio": {
                                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                                    "sample_rate": "16000",
                                    "encoding": "audio/pcm_s16le",
                                }
                            }))
                        except Exception:
                            stream_failed = True
                            break

            else:  # flush sentinel — end of this utterance
                if not utterance_started:
                    continue  # flush with no preceding audio — nothing to finalize

                if resampler is not None and ws is not None and not stream_failed:
                    try:
                        flushed_frames = resampler.flush()
                        for frame in flushed_frames:
                            pcm_bytes = frame.data.tobytes()
                            await ws.send_str(json.dumps({
                                "audio": {
                                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                                    "sample_rate": "16000",
                                    "encoding": "audio/pcm_s16le",
                                }
                            }))
                    except Exception:
                        stream_failed = True

                if ws is not None and not stream_failed:
                    try:
                        await ws.send_str(json.dumps({"type": "flush"}))
                        await asyncio.wait_for(got_result.wait(), timeout=FINALIZE_TIMEOUT_SECS)
                    except Exception:
                        stream_failed = True

                if recv_task is not None and not recv_task.done():
                    recv_task.cancel()
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                ws = None

                self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))

                if not stream_failed and latest_result is not None:
                    transcript = latest_result["transcript"].strip()
                    raw_lang = latest_result.get("language_code") or "en"
                    normalized_lang = apply_romanized_override(transcript, raw_lang)
                    self._event_ch.send_nowait(_build_speech_event(transcript, normalized_lang, confidence=0.0))
                else:
                    # Fail-safe fallback â€” same guarantee as the source: use
                    # the buffered real AudioFrame list directly as the
                    # AudioBuffer _recognize_impl expects (it internally
                    # calls rtc.combine_audio_frames() + .to_wav_bytes()).
                    try:
                        if buffered_frames:
                            event = await self._batch_stt._recognize_impl(
                                buffered_frames, conn_options=self._conn_options
                            )
                            self._event_ch.send_nowait(event)
                    except Exception:
                        # Total failure â€” never fabricate a transcript. The
                        # caller sees no FINAL_TRANSCRIPT event for this
                        # utterance rather than a fake empty one.
                        pass

                utterance_started = False
                buffered_frames = []

        # Input channel closed (end_input() called). Clean up any still-open
        # connection from an utterance that never got its flush sentinel.
        if recv_task is not None and not recv_task.done():
            recv_task.cancel()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass









