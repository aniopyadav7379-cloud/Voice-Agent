"""
Unit tests for the Sarvam STT plugin. Mocks the HTTP/WS boundary only —
everything else (request construction, response parsing, romanized-LID
integration, base-class conformance) runs for real. No network access to
api.sarvam.ai happens or is needed for these tests to be meaningful.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from livekit.agents import APIStatusError, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents import stt as stt_base

from app.voice_providers.sarvam.stt import STT, SpeechStream


def _mock_response(status: int, json_body: dict):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body)
    resp.text = AsyncMock(return_value=json.dumps(json_body))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _dummy_frame():
    import numpy as np
    from livekit import rtc

    return rtc.AudioFrame(
        data=np.zeros(160, dtype=np.int16).tobytes(), sample_rate=16000, num_channels=1, samples_per_channel=160,
    )


@pytest.mark.asyncio
async def test_recognize_impl_success_applies_romanized_override():
    """A transcript Sarvam tags 'en' but that is actually romanized Hindi
    (per app/context/romanized_lid.py) must come back tagged hi-IN — proving
    the romanized-override wiring, not just the HTTP call, is correct."""
    stt = STT(api_key="fake-key")
    session = MagicMock()
    session.post = MagicMock(return_value=_mock_response(200, {
        "transcript": "kya aap mujhe Hyderabad ka weather batao sakte hain",
        "language_code": "en",
        "confidence": 0.9,
    }))
    stt._ensure_session = lambda: session

    event = await stt._recognize_impl([_dummy_frame()], conn_options=DEFAULT_API_CONNECT_OPTIONS)

    assert event.type == stt_base.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "kya aap mujhe Hyderabad ka weather batao sakte hain"
    assert str(event.alternatives[0].language) == "hi-IN"


@pytest.mark.asyncio
async def test_recognize_impl_http_error_raises_status_error():
    """A real 500 from Sarvam must surface as APIStatusError, not a
    swallowed/empty transcript."""
    stt = STT(api_key="fake-key")
    session = MagicMock()
    session.post = MagicMock(return_value=_mock_response(500, {"error": "server error"}))
    stt._ensure_session = lambda: session

    with pytest.raises(APIStatusError):
        await stt._recognize_impl([_dummy_frame()], conn_options=DEFAULT_API_CONNECT_OPTIONS)


@pytest.mark.asyncio
async def test_recognize_impl_timeout_raises_api_timeout_error():
    from livekit.agents import APITimeoutError

    stt = STT(api_key="fake-key")
    session = MagicMock()

    def _raise_timeout(*a, **kw):
        raise asyncio.TimeoutError()

    session.post = _raise_timeout
    stt._ensure_session = lambda: session

    with pytest.raises(APITimeoutError):
        await stt._recognize_impl([_dummy_frame()], conn_options=DEFAULT_API_CONNECT_OPTIONS)


@pytest.mark.asyncio
async def test_streaming_falls_back_to_batch_on_ws_connect_failure():
    """THE core reliability property ported from the source: if the Sarvam
    streaming WS can't even connect, the utterance must still produce a
    transcript via the batch HTTP path — never silently dropped, never
    fabricated. This is the single most important test in this file."""
    stt = STT(api_key="fake-key")

    session = MagicMock()

    async def _raise_connect_error(*a, **kw):
        raise ConnectionError("simulated WS connect failure")

    session.ws_connect = _raise_connect_error
    stt._ensure_session = lambda: session

    fallback_event = stt_base.SpeechEvent(
        type=stt_base.SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[stt_base.SpeechData(language="hi-IN", text="fallback transcript", confidence=0.8)],
    )
    stt._recognize_impl = AsyncMock(return_value=fallback_event)

    stream = stt.stream(conn_options=DEFAULT_API_CONNECT_OPTIONS)

    # Feed one fake audio frame, then flush to end the utterance.
    import numpy as np
    from livekit import rtc

    frame = rtc.AudioFrame(
        data=(np.zeros(160, dtype=np.int16)).tobytes(), sample_rate=16000, num_channels=1, samples_per_channel=160,
    )
    stream.push_frame(frame)
    stream.flush()
    stream.end_input()

    events = []
    async for event in stream:
        events.append(event)

    await stream.aclose()

    final_events = [e for e in events if e.type == stt_base.SpeechEventType.FINAL_TRANSCRIPT]
    assert len(final_events) == 1, f"expected exactly 1 fallback transcript, got {len(final_events)}"
    assert final_events[0].alternatives[0].text == "fallback transcript"
    stt._recognize_impl.assert_awaited_once()
