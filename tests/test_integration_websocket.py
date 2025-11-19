"""
Integration tests for refactored WebSocket routes.
Tests that audio bypasses Temporal for real-time processing.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.voice_ai_system.api.routes.twilio import router


@pytest.fixture
def test_app():
    """Create test FastAPI app."""
    app = FastAPI()
    # Mount with /twilio prefix to match production configuration
    app.include_router(router, prefix="/twilio")

    # Mock Temporal client
    app.state.temporal_client = MagicMock()
    app.state.settings = MagicMock(base_url="http://test.example.com")

    return app


@pytest.fixture
def test_client(test_app):
    """Create test client."""
    return TestClient(test_app)


@pytest.mark.asyncio
async def test_websocket_creates_audio_bridge():
    """Test that WebSocket creates audio bridge instead of using Temporal."""
    with patch(
        "src.voice_ai_system.api.routes.twilio.audio_bridge_manager"
    ) as mock_manager:
        # Setup mocks
        mock_session = AsyncMock()
        mock_manager.create_session.return_value = mock_session
        mock_session.send_audio_from_twilio = AsyncMock()
        mock_session.receive_audio_for_twilio = AsyncMock(return_value=None)
        mock_session.get_transcript_buffer = AsyncMock(return_value=[])

        # Mock Temporal workflow handle
        mock_handle = AsyncMock()
        mock_handle.query.return_value = {
            "call_id": "test-call-123",
            "greeting": "Hello",
            "system_prompt": "Be helpful",
        }

        app = FastAPI()
        # Mount with /twilio prefix to match production configuration
        app.include_router(router, prefix="/twilio")
        app.state.temporal_client = MagicMock()
        app.state.temporal_client.get_workflow_handle.return_value = mock_handle

        client = TestClient(app)

        # Connect WebSocket
        with client.websocket_connect("/twilio/ws/media/test-workflow") as websocket:
            # Send start event
            websocket.send_json(
                {
                    "event": "start",
                    "start": {
                        "streamSid": "stream-123",
                        "callSid": "call-123",
                    },
                }
            )

            # Verify audio bridge was created (NOT Temporal activity)
            mock_manager.create_session.assert_called_once_with(
                session_id="stream-123",
                call_id="test-call-123",
                greeting="Hello",
                system_prompt="Be helpful",
            )

            # Send media event
            websocket.send_json(
                {
                    "event": "media",
                    "media": {"payload": "base64audiodata"},
                }
            )

            # Verify audio went to bridge, not Temporal
            mock_session.send_audio_from_twilio.assert_called_with("base64audiodata")

            # Send stop event
            websocket.send_json({"event": "stop"})

        # Verify cleanup
        mock_manager.close_session.assert_called_with("stream-123")


@pytest.mark.asyncio
async def test_periodic_transcript_sync():
    """Test that transcripts are synced periodically, not per-frame."""
    with patch(
        "src.voice_ai_system.api.routes.twilio.audio_bridge_manager"
    ) as mock_manager:
        with patch(
            "src.voice_ai_system.api.routes.twilio._sync_transcripts_to_workflow"
        ) as mock_sync:
            mock_session = AsyncMock()
            mock_manager.create_session.return_value = mock_session

            mock_handle = AsyncMock()
            mock_handle.query.return_value = {
                "call_id": "test-call",
                "greeting": "",
                "system_prompt": None,
            }

            app = FastAPI()
            # Mount with /twilio prefix to match production configuration
            app.include_router(router, prefix="/twilio")
            app.state.temporal_client = MagicMock()
            app.state.temporal_client.get_workflow_handle.return_value = mock_handle

            client = TestClient(app)

            with client.websocket_connect("/twilio/ws/media/test-workflow") as websocket:
                websocket.send_json(
                    {
                        "event": "start",
                        "start": {"streamSid": "stream-123", "callSid": "call-123"},
                    }
                )

                # Verify sync task was created
                assert mock_sync.called

                websocket.send_json({"event": "stop"})


def test_twiml_generation(test_client):
    """Test TwiML generation for WebSocket streaming."""
    mock_handle = AsyncMock()
    mock_handle.query = AsyncMock(return_value="in-progress")

    test_client.app.state.temporal_client.get_workflow_handle.return_value = (
        mock_handle
    )

    response = test_client.post("/twilio/twiml/test-workflow")

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "<Stream" in response.text
    # Verify the TwiML includes the /twilio prefix in the WebSocket URL
    assert "ws://testserver/twilio/ws/media/test-workflow" in response.text


def test_status_callback_coarse_events(test_client):
    """Test that status callbacks only send coarse events to Temporal."""
    mock_handle = AsyncMock()
    test_client.app.state.temporal_client.get_workflow_handle.return_value = (
        mock_handle
    )

    # Send status update
    response = test_client.post(
        "/twilio/status/test-workflow",
        data={
            "CallStatus": "in-progress",
            "CallSid": "call-123",
        },
    )

    assert response.status_code == 200

    # Verify only status change was signaled (coarse event)
    assert mock_handle.signal.call_count == 2  # status + sid
    calls = mock_handle.signal.call_args_list

    # Check first signal was status change
    assert "call_status_changed" in str(calls[0])
    assert calls[0][0][1] == "in-progress"

    # Check second signal was SID setting
    assert "set_call_sid" in str(calls[1])
    assert calls[1][0][1] == "call-123"


@pytest.mark.asyncio
async def test_audio_bridge_error_handling():
    """Test error handling in audio bridge doesn't crash WebSocket."""
    with patch(
        "src.voice_ai_system.api.routes.twilio.audio_bridge_manager"
    ) as mock_manager:
        # Make audio bridge raise error
        mock_session = AsyncMock()
        mock_session.send_audio_from_twilio.side_effect = Exception("Audio error")
        mock_manager.create_session.return_value = mock_session

        mock_handle = AsyncMock()
        mock_handle.query.return_value = {"call_id": "test", "greeting": "", "system_prompt": None}

        app = FastAPI()
        # Mount with /twilio prefix to match production configuration
        app.include_router(router, prefix="/twilio")
        app.state.temporal_client = MagicMock()
        app.state.temporal_client.get_workflow_handle.return_value = mock_handle

        client = TestClient(app)

        # Should not crash despite errors
        with client.websocket_connect("/twilio/ws/media/test-workflow") as websocket:
            websocket.send_json(
                {
                    "event": "start",
                    "start": {"streamSid": "stream-123", "callSid": "call-123"},
                }
            )

            # Send media that will cause error
            websocket.send_json(
                {
                    "event": "media",
                    "media": {"payload": "audiodata"},
                }
            )

            # Should still be able to stop cleanly
            websocket.send_json({"event": "stop"})

        # Verify cleanup was still called
        mock_manager.close_session.assert_called()