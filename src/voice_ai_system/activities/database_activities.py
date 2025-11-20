"""Temporal activities for database operations."""

from typing import Any
from uuid import UUID, uuid4
from datetime import datetime

from sqlalchemy import select
from temporalio import activity

from src.voice_ai_system.services.database import get_db_session
from src.voice_ai_system.models.database import Call, Transcript, CallEvent, CallMetrics
from src.voice_ai_system.models.call import CallStatus, Speaker


@activity.defn(name="create_call_record")
async def create_call_record(params: dict[str, Any]) -> UUID:
    """
    Create a new call record in the database.

    Args:
        params: Dictionary containing:
            - workflow_id: Workflow identifier
            - run_id: Run identifier
            - phone_number: Phone number
            - status: Initial status
            - metadata: Additional metadata

    Returns:
        UUID of the created call record
    """
    activity.logger.info(
        f"Creating call record for {params['phone_number']}",
        extra={"workflow_id": params["workflow_id"]},
    )

    # Real database implementation
    async with get_db_session() as session:
        call = Call(
            workflow_id=params["workflow_id"],
            run_id=params["run_id"],
            phone_number=params["phone_number"],
            status=CallStatus(params["status"]),
            meta_data=params.get("metadata", {}),
        )

        session.add(call)
        await session.commit()
        await session.refresh(call)

        activity.logger.info(f"Call record created with ID: {call.id}")
        return call.id


@activity.defn(name="update_call_record")
async def update_call_record(call_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    Update an existing call record.

    Args:
        call_id: Call UUID
        updates: Dictionary of fields to update

    Returns:
        Updated call record data
    """
    activity.logger.info(f"Updating call record {call_id}", extra={"updates": updates})

    # Real database implementation
    async with get_db_session() as session:
        result = await session.execute(
            select(Call).where(Call.id == UUID(call_id))
        )
        call = result.scalar_one()

        for key, value in updates.items():
            # Handle special cases
            if key == "status" and not isinstance(value, CallStatus):
                value = CallStatus(value)
            # Deserialize ISO string timestamps to datetime objects (strip timezone for PostgreSQL)
            elif key in ("ended_at", "started_at"):
                if isinstance(value, str):
                    value = datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
                elif isinstance(value, datetime) and value.tzinfo is not None:
                    value = value.replace(tzinfo=None)
            setattr(call, key, value)

        await session.commit()
        await session.refresh(call)

        return {
            "id": str(call.id),
            "status": call.status.value if hasattr(call.status, 'value') else call.status,
            "duration_seconds": call.duration_seconds,
        }


@activity.defn(name="mark_call_as_failed")
async def mark_call_as_failed(call_id: str) -> dict[str, Any]:
    """
    Mark a call as failed (compensation activity).

    Args:
        call_id: Call UUID

    Returns:
        Updated call record data
    """
    activity.logger.warning(f"Marking call {call_id} as failed")

    return await update_call_record(
        call_id, {"status": "failed", "ended_at": activity.now()}
    )


@activity.defn(name="save_transcript_batch")
async def save_transcript_batch(call_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Save a batch of transcript segments to the database.

    Args:
        call_id: Call UUID
        segments: List of transcript segment data

    Returns:
        Dictionary with save results
    """
    activity.logger.info(
        f"Saving batch of {len(segments)} transcript segments for call {call_id}"
    )

    saved_count = 0
    async with get_db_session() as session:
        for segment in segments:
            # Convert speaker string to enum if necessary
            speaker = segment["speaker"]
            if isinstance(speaker, str):
                speaker = Speaker(speaker)

            transcript = Transcript(
                call_id=UUID(call_id),
                speaker=speaker,
                text=segment["text"],
                confidence=segment.get("confidence"),
                meta_data=segment.get("metadata", {}),
            )

            session.add(transcript)
            saved_count += 1

        await session.commit()

    activity.logger.info(f"Saved {saved_count} transcript segments")
    return {"saved": saved_count}


@activity.defn(name="save_call_event")
async def save_call_event(call_id: str, event_type: str, event_data: dict[str, Any]) -> UUID:
    """
    Save a call lifecycle event to the database.

    Args:
        call_id: Call UUID
        event_type: Type of event
        event_data: Event data

    Returns:
        UUID of the created event record
    """
    activity.logger.info(
        f"Saving call event: call={call_id}, type={event_type}",
        extra={"event_type": event_type},
    )

    # Real database implementation
    async with get_db_session() as session:
        event = CallEvent(
            call_id=UUID(call_id),
            event_type=event_type,
            event_data=event_data,
        )

        session.add(event)
        await session.commit()
        await session.refresh(event)

        activity.logger.info(f"Call event saved with ID: {event.id}")
        return event.id


@activity.defn(name="get_call_transcripts")
async def get_call_transcripts(call_id: str) -> list[dict[str, Any]]:
    """
    Retrieve all transcript segments for a call.

    Args:
        call_id: Call UUID

    Returns:
        List of transcript segments
    """
    activity.logger.info(f"Retrieving transcripts for call {call_id}")

    # Real database implementation
    async with get_db_session() as session:
        result = await session.execute(
            select(Transcript)
            .where(Transcript.call_id == UUID(call_id))
            .order_by(Transcript.timestamp)
        )
        transcripts = result.scalars().all()

        transcript_list = [
            {
                "speaker": t.speaker.value if hasattr(t.speaker, 'value') else t.speaker,
                "text": t.text,
                "timestamp": t.timestamp.isoformat(),
                "confidence": t.confidence,
                "metadata": t.meta_data,
            }
            for t in transcripts
        ]

        activity.logger.info(f"Retrieved {len(transcript_list)} transcript segments for call {call_id}")
        return transcript_list


@activity.defn(name="get_call_by_workflow_id")
async def get_call_by_workflow_id(workflow_id: str) -> dict[str, Any] | None:
    """
    Retrieve call record by workflow ID.

    Args:
        workflow_id: Workflow identifier

    Returns:
        Call record data or None if not found
    """
    activity.logger.info(f"Retrieving call by workflow_id: {workflow_id}")

    # Real database implementation
    async with get_db_session() as session:
        result = await session.execute(
            select(Call).where(Call.workflow_id == workflow_id)
        )
        call = result.scalar_one_or_none()

        if not call:
            activity.logger.warning(f"Call not found for workflow_id: {workflow_id}")
            return None

        call_data = {
            "id": str(call.id),
            "workflow_id": call.workflow_id,
            "run_id": call.run_id,
            "phone_number": call.phone_number,
            "status": call.status.value if hasattr(call.status, 'value') else call.status,
            "started_at": call.started_at.isoformat() if call.started_at else None,
            "ended_at": call.ended_at.isoformat() if call.ended_at else None,
            "duration_seconds": call.duration_seconds,
            "call_sid": call.call_sid,
            "metadata": call.meta_data,
        }

        activity.logger.info(f"Retrieved call record: {call.id}")
        return call_data