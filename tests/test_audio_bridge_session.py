"""
Unit tests for AudioBridgeSession and AudioBridgeManager.

These tests focus on the critical session management functionality:
1. Pre-warming sessions for reduced latency
2. Session claiming and handoff
3. Proper cleanup to prevent resource leaks
4. Metrics tracking for monitoring
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.voice_ai_system.services.audio_bridge import (
    AudioBridgeSession,
    AudioBridgeManager,
)


@pytest.fixture
def mock_genai_client():
    """Create a mock Gemini client that doesn't make real API calls."""
    with patch("src.voice_ai_system.services.audio_bridge.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_session = AsyncMock()

        # Create async context manager for session
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_client.aio.live.connect = MagicMock(return_value=mock_context)
        mock_client_class.return_value = mock_client

        yield mock_client, mock_session


class TestAudioBridgeSessionInit:
    """Test AudioBridgeSession initialization and configuration."""

    def test_session_initializes_with_correct_defaults(self):
        """Test that session initializes with correct VAD defaults."""
        session = AudioBridgeSession("test-session", "test-call")

        # Check VAD config defaults (critical for speech detection)
        assert session._vad_config["start_sensitivity"] == "HIGH"
        assert session._vad_config["end_sensitivity"] == "LOW"
        assert session._vad_config["silence_duration_ms"] == 500
        assert session._vad_config["disabled"] is False

    def test_session_initializes_queues_correctly(self):
        """Test that audio queues are initialized with correct sizes."""
        session = AudioBridgeSession("test-session", "test-call")

        # out_queue has maxsize for backpressure
        assert session.out_queue.maxsize == 100
        # audio_in_queue is unbounded
        assert session.audio_in_queue.maxsize == 0  # 0 means unbounded

    def test_session_initializes_metrics(self):
        """Test that metrics are initialized to zero."""
        session = AudioBridgeSession("test-session", "test-call")

        assert session.total_frames_sent == 0
        assert session.total_frames_received == 0
        assert session.dropped_frames == 0
        assert session.ai_turn_count == 0
        assert session.user_turn_count == 0
        assert session.interruption_count == 0


class TestAudioBridgeSessionMetrics:
    """Test metrics collection and retrieval."""

    def test_get_metrics_returns_complete_data(self):
        """Test that get_metrics returns all expected fields."""
        session = AudioBridgeSession("test-session", "test-call")

        # Set some values
        session.total_frames_sent = 100
        session.total_frames_received = 50
        session.dropped_frames = 5
        session.ai_turn_count = 3
        session.session_started_at = datetime.now(timezone.utc)

        metrics = session.get_metrics()

        assert metrics["total_audio_frames_sent"] == 100
        assert metrics["total_audio_frames_received"] == 50
        assert metrics["total_audio_frames_dropped"] == 5
        assert metrics["audio_drop_rate_percent"] == 5.0
        assert metrics["ai_turn_count"] == 3
        assert "session_started_at" in metrics

    def test_drop_rate_calculation(self):
        """Test that drop rate is calculated correctly."""
        session = AudioBridgeSession("test-session", "test-call")

        session.total_frames_sent = 1000
        session.dropped_frames = 50

        metrics = session.get_metrics()
        assert metrics["audio_drop_rate_percent"] == 5.0

    def test_queue_utilization_calculation(self):
        """Test queue utilization calculation."""
        session = AudioBridgeSession("test-session", "test-call")

        # Add some items to the queue
        for _ in range(50):
            session.out_queue.put_nowait("test")

        metrics = session.get_metrics()
        assert metrics["queue_utilization"] == 50.0  # 50/100 * 100


class TestAudioBridgeManager:
    """Test AudioBridgeManager session management."""

    @pytest.mark.asyncio
    async def test_create_session(self, mock_genai_client):
        """Test basic session creation."""
        manager = AudioBridgeManager()

        session = await manager.create_session(
            session_id="session-1",
            call_id="call-1",
            greeting="Hello",
            system_prompt="Be helpful"
        )

        assert session is not None
        assert "session-1" in manager.sessions
        assert session.session_id == "session-1"

        # Cleanup
        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_get_session(self, mock_genai_client):
        """Test retrieving an existing session."""
        manager = AudioBridgeManager()

        created = await manager.create_session("session-1", "call-1")
        retrieved = await manager.get_session("session-1")

        assert retrieved is created

        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_returns_none(self, mock_genai_client):
        """Test that getting a non-existent session returns None."""
        manager = AudioBridgeManager()

        result = await manager.get_session("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_close_session(self, mock_genai_client):
        """Test closing a session removes it from manager."""
        manager = AudioBridgeManager()

        await manager.create_session("session-1", "call-1")
        assert "session-1" in manager.sessions

        await manager.close_session("session-1")
        assert "session-1" not in manager.sessions

    @pytest.mark.asyncio
    async def test_close_all_sessions(self, mock_genai_client):
        """Test closing all sessions clears the manager."""
        manager = AudioBridgeManager()

        await manager.create_session("session-1", "call-1")
        await manager.create_session("session-2", "call-2")
        assert len(manager.sessions) == 2

        await manager.close_all_sessions()
        assert len(manager.sessions) == 0


class TestPrewarming:
    """Test session pre-warming functionality."""

    @pytest.mark.asyncio
    async def test_prewarm_creates_session(self, mock_genai_client):
        """Test that pre-warming creates a session with prewarm prefix."""
        manager = AudioBridgeManager()

        await manager.prewarm_session(
            workflow_id="workflow-123",
            greeting="Hello!",
            system_prompt="Be helpful"
        )

        assert "workflow-123" in manager.prewarmed_sessions
        session = manager.prewarmed_sessions["workflow-123"]
        assert session.session_id.startswith("prewarm-")

        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_prewarm_does_not_duplicate(self, mock_genai_client):
        """Test that pre-warming the same workflow twice doesn't create duplicates."""
        manager = AudioBridgeManager()

        await manager.prewarm_session("workflow-123", "Hello")
        await manager.prewarm_session("workflow-123", "Hello again")

        # Should still only have one pre-warmed session
        assert len(manager.prewarmed_sessions) == 1

        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_get_or_create_claims_prewarmed_session(self, mock_genai_client):
        """Test that get_or_create_session claims a pre-warmed session."""
        manager = AudioBridgeManager()

        # Pre-warm
        await manager.prewarm_session("workflow-123", "Hello")
        prewarmed = manager.prewarmed_sessions["workflow-123"]

        # Claim it
        session = await manager.get_or_create_session(
            session_id="real-stream-sid",
            workflow_id="workflow-123",
            call_id="call-456",
        )

        # Should be the same session object
        assert session is prewarmed
        # But with updated session_id
        assert session.session_id == "real-stream-sid"
        # And moved from prewarmed to active sessions
        assert "workflow-123" not in manager.prewarmed_sessions
        assert "real-stream-sid" in manager.sessions

        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new_when_no_prewarm(self, mock_genai_client):
        """Test that get_or_create creates new session when no pre-warmed exists."""
        manager = AudioBridgeManager()

        session = await manager.get_or_create_session(
            session_id="stream-sid",
            workflow_id="workflow-999",  # No pre-warmed session for this
            call_id="call-456",
        )

        assert session is not None
        assert session.session_id == "stream-sid"
        assert "stream-sid" in manager.sessions

        await manager.close_all_sessions()

    @pytest.mark.asyncio
    async def test_cleanup_prewarm_removes_session(self, mock_genai_client):
        """Test that cleanup_prewarm properly removes pre-warmed sessions."""
        manager = AudioBridgeManager()

        await manager.prewarm_session("workflow-123", "Hello")
        assert "workflow-123" in manager.prewarmed_sessions

        cleaned = await manager.cleanup_prewarm("workflow-123")

        assert cleaned is True
        assert "workflow-123" not in manager.prewarmed_sessions

    @pytest.mark.asyncio
    async def test_cleanup_prewarm_returns_false_for_nonexistent(self, mock_genai_client):
        """Test that cleanup_prewarm returns False for non-existent sessions."""
        manager = AudioBridgeManager()

        cleaned = await manager.cleanup_prewarm("does-not-exist")
        assert cleaned is False


class TestBackpressure:
    """Test backpressure handling in audio queues."""

    @pytest.mark.asyncio
    async def test_queue_full_drops_frames(self, mock_genai_client):
        """Test that frames are dropped when queue is full."""
        manager = AudioBridgeManager()
        session = await manager.create_session("session-1", "call-1")

        # Fill the queue to 80%+ capacity (triggers backpressure)
        for _ in range(85):
            try:
                session.out_queue.put_nowait("test")
            except asyncio.QueueFull:
                pass

        # Now send audio - should trigger early backpressure
        import base64
        test_audio = base64.b64encode(b"\x7f" * 160).decode()  # μ-law silence

        initial_dropped = session.dropped_frames

        # Send multiple frames - some should be dropped
        for _ in range(20):
            await session.send_audio_from_twilio(test_audio)

        # Should have dropped some frames
        assert session.dropped_frames > initial_dropped

        await manager.close_all_sessions()


class TestSessionLifecycle:
    """Test session start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_session_stop_sets_inactive(self, mock_genai_client):
        """Test that stopping a session sets it to inactive."""
        session = AudioBridgeSession("test-session", "test-call")
        await session.start()

        assert session.active is True

        await session.stop()

        assert session.active is False

    @pytest.mark.asyncio
    async def test_session_stop_is_idempotent(self, mock_genai_client):
        """Test that stopping a session multiple times is safe."""
        session = AudioBridgeSession("test-session", "test-call")
        await session.start()

        # Stop multiple times - should not raise
        await session.stop()
        await session.stop()
        await session.stop()

        assert session.active is False

    @pytest.mark.asyncio
    async def test_send_audio_after_stop_is_ignored(self, mock_genai_client):
        """Test that sending audio after stop is silently ignored."""
        session = AudioBridgeSession("test-session", "test-call")
        await session.start()
        await session.stop()

        import base64
        test_audio = base64.b64encode(b"\x7f" * 160).decode()

        # Should not raise
        await session.send_audio_from_twilio(test_audio)

        # Frame count should not increase
        assert session.total_frames_sent == 0
