"""
Temporal activities for Redis-based session management.

These activities create, update, and clean up session records in Redis,
providing shared state between FastAPI (real-time audio) and Temporal
(orchestration).
"""

from typing import Any
from datetime import datetime

from temporalio import activity

from src.voice_ai_system.utils.redis_client import redis_store


@activity.defn(name="create_session_record")
async def create_session_record(
    workflow_id: str,
    call_id: str,
    phone_number: str,
    greeting: str = "",
    system_prompt: str | None = None,
    max_duration_seconds: int = 1800
) -> dict[str, Any]:
    """
    Create a Redis session record for a new call.

    This allows FastAPI WebSocket handlers to discover session configuration
    when Twilio connects.

    Args:
        workflow_id: Temporal workflow ID
        call_id: Call UUID
        phone_number: Phone number being called
        greeting: Initial greeting message
        system_prompt: Custom system prompt for AI
        max_duration_seconds: Maximum call duration in seconds

    Returns:
        Created session data
    """
    activity.logger.info(
        f"Creating session record",
        extra={"workflow_id": workflow_id, "call_id": call_id}
    )

    session_data = await redis_store.create_session(
        workflow_id=workflow_id,
        call_id=call_id,
        phone_number=phone_number,
        greeting=greeting,
        system_prompt=system_prompt,
        max_duration_seconds=max_duration_seconds
    )

    # Add creation timestamp
    await redis_store.update_session_status(
        workflow_id,
        status="pending",
        created_at=datetime.utcnow().isoformat()
    )

    activity.logger.info(f"Session record created: {workflow_id}")
    return session_data


@activity.defn(name="update_session_status")
async def update_session_status(
    workflow_id: str,
    status: str,
    **additional_fields
) -> dict[str, Any]:
    """
    Update session status in Redis.

    Args:
        workflow_id: Temporal workflow ID
        status: New status (e.g., "in_progress", "completed", "failed")
        **additional_fields: Additional fields to update

    Returns:
        Result dictionary with success status
    """
    activity.logger.info(
        f"Updating session status to '{status}'",
        extra={"workflow_id": workflow_id}
    )

    success = await redis_store.update_session_status(
        workflow_id,
        status,
        **additional_fields
    )

    if not success:
        activity.logger.warning(f"Session not found: {workflow_id}")
        return {"success": False, "error": "Session not found"}

    return {"success": True, "status": status}


@activity.defn(name="cleanup_session_record")
async def cleanup_session_record(
    workflow_id: str,
    final_status: str = "completed",
    set_ttl: int = 300  # Keep for 5 minutes after completion
) -> dict[str, Any]:
    """
    Clean up session record in Redis after call ends.

    Instead of immediate deletion, we mark it as completed and set a short TTL.
    This allows any in-flight WebSocket messages to complete gracefully.

    Args:
        workflow_id: Temporal workflow ID
        final_status: Final status to set ("completed" or "failed")
        set_ttl: TTL in seconds before auto-deletion (default: 5 minutes)

    Returns:
        Cleanup result dictionary
    """
    activity.logger.info(
        f"Cleaning up session record",
        extra={"workflow_id": workflow_id, "final_status": final_status}
    )

    # Update status to final state
    success = await redis_store.update_session_status(
        workflow_id,
        status=final_status,
        ended_at=datetime.utcnow().isoformat()
    )

    if not success:
        activity.logger.warning(f"Session not found during cleanup: {workflow_id}")
        return {"success": False, "error": "Session not found"}

    # Set short TTL for graceful cleanup
    await redis_store.set_session_ttl(workflow_id, set_ttl)

    activity.logger.info(
        f"Session marked for cleanup (TTL: {set_ttl}s)",
        extra={"workflow_id": workflow_id}
    )

    return {
        "success": True,
        "final_status": final_status,
        "ttl": set_ttl
    }


@activity.defn(name="get_session_record")
async def get_session_record(workflow_id: str) -> dict[str, Any] | None:
    """
    Retrieve session record from Redis.

    Args:
        workflow_id: Temporal workflow ID

    Returns:
        Session data dictionary or None if not found
    """
    activity.logger.debug(f"Retrieving session record: {workflow_id}")

    session_data = await redis_store.get_session(workflow_id)

    if not session_data:
        activity.logger.warning(f"Session not found: {workflow_id}")
        return None

    return session_data
