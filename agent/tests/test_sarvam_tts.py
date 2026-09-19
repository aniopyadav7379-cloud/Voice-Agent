"""
Unit tests for the Sarvam TTS plugin. Mocks the HTTP/WS boundary only.
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import APIStatusError, DEFAULT_API_CONNECT_OPTIONS

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


class _FakeWSMessage:
    def __init__(self, data: dict):
        import aiohttp
        self.type = aiohttp.WSMsgType.TEXT
        self.data = json.dumps(data)


class _FakeStreamingWS:
    """Minimal fake of aiohttp.ClientWebSocketResponse sufficient to drive
    SynthesizeStream._run(): supports send_str, close, and async iteration
    over a fixed sequence of server messages.

    Deliberately waits for at least one "text" message to have been sent
    before yielding any queued server message — a real Sarvam server can't
    respond with audio before receiving text to synthesize, so a fake that
    yields messages immediately (regardless of whether the client has sent
    anything yet) creates an artificial race that wouldn't occur against
    a real WS. This caused a real test failure on the first attempt (see
    STATUS_REPORT.md) that turned out to be a test-realism bug, not a
    production bug: `asyncio.create_task(sender())` doesn't guarantee the
    sender runs before the receive loop's first iteration, and a
    same-tick fake WS exposed that where a real network round-trip never
    would. Fixed here by making the fake wait for real causality instead
    of adding an artificial delay or asserting less."""

    def __init__(self, messages: list[dict]):
        self._messages = messages
        self.sent: list[str] = []
        self.closed = False
        self._text_sent = asyncio.Event()

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        if json.loads(data).get("type") == "text":
            self._text_sent.set()

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        self._iter = iter(self._messages)
        return self

    async def __anext__(self):
        await self._text_sent.wait()
        try:
            return _FakeWSMessage(next(self._iter))
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_synthesize_stream_pushes_audio_and_ends_on_final_event():
    """Drives the real SynthesizeStream through its public interface
    (push_text/flush/end_input + async iteration), mocking only the WS
    connection. Confirms: config message sent first, text message sent
    with digit-spelling applied, audio chunks decoded and yielded as real
    SynthesizedAudio frames, and the stream terminates cleanly on the
    server's 'final' event rather than hanging or erroring."""
    audio_b64 = base64.b64encode(b"\x00\x01\x02\x03").decode("ascii")
    fake_ws = _FakeStreamingWS(messages=[
        {"type": "audio", "data": {"audio": audio_b64}},
        {"type": "event", "data": {"event_type": "final"}},
    ])

    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=fake_ws)

    tts_client = TTS(api_key="fake-key", target_language="hi", sample_rate=16000)
    tts_client._ensure_session = lambda: session

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

    # Config message must be sent before any text.
    assert fake_ws.sent, "no messages were sent over the WS at all"
    first_msg = json.loads(fake_ws.sent[0])
    assert first_msg["type"] == "config"
    assert first_msg["data"]["target_language_code"] == "hi"

    # Digit-spelling must have been applied to the text actually sent.
    text_msgs = [json.loads(m) for m in fake_ws.sent if json.loads(m).get("type") == "text"]
    assert text_msgs, "no text message was ever sent to Sarvam"
    assert text_msgs[0]["data"]["text"] != "65 rupees", "digits should have been spelled, not sent raw"

    assert fake_ws.closed, "WS connection was not closed after the stream ended"


@pytest.mark.asyncio
async def test_synthesize_stream_ws_connect_failure_raises_not_hangs():
    """If the streaming WS can't connect at all, this must raise a
    structured error promptly — not hang, and not silently yield zero
    audio as if synthesis had quietly succeeded."""
    from livekit.agents import APIConnectionError

    session = MagicMock()

    async def _raise(*a, **kw):
        raise ConnectionError("simulated connect failure")

    session.ws_connect = _raise

    tts_client = TTS(api_key="fake-key")
    tts_client._ensure_session = lambda: session

    stream = tts_client.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream.push_text("hello")
    stream.flush()
    stream.end_input()

    with pytest.raises(APIConnectionError):
        async for _ in stream:
            pass

    await stream.aclose()
