"""
Integration tests for the audio bridge service.
Tests real audio streaming without going through Temporal.
"""

import asyncio
import base64
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.voice_ai_system.services.audio_bridge import (
    AudioBridgeSession,
    AudioBridgeManager,
)
from src.voice_ai_system.models.call import Speaker


@pytest.fixture
async def mock_gemini_client():
    """Mock Gemini client for testing."""
    with patch("src.voice_ai_system.services.audio_bridge.genai.Client") as mock:
        client = MagicMock()
        session = AsyncMock()

        # Mock the async context manager
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock()
        session.send_client_content = AsyncMock()
        session.send_realtime_input = AsyncMock()

        client.aio.live.connect = AsyncMock(return_value=session)
        mock.return_value = client

        yield client, session


@pytest.mark.asyncio
async def test_audio_bridge_session_creation(mock_gemini_client):
    """Test creating and starting an audio bridge session."""
    client, gemini_session = mock_gemini_client

    session = AudioBridgeSession("test-session", "test-call-id")
    await session.start(greeting="Hello", system_prompt="Be helpful")

    # Verify Gemini connection
    client.aio.live.connect.assert_called_once()
    gemini_session.send_client_content.assert_called_once()

    # Verify session is active
    assert session.active
    assert len(session.tasks) == 2

    # Clean up
    await session.stop()
    assert not session.active


@pytest.mark.asyncio
async def test_audio_bridge_twilio_to_gemini_flow(mock_gemini_client):
    """Test audio flowing from Twilio to Gemini."""
    client, gemini_session = mock_gemini_client

    session = AudioBridgeSession("test-session", "test-call-id")
    await session.start()

    # Simulate Twilio sending audio
    test_audio = base64.b64encode(b"test audio data").decode()
    await session.send_audio_from_twilio(test_audio)

    # Give processor time to handle
    await asyncio.sleep(0.1)

    # Verify audio was sent to Gemini (after conversion)
    assert gemini_session.send_realtime_input.called

    await session.stop()


@pytest.mark.asyncio
async def test_audio_bridge_transcript_buffering(mock_gemini_client):
    """Test transcript buffering and retrieval."""
    client, gemini_session = mock_gemini_client

    # Mock Gemini responses with text
    async def mock_receive():
        response = MagicMock()
        response.server_content.model_turn.parts = [
            MagicMock(text="Hello from AI", inline_data=None)
        ]
        yield response

    gemini_session.receive = mock_receive

    session = AudioBridgeSession("test-session", "test-call-id")
    await session.start()

    # Let processor run
    await asyncio.sleep(0.1)

    # Get transcripts
    transcripts = await session.get_transcript_buffer()

    assert len(transcripts) > 0
    assert transcripts[0].speaker == Speaker.AI
    assert transcripts[0].text == "Hello from AI"

    # Buffer should be cleared
    assert len(await session.get_transcript_buffer()) == 0

    await session.stop()


@pytest.mark.asyncio
async def test_audio_bridge_manager():
    """Test the audio bridge manager."""
    manager = AudioBridgeManager()

    with patch("src.voice_ai_system.services.audio_bridge.genai.Client"):
        # Create session
        session = await manager.create_session(
            "session-1", "call-1", "Hello", "Be helpful"
        )
        assert session is not None
        assert "session-1" in manager.sessions

        # Get session
        retrieved = await manager.get_session("session-1")
        assert retrieved == session

        # Close session
        await manager.close_session("session-1")
        assert "session-1" not in manager.sessions

        # Close all (with multiple sessions)
        await manager.create_session("session-2", "call-2")
        await manager.create_session("session-3", "call-3")
        assert len(manager.sessions) == 2

        await manager.close_all_sessions()
        assert len(manager.sessions) == 0


@pytest.mark.asyncio
async def test_audio_queue_overflow_handling(mock_gemini_client):
    """Test handling of queue overflow gracefully."""
    client, gemini_session = mock_gemini_client

    session = AudioBridgeSession("test-session", "test-call-id")
    await session.start()

    # Fill the incoming queue
    for _ in range(110):  # Queue maxsize is 100
        await session.send_audio_from_twilio("audio_data")

    # Should not crash, just drop frames
    assert session.active

    await session.stop()


@pytest.mark.asyncio
async def test_gemini_session_cleanup_on_error(mock_gemini_client):
    """Test that Gemini session is properly closed on errors."""
    client, gemini_session = mock_gemini_client

    # Make receive raise an exception
    gemini_session.receive.side_effect = Exception("Network error")

    session = AudioBridgeSession("test-session", "test-call-id")
    await session.start()

    # Let processor encounter error
    await asyncio.sleep(0.1)

    # Stop should still work and clean up
    await session.stop()

    # Verify cleanup was called
    gemini_session.__aexit__.assert_called()
