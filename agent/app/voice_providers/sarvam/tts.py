"""
Sarvam TTS as a real LiveKit `tts.TTS` plugin.

Streaming path uses Sarvam's official `sarvamai` SDK
(`AsyncSarvamAI().text_to_speech_streaming.connect(...)`) rather than a
hand-rolled WebSocket client -- confirmed against the real installed SDK
(`sarvamai.text_to_speech_streaming.socket_client`), not just its docs.

FIXED (previously silent "no audio frames were pushed" failure):
1. The socket's `TextToSpeechStreamingSocketClientResponse` union is
   `AudioOutput | ErrorResponse | EventResponse`, but the receive loop
   only ever checked for the first and last of those. A server-side
   `ErrorResponse` (bad config, invalid text, etc.) was silently dropped,
   the loop then ran out on connection close, and the base `tts.py`
   surfaced that as a generic "no audio frames were pushed for text: ..."
   with the real reason gone. Now raises `APIStatusError` with Sarvam's
   own error message/code.
2. `ws.configure()` was never given `speech_sample_rate`, so it silently
   used the SDK's own default (22050 Hz) while `output_emitter.initialize`
   declared `self._tts.sample_rate` (24000 Hz by default here) -- a real
   sample-rate mismatch between what we told LiveKit to expect and what
   Sarvam actually sent. Now explicit and kept in sync with the emitter.
3. `min_buffer_size` defaults to 50 chars (left at the SDK's default --
   see the inline comment in `_run` for why a lowered value was tried
   and reverted), so a short reply (a greeting under 50 characters, for
   example) could sit in Sarvam's server-side buffer unless a `flush`
   happened to land after it. The `sender()` coroutine only forwarded a
   `flush` when the LiveKit input channel handed us an explicit flush
   sentinel -- if the last chunk of text closed the channel without one,
   nothing ever told Sarvam to drain the remainder. Now the `sender()`
   coroutine unconditionally flushes once more right after the input
   channel is exhausted, so the trailing buffered text is always
   processed regardless of what the caller sent.
4. `sender()` only skipped a chunk from LiveKit's LLM->TTS streaming if it
   was empty after `.strip()`. Confirmed against a real production run
   that the streaming tokenizer can hand us a trailing chunk that's pure
   punctuation (a lone "."), which is non-empty and sailed through that
   check straight to `ws.convert()`. A message with zero letters/digits
   in it is exactly what Sarvam's "Text must contain at least one
   character from the allowed languages" (422) means, and it fired on
   effectively every real response. Now skips any chunk with no
   alphanumeric character at all -- punctuation-only chunks were never
   audibly rendered anyway, so nothing is lost by not sending them.

Batch path (ChunkedStream) is unchanged -- it was never the one failing.

Digit-spelling (`spell_digits`, `app/context/num_to_words.py`) is applied
before every synthesis call, batch or streaming -- Sarvam's own documented
behavior is to mis-speak or drop bare digits, especially Devanagari
numerals, and that fix is preserved exactly.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib

import aiohttp
from sarvamai import AsyncSarvamAI, AudioOutput, ErrorResponse, EventResponse

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
TTS_CHAR_LIMIT = 450
NUM_CHANNELS = 1


def _truncate(text: str) -> str:
    return text[:TTS_CHAR_LIMIT]


class TTS(tts.TTS):
    def __init__(
        self, *, api_key: str, model: str = "bulbul:v3", speaker: str = "shubh",
        target_language: str = "en-IN", sample_rate: int = 24000,
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
        # Lazily-created, reused across SynthesizeStream instances (one per
        # TTS turn) rather than a brand-new AsyncSarvamAI per turn -- same
        # "one session, many calls" pattern as `_ensure_session` above.
        # Overridable per-instance in tests (see tests/test_sarvam_tts.py)
        # without needing a real API key or network access.
        self._streaming_client: AsyncSarvamAI | None = None

    def _ensure_streaming_client(self) -> AsyncSarvamAI:
        if self._streaming_client is None:
            self._streaming_client = AsyncSarvamAI(api_subscription_key=self._api_key)
        return self._streaming_client

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def update_language(self, language: str) -> None:
        """Called by the agent when LID determines the conversation's active
        language has changed."""
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
    """Batch path -- unchanged, raw HTTP call to the non-streaming endpoint."""

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
                    raise APIStatusError(message=body[:800], status_code=resp.status, request_id=None, body=None)
                resp_json = await resp.json()

        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except APIStatusError:
            raise
        except Exception as e:
            raise APIConnectionError() from e

        audios_b64 = [a for a in (resp_json.get("audios") or []) if a]
        if not audios_b64:
            raise APIStatusError(message="Sarvam TTS returned no audio", status_code=200, request_id=None, body=None)

        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=self._tts.sample_rate,
            num_channels=NUM_CHANNELS, mime_type="audio/wav",
        )
        for audio_b64 in audios_b64:
            output_emitter.push(base64.b64decode(audio_b64))
        output_emitter.flush()


class SynthesizeStream(tts.SynthesizeStream):
    """Streaming path -- uses Sarvam's official SDK (AsyncSarvamAI), not a
    hand-rolled WebSocket client. One connection per stream lifetime."""

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

        client = self._tts._ensure_streaming_client()

        try:
            async with client.text_to_speech_streaming.connect(
                model=self._tts._model, send_completion_event=True,
            ) as ws:
                await ws.configure(
                    target_language_code=self._tts._language,
                    speaker=self._tts._speaker,
                    output_audio_codec="linear16",
                    # Must match what output_emitter.initialize() declared
                    # above -- otherwise Sarvam renders at its own default
                    # (22050 Hz) while we tell LiveKit to expect
                    # self._tts.sample_rate, corrupting playback speed/pitch.
                    speech_sample_rate=self._tts.sample_rate,
                    # Deliberately NOT overriding min_buffer_size here. The
                    # SDK's docstring gives it no documented valid range,
                    # and Sarvam's server rejected a lowered value (10)
                    # with a generic 422 ("Input parameters has to be a
                    # valid dictionary") -- an undocumented server-side
                    # floor, not a client-side schema error (the payload
                    # itself is well-formed; verified by hand against
                    # ConfigureConnectionData). Left at the SDK's own
                    # default (50, known-good) rather than guessing at
                    # a number Sarvam hasn't documented as valid. The
                    # unconditional flush after the input channel closes
                    # (below) is what actually fixes short replies getting
                    # stranded below that threshold -- it doesn't depend
                    # on min_buffer_size being lowered.
                )

                async def sender() -> None:
                    async for data in self._input_ch:
                        if isinstance(data, str):
                            spoken = spell_digits(data, self._tts._language)
                            tts_text = _truncate(spoken)
                            # `.strip()` only catches whitespace-only chunks.
                            # LiveKit's LLM->TTS streaming can (and does --
                            # confirmed against a real production run) hand
                            # us a trailing chunk that's pure punctuation,
                            # e.g. a lone ".". That's non-empty, so the old
                            # `if tts_text.strip():` check let it through --
                            # and Sarvam legitimately rejects a message with
                            # zero letters/digits in it with "Text must
                            # contain at least one character from the
                            # allowed languages". Skipping it here changes
                            # nothing about the spoken output (punctuation
                            # on its own isn't vocalized anyway), it just
                            # stops sending Sarvam a message it will always
                            # reject.
                            if any(ch.isalnum() for ch in tts_text):
                                await ws.convert(tts_text)
                        else:  # explicit flush sentinel from the caller
                            await ws.flush()
                    # The input channel is exhausted (end_input() closed
                    # it). Whatever text was sent above may still be
                    # sitting unflushed in Sarvam's server-side buffer if
                    # it never reached min_buffer_size and the caller's
                    # last item wasn't itself a flush sentinel -- this is
                    # exactly the "no audio frames were pushed" failure
                    # mode for short replies. Always flush once more here
                    # so the trailing text is guaranteed to be processed.
                    await ws.flush()

                sender_task = asyncio.create_task(sender())

                try:
                    async for message in ws:
                        if isinstance(message, AudioOutput):
                            b64 = message.data.audio
                            if b64:
                                output_emitter.push(base64.b64decode(b64))
                        elif isinstance(message, ErrorResponse):
                            raise APIStatusError(
                                message=message.data.message,
                                status_code=message.data.code or -1,
                                request_id=message.data.request_id,
                                body=None,
                            )
                        elif isinstance(message, EventResponse):
                            if message.data.event_type == "final":
                                break
                finally:
                    if not sender_task.done():
                        sender_task.cancel()
                    # Retrieve/suppress the cancellation (or any exception
                    # sender() raised) so it doesn't surface later as an
                    # "asyncio - Task exception was never retrieved" log.
                    # CancelledError is a BaseException (not Exception) as
                    # of Python 3.8+, so it must be listed explicitly --
                    # letting it escape here would replace/mask whatever
                    # real exception (e.g. the APIStatusError above) is
                    # already propagating out of this `finally`.
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        await sender_task

        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except APIStatusError:
            raise
        except Exception as e:
            raise APIConnectionError() from e
        finally:
            output_emitter.end_segment()