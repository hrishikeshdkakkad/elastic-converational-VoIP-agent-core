"""Call management endpoints."""

import asyncio
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from temporalio.client import WorkflowHandle

from src.voice_ai_system.models.call import CallWorkflowInput, CallWorkflowResult
from src.voice_ai_system.workflows.call_workflow import VoiceCallWorkflow

router = APIRouter()
logger = structlog.get_logger(__name__)


class InitiateCallRequest(BaseModel):
    """Request to initiate a new call."""

    phone_number: str = Field(..., description="Phone number to call (E.164 format)")
    greeting: str = Field(
        default="Hello! How can I help you today?",
        description="Initial greeting message",
    )
    system_prompt: str | None = Field(
        default=None, description="Custom system prompt for AI behavior"
    )
    max_duration_seconds: int = Field(
        default=1800, description="Maximum call duration in seconds"
    )


class CallResponse(BaseModel):
    """Response for call operations."""

    workflow_id: str
    run_id: str
    phone_number: str
    status: str


class CallStatusResponse(BaseModel):
    """Call status response."""

    workflow_id: str
    status: str
    transcript_count: int
    call_config: dict


@router.post("", response_model=CallResponse, status_code=status.HTTP_201_CREATED)
async def initiate_call(request: Request, call_request: InitiateCallRequest):
    """
    Initiate a new outbound call.

    This endpoint starts a Temporal workflow that orchestrates the entire call lifecycle.
    """
    from src.voice_ai_system.services.audio_bridge import audio_bridge_manager

    temporal_client = request.app.state.temporal_client
    settings = request.app.state.settings

    # Generate workflow ID
    workflow_id = f"call-{uuid4()}"
    prewarm_started = False

    logger.info(
        "Initiating call",
        workflow_id=workflow_id,
        phone_number=call_request.phone_number,
    )

    try:
        # Prepare workflow input
        workflow_input = CallWorkflowInput(
            phone_number=call_request.phone_number,
            greeting=call_request.greeting,
            system_prompt=call_request.system_prompt,
            max_duration_seconds=call_request.max_duration_seconds,
        )

        # Pre-warm Gemini session BEFORE starting workflow
        # This way if workflow fails, we can clean up the pre-warmed session
        # Pre-warming is fire-and-forget but tracked for cleanup
        asyncio.create_task(
            audio_bridge_manager.prewarm_session(
                workflow_id=workflow_id,
                greeting=call_request.greeting,
                system_prompt=call_request.system_prompt
            )
        )
        prewarm_started = True

        logger.info(
            "Gemini pre-warming initiated",
            workflow_id=workflow_id,
            optimization="pre-warming enabled"
        )

        # Start workflow
        handle: WorkflowHandle = await temporal_client.start_workflow(
            VoiceCallWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=settings.worker_task_queue,  # Use settings instead of hardcoded value
        )

        logger.info(
            "Call workflow started",
            workflow_id=workflow_id,
            run_id=handle.first_execution_run_id,
        )

        return CallResponse(
            workflow_id=workflow_id,
            run_id=handle.first_execution_run_id,
            phone_number=call_request.phone_number,
            status="initiated",
        )

    except Exception as e:
        logger.error("Failed to start call workflow", error=str(e), exc_info=True)

        # CRITICAL: Clean up pre-warmed session if workflow failed to start
        if prewarm_started:
            try:
                cleaned = await audio_bridge_manager.cleanup_prewarm(workflow_id)
                if cleaned:
                    logger.info(
                        "Cleaned up orphaned pre-warmed session",
                        workflow_id=workflow_id
                    )
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to cleanup pre-warmed session",
                    workflow_id=workflow_id,
                    error=str(cleanup_error)
                )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate call: {str(e)}",
        )


@router.get("/{workflow_id}", response_model=CallStatusResponse)
async def get_call_status(request: Request, workflow_id: str):
    """
    Get current status and transcript of an active or completed call.

    This endpoint queries the Temporal workflow for real-time status.
    """
    temporal_client = request.app.state.temporal_client

    logger.info("Querying call status", workflow_id=workflow_id)

    try:
        # Get workflow handle
        handle: WorkflowHandle = temporal_client.get_workflow_handle(workflow_id)

        # Query workflow for current status
        call_status = await handle.query(VoiceCallWorkflow.get_call_status)

        # Query for transcript count (new method)
        transcript_count = await handle.query(VoiceCallWorkflow.get_transcript_count)

        # Query for call configuration
        call_config = await handle.query(VoiceCallWorkflow.get_call_config)

        return CallStatusResponse(
            workflow_id=workflow_id,
            status=call_status,
            transcript_count=transcript_count,
            call_config=call_config,
        )

    except Exception as e:
        logger.error("Failed to query call status", workflow_id=workflow_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call not found or error querying status: {str(e)}",
        )


@router.post("/{workflow_id}/terminate", status_code=status.HTTP_204_NO_CONTENT)
async def terminate_call(request: Request, workflow_id: str):
    """
    Terminate an active call.

    This sends a signal to the workflow to gracefully end the call.
    """
    temporal_client = request.app.state.temporal_client

    logger.info("Terminating call", workflow_id=workflow_id)

    try:
        # Get workflow handle
        handle: WorkflowHandle = temporal_client.get_workflow_handle(workflow_id)

        # Send signal to end call
        await handle.signal(VoiceCallWorkflow.call_status_changed, "completed")

        logger.info("Call termination signal sent", workflow_id=workflow_id)

        return None

    except Exception as e:
        logger.error("Failed to terminate call", workflow_id=workflow_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to terminate call: {str(e)}",
        )


@router.get("/{workflow_id}/result")
async def get_call_result(request: Request, workflow_id: str):
    """
    Get the final result of a completed call.

    This waits for the workflow to complete and returns the final result.
    """
    temporal_client = request.app.state.temporal_client

    logger.info("Getting call result", workflow_id=workflow_id)

    try:
        # Get workflow handle
        handle: WorkflowHandle = temporal_client.get_workflow_handle(workflow_id)

        # Wait for workflow to complete (with timeout)
        result: CallWorkflowResult = await handle.result()

        return result.model_dump()

    except Exception as e:
        logger.error("Failed to get call result", workflow_id=workflow_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get call result: {str(e)}",
        )
