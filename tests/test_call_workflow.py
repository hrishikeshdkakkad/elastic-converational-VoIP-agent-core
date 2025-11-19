"""
Integration tests for the refactored call workflow.
Tests workflow orchestration with coarse-grained events.
"""

import pytest
from datetime import timedelta
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.voice_ai_system.workflows.call_workflow_refactored import (
    VoiceCallWorkflowRefactored,
)
from src.voice_ai_system.models.call import (
    CallStatus,
    CallWorkflowInput,
)


# Mock activities
async def mock_create_call_record(params):
    """Mock database call creation."""
    return "mock-call-id-123"


async def mock_initiate_twilio_call(params):
    """Mock Twilio call initiation."""
    return {
        "call_sid": "mock-twilio-sid-456",
        "status": "initiated",
    }


async def mock_terminate_twilio_call(call_sid):
    """Mock Twilio call termination."""
    return {"status": "completed"}


async def mock_update_call_record(call_id, updates):
    """Mock database call update."""
    return {"status": "ok"}


async def mock_save_transcript_batch(call_id, transcripts):
    """Mock transcript batch save."""
    return {"saved": len(transcripts)}


@pytest.fixture
async def workflow_env():
    """Create test workflow environment."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@pytest.mark.asyncio
async def test_successful_call_workflow(workflow_env):
    """Test successful call workflow execution."""
    async with Worker(
        workflow_env.client,
        task_queue="test-queue",
        workflows=[VoiceCallWorkflowRefactored],
        activities={
            "create_call_record": mock_create_call_record,
            "initiate_twilio_call": mock_initiate_twilio_call,
            "terminate_twilio_call": mock_terminate_twilio_call,
            "update_call_record": mock_update_call_record,
            "save_transcript_batch": mock_save_transcript_batch,
        },
    ):
        # Start workflow
        handle = await workflow_env.client.start_workflow(
            VoiceCallWorkflowRefactored.run,
            CallWorkflowInput(
                phone_number="+1234567890",
                greeting="Hello, how can I help?",
                system_prompt="Be helpful",
                max_duration_seconds=300,
            ),
            id="test-workflow-1",
            task_queue="test-queue",
        )

        # Simulate call connecting
        await handle.signal(
            VoiceCallWorkflowRefactored.call_status_changed, "in-progress"
        )

        # Simulate streaming
        await handle.signal(
            VoiceCallWorkflowRefactored.streaming_started,
            {"stream_sid": "stream-123", "call_sid": "call-123"},
        )

        # Send transcript batch (not individual frames!)
        await handle.signal(
            VoiceCallWorkflowRefactored.transcripts_available,
            [
                {
                    "speaker": "USER",
                    "text": "Hello, I need help",
                    "timestamp": "2024-01-01T00:00:00",
                    "confidence": 0.95,
                },
                {
                    "speaker": "AI",
                    "text": "I'm here to help you",
                    "timestamp": "2024-01-01T00:00:01",
                    "confidence": 1.0,
                },
            ],
        )

        # Check transcript count
        count = await handle.query(
            VoiceCallWorkflowRefactored.get_transcript_count
        )
        assert count == 2

        # End call
        await handle.signal(
            VoiceCallWorkflowRefactored.streaming_ended,
            {"stream_sid": "stream-123"},
        )

        # Get result
        result = await handle.result()
        assert result.status == CallStatus.COMPLETED
        assert result.total_transcript_segments == 2
        assert result.call_sid == "mock-twilio-sid-456"


@pytest.mark.asyncio
async def test_call_no_answer_workflow(workflow_env):
    """Test workflow when call is not answered."""
    async with Worker(
        workflow_env.client,
        task_queue="test-queue",
        workflows=[VoiceCallWorkflowRefactored],
        activities={
            "create_call_record": mock_create_call_record,
            "initiate_twilio_call": mock_initiate_twilio_call,
            "terminate_twilio_call": mock_terminate_twilio_call,
            "update_call_record": mock_update_call_record,
            "save_transcript_batch": mock_save_transcript_batch,
        },
    ):
        handle = await workflow_env.client.start_workflow(
            VoiceCallWorkflowRefactored.run,
            CallWorkflowInput(
                phone_number="+1234567890",
                max_duration_seconds=30,
            ),
            id="test-workflow-2",
            task_queue="test-queue",
        )

        # Simulate no answer
        await handle.signal(
            VoiceCallWorkflowRefactored.call_status_changed, "no-answer"
        )

        result = await handle.result()
        assert result.status == CallStatus.NO_ANSWER
        assert result.total_transcript_segments == 0


@pytest.mark.asyncio
async def test_workflow_status_mapping():
    """Test Twilio status to CallStatus mapping."""
    workflow = VoiceCallWorkflowRefactored()

    # Test status mapping
    test_cases = [
        ("initiated", CallStatus.INITIATED),
        ("ringing", CallStatus.RINGING),
        ("in-progress", CallStatus.IN_PROGRESS),
        ("completed", CallStatus.COMPLETED),
        ("busy", CallStatus.BUSY),
        ("no-answer", CallStatus.NO_ANSWER),
        ("failed", CallStatus.FAILED),
        ("canceled", CallStatus.CANCELED),
    ]

    for twilio_status, expected_status in test_cases:
        workflow.status = CallStatus.INITIATED
        await workflow.call_status_changed(twilio_status)
        assert workflow.status == expected_status


@pytest.mark.asyncio
async def test_workflow_queries(workflow_env):
    """Test workflow query handlers."""
    async with Worker(
        workflow_env.client,
        task_queue="test-queue",
        workflows=[VoiceCallWorkflowRefactored],
        activities={
            "create_call_record": mock_create_call_record,
            "initiate_twilio_call": mock_initiate_twilio_call,
            "terminate_twilio_call": mock_terminate_twilio_call,
            "update_call_record": mock_update_call_record,
            "save_transcript_batch": mock_save_transcript_batch,
        },
    ):
        handle = await workflow_env.client.start_workflow(
            VoiceCallWorkflowRefactored.run,
            CallWorkflowInput(
                phone_number="+1234567890",
                greeting="Test greeting",
                system_prompt="Test prompt",
            ),
            id="test-workflow-3",
            task_queue="test-queue",
        )

        # Query call config
        config = await handle.query(VoiceCallWorkflowRefactored.get_call_config)
        assert config["greeting"] == "Test greeting"
        assert config["system_prompt"] == "Test prompt"

        # Query status
        status = await handle.query(VoiceCallWorkflowRefactored.get_call_status)
        assert status == "initiated"

        # Signal status change
        await handle.signal(
            VoiceCallWorkflowRefactored.call_status_changed, "ringing"
        )

        # Query updated status
        status = await handle.query(VoiceCallWorkflowRefactored.get_call_status)
        assert status == "ringing"

        # End workflow
        await handle.signal(
            VoiceCallWorkflowRefactored.call_status_changed, "canceled"
        )
        await handle.result()