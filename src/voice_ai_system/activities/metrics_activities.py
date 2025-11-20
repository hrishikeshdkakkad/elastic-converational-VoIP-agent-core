"""Temporal activities for metrics tracking."""

from typing import Any, Optional
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import select
from temporalio import activity

from src.voice_ai_system.services.database import get_db_session
from src.voice_ai_system.models.database import Call, CallMetrics


def _parse_timestamp(value: Any, label: str) -> Optional[datetime]:
    """
    Normalize incoming timestamps to UTC naive datetime (what our DB columns expect).
    Returns None if parsing fails.
    """
    if value is None:
        return None

    dt = None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            activity.logger.warning(f"Could not parse {label}: {value}")
            return None
    else:
        activity.logger.warning(f"Unexpected type for {label}: {type(value)}")
        return None

    # Store naive UTC to match DB column definition (no timezone info)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

    return dt


@activity.defn(name="create_or_update_call_metrics")
async def create_or_update_call_metrics(params: dict[str, Any]) -> dict[str, Any]:
    """
    Create or update call metrics record.

    Args:
        params: Dictionary containing:
            - call_id: Call UUID
            - workflow_id: Workflow identifier
            - metrics: Dictionary of metrics to update

    Returns:
        Dictionary with metrics ID and status
    """
    call_id = params["call_id"]
    workflow_id = params["workflow_id"]
    metrics_data = params.get("metrics", {})

    activity.logger.info(f"Updating metrics for call {call_id}", extra={"metrics": metrics_data})

    async with get_db_session() as session:
        # Try to get existing metrics record
        result = await session.execute(
            select(CallMetrics).where(CallMetrics.call_id == UUID(call_id))
        )
        metrics = result.scalar_one_or_none()

        if not metrics:
            # Create new metrics record
            metrics = CallMetrics(
                call_id=UUID(call_id),
                workflow_id=workflow_id,
                created_at=datetime.utcnow()
            )
            session.add(metrics)
            activity.logger.info(f"Created new metrics record for call {call_id}")
        else:
            metrics.updated_at = datetime.utcnow()
            activity.logger.info(f"Updating existing metrics record for call {call_id}")

        # Update metrics fields
        for key, value in metrics_data.items():
            if not hasattr(metrics, key):
                continue

            if key.endswith("_at"):
                parsed = _parse_timestamp(value, key)
                if parsed is None:
                    continue
                setattr(metrics, key, parsed)
            else:
                if value is not None:
                    setattr(metrics, key, value)

        await session.commit()
        await session.refresh(metrics)

        return {
            "id": str(metrics.id),
            "call_id": str(metrics.call_id),
            "status": "updated"
        }


@activity.defn(name="update_websocket_connection_time")
async def update_websocket_connection_time(params: dict[str, Any]) -> dict[str, Any]:
    """
    Update the WebSocket connection timing metric.

    Args:
        params: Dictionary containing:
            - workflow_id: Workflow identifier
            - call_initiated_at: When the call was initiated
            - websocket_connected_at: When the WebSocket connected

    Returns:
        Dictionary with calculated time_to_websocket_ms
    """
    workflow_id = params["workflow_id"]
    call_initiated_at = params["call_initiated_at"]
    websocket_connected_at = params["websocket_connected_at"]

    activity.logger.info(f"Updating WebSocket connection time for workflow {workflow_id}")

    # Parse timestamps
    call_initiated_at = _parse_timestamp(call_initiated_at, "call_initiated_at")
    websocket_connected_at = _parse_timestamp(websocket_connected_at, "websocket_connected_at")

    if not call_initiated_at or not websocket_connected_at:
        activity.logger.warning("Skipping websocket connection time update due to missing timestamps")
        return {"error": "missing timestamps"}

    # Calculate time difference in milliseconds
    time_diff = websocket_connected_at - call_initiated_at
    time_to_websocket_ms = int(time_diff.total_seconds() * 1000)

    activity.logger.info(f"WebSocket connected after {time_to_websocket_ms}ms for workflow {workflow_id}")

    # Update metrics in database
    async with get_db_session() as session:
        # Get call record
        result = await session.execute(
            select(Call).where(Call.workflow_id == workflow_id)
        )
        call = result.scalar_one_or_none()

        if not call:
            activity.logger.warning(f"Call not found for workflow {workflow_id}")
            return {"error": "Call not found"}

        # Update or create metrics
        result = await session.execute(
            select(CallMetrics).where(CallMetrics.call_id == call.id)
        )
        metrics = result.scalar_one_or_none()

        if not metrics:
            metrics = CallMetrics(
                call_id=call.id,
                workflow_id=workflow_id,
                created_at=datetime.utcnow()
            )
            session.add(metrics)

        metrics.call_initiated_at = call_initiated_at.replace(tzinfo=None)
        metrics.websocket_connected_at = websocket_connected_at.replace(tzinfo=None)
        metrics.time_to_websocket_ms = time_to_websocket_ms
        metrics.updated_at = datetime.utcnow()

        await session.commit()

        return {
            "workflow_id": workflow_id,
            "time_to_websocket_ms": time_to_websocket_ms
        }


@activity.defn(name="update_streaming_metrics")
async def update_streaming_metrics(params: dict[str, Any]) -> dict[str, Any]:
    """
    Update streaming and audio metrics.

    Args:
        params: Dictionary containing various streaming metrics

    Returns:
        Dictionary with update status
    """
    workflow_id = params["workflow_id"]

    activity.logger.info(f"Updating streaming metrics for workflow {workflow_id}")

    async with get_db_session() as session:
        # Get call record
        result = await session.execute(
            select(Call).where(Call.workflow_id == workflow_id)
        )
        call = result.scalar_one_or_none()

        if not call:
            activity.logger.warning(f"Call not found for workflow {workflow_id}")
            return {"error": "Call not found"}

        # Get or create metrics
        result = await session.execute(
            select(CallMetrics).where(CallMetrics.call_id == call.id)
        )
        metrics = result.scalar_one_or_none()

        if not metrics:
            metrics = CallMetrics(
                call_id=call.id,
                workflow_id=workflow_id,
                created_at=datetime.utcnow()
            )
            session.add(metrics)

        # Update streaming timestamps
        metrics.websocket_connected_at = _parse_timestamp(params.get("websocket_connected_at"), "websocket_connected_at") or metrics.websocket_connected_at
        metrics.call_answered_at = _parse_timestamp(params.get("call_answered_at"), "call_answered_at") or metrics.call_answered_at
        metrics.streaming_started_at = _parse_timestamp(params.get("streaming_started_at"), "streaming_started_at") or metrics.streaming_started_at
        metrics.first_audio_frame_at = _parse_timestamp(params.get("first_audio_frame_at"), "first_audio_frame_at") or metrics.first_audio_frame_at

        # Calculate timing metrics
        if metrics.call_initiated_at and metrics.websocket_connected_at:
            metrics.time_to_websocket_ms = int((metrics.websocket_connected_at - metrics.call_initiated_at).total_seconds() * 1000)

        if metrics.call_initiated_at and metrics.call_answered_at:
            metrics.time_to_answer_ms = int((metrics.call_answered_at - metrics.call_initiated_at).total_seconds() * 1000)

        if metrics.call_answered_at and metrics.streaming_started_at:
            metrics.time_to_streaming_ms = int((metrics.streaming_started_at - metrics.call_answered_at).total_seconds() * 1000)

        if metrics.streaming_started_at and metrics.first_audio_frame_at:
            metrics.time_to_first_audio_ms = int((metrics.first_audio_frame_at - metrics.streaming_started_at).total_seconds() * 1000)

        # Update audio metrics
        if "total_audio_frames_sent" in params:
            metrics.total_audio_frames_sent = params["total_audio_frames_sent"]
        if "total_audio_frames_received" in params:
            metrics.total_audio_frames_received = params["total_audio_frames_received"]
        if "total_audio_frames_dropped" in params:
            metrics.total_audio_frames_dropped = params["total_audio_frames_dropped"]
        if "audio_drop_rate_percent" in params:
            metrics.audio_drop_rate_percent = params["audio_drop_rate_percent"]
        if "max_audio_queue_depth" in params:
            metrics.max_audio_queue_depth = params["max_audio_queue_depth"]
        if "avg_audio_queue_depth" in params:
            metrics.avg_audio_queue_depth = params["avg_audio_queue_depth"]

        # Update VAD and interaction metrics
        if "vad_config" in params:
            metrics.vad_config = params["vad_config"]
        if "interruption_count" in params:
            metrics.interruption_count = params["interruption_count"]
        if "ai_turn_count" in params:
            metrics.ai_turn_count = params["ai_turn_count"]
        if "user_turn_count" in params:
            metrics.user_turn_count = params["user_turn_count"]

        # Update identifiers
        if "twilio_call_sid" in params:
            metrics.twilio_call_sid = params["twilio_call_sid"]
        if "twilio_stream_sid" in params:
            metrics.twilio_stream_sid = params["twilio_stream_sid"]

        metrics.updated_at = datetime.utcnow()
        await session.commit()

        return {
            "workflow_id": workflow_id,
            "status": "updated"
        }


@activity.defn(name="get_call_metrics")
async def get_call_metrics(call_id: str) -> dict[str, Any] | None:
    """
    Retrieve call metrics by call ID.

    Args:
        call_id: Call UUID

    Returns:
        Metrics data or None if not found
    """
    activity.logger.info(f"Retrieving metrics for call {call_id}")

    async with get_db_session() as session:
        result = await session.execute(
            select(CallMetrics).where(CallMetrics.call_id == UUID(call_id))
        )
        metrics = result.scalar_one_or_none()

        if not metrics:
            activity.logger.warning(f"Metrics not found for call {call_id}")
            return None

        return {
            "id": str(metrics.id),
            "call_id": str(metrics.call_id),
            "workflow_id": metrics.workflow_id,
            "time_to_websocket_ms": metrics.time_to_websocket_ms,
            "time_to_answer_ms": metrics.time_to_answer_ms,
            "time_to_streaming_ms": metrics.time_to_streaming_ms,
            "time_to_first_audio_ms": metrics.time_to_first_audio_ms,
            "total_audio_frames_sent": metrics.total_audio_frames_sent,
            "total_audio_frames_received": metrics.total_audio_frames_received,
            "total_audio_frames_dropped": metrics.total_audio_frames_dropped,
            "audio_drop_rate_percent": metrics.audio_drop_rate_percent,
            "vad_config": metrics.vad_config,
            "interruption_count": metrics.interruption_count,
            "ai_turn_count": metrics.ai_turn_count,
            "user_turn_count": metrics.user_turn_count,
            "call_completion_status": metrics.call_completion_status,
            "twilio_call_sid": metrics.twilio_call_sid,
            "twilio_stream_sid": metrics.twilio_stream_sid,
        }
