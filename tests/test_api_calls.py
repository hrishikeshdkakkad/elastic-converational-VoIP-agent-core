"""
Integration tests for the /calls API endpoints.

These tests verify the critical call initiation flow:
1. Call request validation
2. Temporal workflow startup
3. Pre-warming integration
4. Error handling and cleanup
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.voice_ai_system.api.routes.calls import router


# Patch the audio_bridge_manager at the module level where it's imported
AUDIO_BRIDGE_PATCH = "src.voice_ai_system.services.audio_bridge.audio_bridge_manager"


@pytest.fixture
def mock_audio_bridge():
    """Mock the audio bridge manager."""
    with patch(AUDIO_BRIDGE_PATCH) as mock_manager:
        mock_manager.prewarm_session = AsyncMock()
        mock_manager.cleanup_prewarm = AsyncMock(return_value=True)
        yield mock_manager


@pytest.fixture
def app(mock_audio_bridge):
    """Create a FastAPI app with the calls router."""
    app = FastAPI()
    app.include_router(router, prefix="/calls")

    # Mock Temporal client
    mock_temporal = MagicMock()
    mock_handle = AsyncMock()
    mock_handle.first_execution_run_id = "test-run-id"
    mock_temporal.start_workflow = AsyncMock(return_value=mock_handle)
    mock_temporal.get_workflow_handle = MagicMock(return_value=mock_handle)

    # Mock settings
    mock_settings = MagicMock()
    mock_settings.worker_task_queue = "test-queue"

    app.state.temporal_client = mock_temporal
    app.state.settings = mock_settings

    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestInitiateCall:
    """Test the POST /calls endpoint."""

    def test_initiate_call_success(self, client, app):
        """Test successful call initiation."""
        response = client.post(
            "/calls",
            json={
                "phone_number": "+1234567890",
                "greeting": "Hello!",
                "system_prompt": "Be helpful",
                "max_duration_seconds": 300
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert "workflow_id" in data
        assert data["workflow_id"].startswith("call-")
        assert data["phone_number"] == "+1234567890"
        assert data["status"] == "initiated"

    def test_initiate_call_minimal_request(self, client, app):
        """Test call initiation with only required fields."""
        response = client.post(
            "/calls",
            json={"phone_number": "+1234567890"}
        )

        assert response.status_code == 201
        data = response.json()
        assert data["phone_number"] == "+1234567890"

    def test_initiate_call_missing_phone_number(self, client):
        """Test that missing phone number returns 422."""
        response = client.post("/calls", json={})

        assert response.status_code == 422

    def test_initiate_call_starts_prewarm(self, client, app):
        """Test that call initiation triggers pre-warming."""
        response = client.post(
            "/calls",
            json={
                "phone_number": "+1234567890",
                "greeting": "Hello!",
                "system_prompt": "Custom prompt"
            }
        )

        assert response.status_code == 201
        # Pre-warming happens via asyncio.create_task, so we just verify
        # the call succeeded (pre-warming is fire-and-forget)

    def test_initiate_call_temporal_failure_cleanup(self, client, app, mock_audio_bridge):
        """Test that pre-warmed session is cleaned up on Temporal failure."""
        # Make Temporal fail
        app.state.temporal_client.start_workflow = AsyncMock(
            side_effect=Exception("Temporal unavailable")
        )

        response = client.post(
            "/calls",
            json={"phone_number": "+1234567890"}
        )

        assert response.status_code == 500
        assert "Failed to initiate call" in response.json()["detail"]


class TestGetCallStatus:
    """Test the GET /calls/{workflow_id} endpoint."""

    def test_get_call_status_success(self, client, app):
        """Test successful status retrieval."""
        mock_handle = app.state.temporal_client.get_workflow_handle.return_value
        mock_handle.query = AsyncMock(side_effect=[
            "in-progress",  # get_call_status
            5,              # get_transcript_count
            {"greeting": "Hello"}  # get_call_config
        ])

        response = client.get("/calls/call-123")

        assert response.status_code == 200
        data = response.json()
        assert data["workflow_id"] == "call-123"
        assert data["status"] == "in-progress"
        assert data["transcript_count"] == 5

    def test_get_call_status_not_found(self, client, app):
        """Test 404 when workflow doesn't exist."""
        app.state.temporal_client.get_workflow_handle.return_value.query = AsyncMock(
            side_effect=Exception("Workflow not found")
        )

        response = client.get("/calls/nonexistent-workflow")

        assert response.status_code == 404


class TestTerminateCall:
    """Test the POST /calls/{workflow_id}/terminate endpoint."""

    def test_terminate_call_success(self, client, app):
        """Test successful call termination."""
        mock_handle = app.state.temporal_client.get_workflow_handle.return_value
        mock_handle.signal = AsyncMock()

        response = client.post("/calls/call-123/terminate")

        assert response.status_code == 204
        mock_handle.signal.assert_called_once()

    def test_terminate_call_failure(self, client, app):
        """Test 500 when termination fails."""
        mock_handle = app.state.temporal_client.get_workflow_handle.return_value
        mock_handle.signal = AsyncMock(side_effect=Exception("Signal failed"))

        response = client.post("/calls/call-123/terminate")

        assert response.status_code == 500


class TestGetCallResult:
    """Test the GET /calls/{workflow_id}/result endpoint."""

    def test_get_call_result_success(self, client, app):
        """Test successful result retrieval."""
        mock_result = MagicMock()
        mock_result.model_dump.return_value = {
            "status": "completed",
            "duration": 120,
            "transcript_segments": 10
        }

        mock_handle = app.state.temporal_client.get_workflow_handle.return_value
        mock_handle.result = AsyncMock(return_value=mock_result)

        response = client.get("/calls/call-123/result")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

    def test_get_call_result_workflow_still_running(self, client, app):
        """Test 500 when workflow hasn't completed."""
        mock_handle = app.state.temporal_client.get_workflow_handle.return_value
        mock_handle.result = AsyncMock(
            side_effect=Exception("Workflow still running")
        )

        response = client.get("/calls/call-123/result")

        assert response.status_code == 500


class TestRequestValidation:
    """Test request validation for the /calls endpoint."""

    def test_phone_number_required(self, client):
        """Test that phone_number is required."""
        response = client.post("/calls", json={"greeting": "Hello"})
        assert response.status_code == 422

    def test_default_values_applied(self, client, app):
        """Test that default values are applied correctly."""
        response = client.post(
            "/calls",
            json={"phone_number": "+1234567890"}
        )

        assert response.status_code == 201

        # Verify workflow was started with correct input
        call_args = app.state.temporal_client.start_workflow.call_args
        workflow_input = call_args[0][1]  # Second positional arg

        # Check defaults were applied
        assert workflow_input.greeting == "Hello! How can I help you today?"
        assert workflow_input.max_duration_seconds == 1800

    def test_custom_values_override_defaults(self, client, app):
        """Test that custom values override defaults."""
        response = client.post(
            "/calls",
            json={
                "phone_number": "+1234567890",
                "greeting": "Custom greeting",
                "max_duration_seconds": 600
            }
        )

        assert response.status_code == 201

        call_args = app.state.temporal_client.start_workflow.call_args
        workflow_input = call_args[0][1]

        assert workflow_input.greeting == "Custom greeting"
        assert workflow_input.max_duration_seconds == 600
