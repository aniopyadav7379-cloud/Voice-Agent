"""
Unit tests for the Sarvam TTS plugin. Mocks the HTTP/WS boundary only.

The streaming tests below drive `SynthesizeStream` against a fake of the
real `sarvamai` SDK socket client (`AsyncTextToSpeechStreamingSocketClient`),
not a hand-rolled `aiohttp` WebSocket -- `tts.py`'s streaming path now uses
Sarvam's official SDK directly (`AsyncSarvamAI().text_to_speech_streaming
.connect(...)`), confirmed against the real installed `sarvamai` package
(`inspect`/reading `sarvamai/text_to_speech_streaming/socket_client.py`),
so these mock the SDK's `AudioOutput` / `ErrorResponse` / `EventResponse`
response types rather than raw JSON-over-WS frames.
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pytest
from livekit.agents import APIStatusError, APIConnectionError, DEFAULT_API_CONNECT_OPTIONS
from sarvamai import AudioOutput, ErrorResponse, EventResponse
from sarvamai.types.audio_output_data import AudioOutputData
from sarvamai.types.error_response_data import ErrorResponseData
from sarvamai.types.event_response_data import EventResponseData

from app.voice_providers.sarvam.tts import TTS


def _mock_post_response(status: int, json_body: dict):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body)
    resp.text = AsyncMock(return_value=json.dumps(json_body))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class _FakeEmitter:
    def __init__(self):
        self.pushed = []
        self.initialized = False
        self.flushed = False

    def initialize(self, **kwargs):
        self.initialized = True
        self.init_kwargs = kwargs

    def push(self, data: bytes):
        self.pushed.append(data)

    def flush(self):
        self.flushed = True

    def start_segment(self, **kwargs):
        pass

    def end_segment(self):
        pass


@pytest.mark.asyncio
async def test_chunked_stream_success_decodes_and_pushes_audio():
    tts_client = TTS(api_key="fake-key", target_language="hi-IN")
    fake_audio = base64.b64encode(b"FAKE_WAV_BYTES").decode("ascii")
    session = MagicMock()
    session.post = MagicMock(return_value=_mock_post_response(200, {"audios": [fake_audio]}))
    tts_client._ensure_session = lambda: session

    stream = tts_client.synthesize("65 rupees only", conn_options=DEFAULT_API_CONNECT_OPTIONS)
    emitter = _FakeEmitter()
    await stream._run(emitter)

    assert emitter.initialized
    assert emitter.pushed == [b"FAKE_WAV_BYTES"]
    assert emitter.flushed


@pytest.mark.asyncio
async def test_chunked_stream_empty_audio_never_fabricates():
    """An empty/failed audios list must raise, not silently emit zero-length
    'success' audio — this is the TTS equivalent of never fabricating a
    transcript."""
    tts_client = TTS(api_key="fake-key")
    session = MagicMock()
    session.post = MagicMock(return_value=_mock_post_response(200, {"audios": []}))
    tts_client._ensure_session = lambda: session

    stream = tts_client.synthesize("hello", conn_options=DEFAULT_API_CONNECT_OPTIONS)
    emitter = _FakeEmitter()

    with pytest.raises(APIStatusError):
        await stream._run(emitter)
    assert emitter.pushed == []


@pytest.mark.asyncio
async def test_chunked_stream_applies_digit_spelling():
    """Verify spell_digits is actually applied before the text reaches
    Sarvam — checked by inspecting the actual outgoing request body, not by
    trusting that the import exists."""
    tts_client = TTS(api_key="fake-key", target_language="hi")
    fake_audio = base64.b64encode(b"X").decode("ascii")
    session = MagicMock()
    captured = {}

    def _capture_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _mock_post_response(200, {"audios": [fake_audio]})

    session.post = _capture_post
    tts_client._ensure_session = lambda: session

    stream = tts_client.synthesize("65", conn_options=DEFAULT_API_CONNECT_OPTIONS)
    emitter = _FakeEmitter()
    await stream._run(emitter)

    sent_text = captured["json"]["inputs"][0]
    assert sent_text != "65", "digits should have been spelled out, not sent raw"
    assert "पैंसठ" in sent_text or "pachaas" not in sent_text  # hi table: 65 = पैंसठ


class _FakeSocketClient:
    """Fake of `sarvamai`'s `AsyncTextToSpeechStreamingSocketClient` --
    the real object `client.text_to_speech_streaming.connect(...)` yields
    as an async context manager. Supports `configure`/`convert`/`flush`
    and async iteration over a fixed queue of typed SDK response objects
    (`AudioOutput` / `ErrorResponse` / `EventResponse`).

    Message delivery is gated on the causal event that would actually
    unlock it on a real server: an `AudioOutput`/`ErrorResponse` waits for
    at least one `convert()` call, and an `EventResponse` (the 'final'
    event) waits for at least one `flush()` call. A real Sarvam server
    can't emit audio before receiving text, or signal 'final' before its
    buffer was flushed — a fake that ignores that and yields immediately
    creates an artificial race between the receive loop and
    `asyncio.create_task(sender())` that a real network round-trip never
    would (this is the same class of test-realism bug the original
    version of this fake, against the old hand-rolled WS client, already
    had to guard against)."""

    def __init__(self, messages: list):
        self._messages = messages
        self.configure = AsyncMock()
        self.sent_text: list[str] = []
        self.flush_calls = 0
        self._got_text = asyncio.Event()
        self._got_flush = asyncio.Event()

    async def convert(self, text: str) -> None:
        self.sent_text.append(text)
        self._got_text.set()

    async def flush(self) -> None:
        self.flush_calls += 1
        self._got_flush.set()

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            message = next(self._iter)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(message, EventResponse):
            await self._got_flush.wait()
        else:
            await self._got_text.wait()
        return message


def _fake_streaming_client(ws: "_FakeSocketClient"):
    """Fake of `AsyncSarvamAI` exposing just the
    `.text_to_speech_streaming.connect(...)` async-context-manager surface
    `tts.py` actually calls, wired in via `TTS._ensure_streaming_client`'s
    `self._streaming_client` override (see below) instead of the real
    SDK's HTTP/WS boundary."""

    @asynccontextmanager
    async def connect(**kwargs):
        yield ws

    client = MagicMock()
    client.text_to_speech_streaming.connect = connect
    return client


@pytest.mark.asyncio
async def test_synthesize_stream_pushes_audio_and_ends_on_final_event():
    """Drives the real SynthesizeStream through its public interface
    (push_text/flush/end_input + async iteration), mocking only the SDK's
    WS socket client. Confirms: config sent with the right language and a
    sample rate matching the emitter, text sent with digit-spelling
    applied, audio chunks decoded and yielded as real SynthesizedAudio
    frames, and the stream terminates cleanly on the server's 'final'
    event rather than hanging or erroring."""
    audio_b64 = base64.b64encode(b"\x00\x01\x02\x03").decode("ascii")
    fake_ws = _FakeSocketClient(messages=[
        AudioOutput(data=AudioOutputData(content_type="audio/pcm", audio=audio_b64)),
        EventResponse(data=EventResponseData(event_type="final")),
    ])

    tts_client = TTS(api_key="fake-key", target_language="hi", sample_rate=16000)
    tts_client._streaming_client = _fake_streaming_client(fake_ws)

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream.push_text("65 rupees")
    stream.flush()
    stream.end_input()

    frames = []
    async for synthesized in stream:
        frames.append(synthesized)

    await stream.aclose()

    assert frames, "expected at least one SynthesizedAudio frame, got none"
    combined = b"".join(f.frame.data.tobytes() for f in frames)
    assert b"\x00\x01\x02\x03" in combined, "decoded audio bytes from the WS message never reached the output"

    # Config must carry the right language and match the emitter's sample
    # rate (regression check for the silent 22050-vs-16000 mismatch bug).
    fake_ws.configure.assert_awaited_once()
    config_kwargs = fake_ws.configure.await_args.kwargs
    assert config_kwargs["target_language_code"] == "hi"
    assert config_kwargs["speech_sample_rate"] == 16000

    # Digit-spelling must have been applied to the text actually sent.
    assert fake_ws.sent_text, "no text was ever sent to Sarvam"
    assert fake_ws.sent_text[0] != "65 rupees", "digits should have been spelled, not sent raw"


@pytest.mark.asyncio
async def test_synthesize_stream_flushes_trailing_text_without_explicit_flush():
    """Regression test for the actual production bug: a short reply sent
    as a single chunk, with end_input() closing the channel directly and
    no explicit stream.flush() first (exactly what a short LLM greeting
    like "Hi there! How can I assist you today?" does upstream). Before
    the fix this text could sit unflushed in Sarvam's server-side buffer
    forever, and the base tts.py would report it as "no audio frames were
    pushed" with the real cause gone."""
    audio_b64 = base64.b64encode(b"\x01\x02\x03\x04").decode("ascii")
    fake_ws = _FakeSocketClient(messages=[
        AudioOutput(data=AudioOutputData(content_type="audio/pcm", audio=audio_b64)),
        EventResponse(data=EventResponseData(event_type="final")),
    ])

    tts_client = TTS(api_key="fake-key")
    tts_client._streaming_client = _fake_streaming_client(fake_ws)

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream.push_text("Hi there! How can I assist you today?")
    stream.end_input()  # deliberately no stream.flush() before this

    frames = [f async for f in stream]
    await stream.aclose()

    assert frames, "expected audio frames for a short, unflushed reply"
    assert fake_ws.flush_calls >= 1, "sender() must still flush the trailing buffered text"


@pytest.mark.asyncio
async def test_synthesize_stream_skips_punctuation_only_chunks():
    """Regression test for the actual production bug, confirmed against a
    real run's debug log: LiveKit's LLM->TTS streaming can hand `sender()`
    a trailing chunk that's pure punctuation (a lone "."). That chunk is
    non-empty, so the old `if tts_text.strip():` check let it through to
    `ws.convert()` -- and Sarvam legitimately rejects a message with zero
    letters/digits in it with "Text must contain at least one character
    from the allowed languages" (422), which then killed the entire
    response even though every earlier chunk was fine. Confirmed via a
    standalone script against the real Sarvam API that the exact
    model/speaker/language combo this project uses works perfectly --
    this was never a config problem, only ever this chunk-filtering one."""
    audio_b64 = base64.b64encode(b"\x0a\x0b\x0c\x0d").decode("ascii")
    fake_ws = _FakeSocketClient(messages=[
        AudioOutput(data=AudioOutputData(content_type="audio/pcm", audio=audio_b64)),
        EventResponse(data=EventResponseData(event_type="final")),
    ])

    tts_client = TTS(api_key="fake-key")
    tts_client._streaming_client = _fake_streaming_client(fake_ws)

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    # Same shape as the real debug log: two chunks of real content, then a
    # bare trailing period as its own separate push_text() call.
    stream.push_text("Hello! How can I help you today? Please let")
    stream.push_text(" me know what task, ticket, worker dispatch, or information you need assistance with")
    stream.push_text(".")
    stream.end_input()

    frames = [f async for f in stream]
    await stream.aclose()

    assert frames, "expected audio frames despite the trailing punctuation-only chunk"
    assert "." not in fake_ws.sent_text, "a punctuation-only chunk must never be sent to Sarvam"
    assert all(any(ch.isalnum() for ch in t) for t in fake_ws.sent_text), (
        f"every chunk actually sent must contain at least one letter/digit, got {fake_ws.sent_text!r}"
    )


@pytest.mark.asyncio
async def test_synthesize_stream_raises_on_error_response():
    """The bug: a server-side ErrorResponse was silently ignored by the
    receive loop, the connection then closed on its own, and no audio was
    ever pushed — masked by the base SDK as a generic 'no audio frames
    were pushed' error with Sarvam's actual error message/code lost. Must
    now raise APIStatusError carrying that real message and code."""
    fake_ws = _FakeSocketClient(messages=[
        ErrorResponse(data=ErrorResponseData(message="invalid speaker for model", code=422)),
    ])

    tts_client = TTS(api_key="fake-key")
    tts_client._streaming_client = _fake_streaming_client(fake_ws)

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream.push_text("hello")
    stream.end_input()

    with pytest.raises(APIStatusError) as excinfo:
        async for _ in stream:
            pass
    assert "invalid speaker" in excinfo.value.message
    assert excinfo.value.status_code == 422

    await stream.aclose()


@pytest.mark.asyncio
async def test_synthesize_stream_ws_connect_failure_raises_not_hangs():
    """If the streaming connection can't be established at all, this must
    raise a structured error promptly — not hang, and not silently yield
    zero audio as if synthesis had quietly succeeded."""

    @asynccontextmanager
    async def _raise(**kwargs):
        raise ConnectionError("simulated connect failure")
        yield  # pragma: no cover - unreachable, makes this a generator

    client = MagicMock()
    client.text_to_speech_streaming.connect = _raise

    tts_client = TTS(api_key="fake-key")
    tts_client._streaming_client = client

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream.push_text("hello")
    stream.flush()
    stream.end_input()

    with pytest.raises(APIConnectionError):
        async for _ in stream:
            pass

    await stream.aclose()
