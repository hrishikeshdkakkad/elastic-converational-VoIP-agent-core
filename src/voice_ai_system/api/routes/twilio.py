"""
Refactored Twilio WebSocket routes with audio streaming outside Temporal.

This implementation keeps real-time audio processing out of Temporal's hot path,
only using Temporal for coarse-grained events and orchestration.
"""

import asyncio
import json
import logging
from typing import Any, Dict

import structlog
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from temporalio.client import Client as TemporalClient

from src.voice_ai_system.services.audio_bridge import audio_bridge_manager
from src.voice_ai_system.workflows.call_workflow import VoiceCallWorkflow

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.websocket("/ws/media/{workflow_id}")
async def media_stream_handler(websocket: WebSocket, workflow_id: str):
    """
    Handle Twilio Media Stream WebSocket with audio bridge outside Temporal.

    Key changes:
    - Audio flows directly between Twilio and Gemini via audio_bridge
    - Temporal only receives periodic transcript updates (not every frame)
    - Dramatically reduces Temporal activity load
    """
    await websocket.accept()
    logger.info(f"Media stream WebSocket connected for workflow {workflow_id}")

    # Get Temporal client and workflow handle
    temporal_client: TemporalClient = websocket.app.state.temporal_client
    handle = temporal_client.get_workflow_handle(workflow_id)

    # Session state
    stream_sid = None
    audio_session = None
    transcript_task = None

    try:
        while True:
            # Receive message from Twilio
            message = await websocket.receive_json()
            event_type = message.get("event")

            if event_type == "start":
                # Stream started - initialize audio bridge
                start_data = message["start"]
                stream_sid = start_data["streamSid"]
                call_sid = start_data["callSid"]

                logger.info(
                    f"Media stream started: workflow={workflow_id}, "
                    f"stream={stream_sid}, call={call_sid}"
                )

                # Get call configuration from workflow (one-time query)
                call_config = await handle.query(VoiceCallWorkflow.get_call_config)

                # Create audio bridge session (outside Temporal)
                audio_session = await audio_bridge_manager.create_session(
                    session_id=stream_sid,
                    call_id=str(call_config.get("call_id")),
                    greeting=call_config.get("greeting", ""),
                    system_prompt=call_config.get("system_prompt"),
                )

                # Start periodic transcript sync task
                transcript_task = asyncio.create_task(
                    _sync_transcripts_to_workflow(audio_session, handle)
                )

                # Signal Temporal that streaming has started (coarse event)
                await handle.signal(
                    VoiceCallWorkflow.streaming_started,
                    {"stream_sid": stream_sid, "call_sid": call_sid}
                )

            elif event_type == "media":
                # Audio chunk received - process directly without Temporal
                if audio_session:
                    media_data = message["media"]
                    audio_base64 = media_data["payload"]

                    # Send to audio bridge (bypasses Temporal)
                    # Use create_task to avoid blocking the WebSocket event loop
                    asyncio.create_task(audio_session.send_audio_from_twilio(audio_base64))

                    # Check for response audio (non-blocking)
                    response_audio = await audio_session.receive_audio_for_twilio()
                    if response_audio:
                        # Send back to Twilio
                        media_message = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": response_audio},
                        }
                        await websocket.send_json(media_message)

            elif event_type == "stop":
                # Stream stopped
                logger.info(f"Media stream stopped: {stream_sid}")

                # Signal Temporal that streaming has ended (coarse event)
                await handle.signal(
                    VoiceCallWorkflow.streaming_ended,
                    {"stream_sid": stream_sid}
                )
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {workflow_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Cleanup
        if transcript_task:
            transcript_task.cancel()

        if audio_session:
            # Send final transcripts to workflow
            final_transcripts = await audio_session.get_transcript_buffer()
            if final_transcripts:
                await handle.signal(
                    VoiceCallWorkflow.transcripts_available,
                    [t.model_dump() for t in final_transcripts]
                )

            # Close audio bridge session
            await audio_bridge_manager.close_session(stream_sid)

        # Ensure workflow knows streaming ended
        try:
            await handle.signal(
                VoiceCallWorkflow.streaming_ended,
                {"stream_sid": stream_sid}
            )
        except:
            pass


async def _sync_transcripts_to_workflow(audio_session, workflow_handle):
    """
    Periodically sync transcripts from audio bridge to Temporal workflow.
    This reduces Temporal load from every-frame to periodic updates.
    """
    while True:
        try:
            await asyncio.sleep(2.0)  # Sync every 2 seconds instead of every 20ms

            # Get accumulated transcripts
            transcripts = await audio_session.get_transcript_buffer()

            if transcripts:
                # Send batch to workflow (one signal instead of hundreds)
                await workflow_handle.signal(
                    VoiceCallWorkflow.transcripts_available,
                    [t.model_dump() for t in transcripts]
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error syncing transcripts: {e}")


@router.post("/twiml/{workflow_id}")
async def generate_twiml(workflow_id: str, request: Request):
    """
    Generate TwiML for Twilio with WebSocket streaming.
    This endpoint remains unchanged as it just sets up the connection.
    """
    logger.info(f"Generating TwiML for workflow {workflow_id}")

    settings = request.app.state.settings
    temporal_client: TemporalClient = request.app.state.temporal_client

    # Verify workflow exists
    try:
        handle = temporal_client.get_workflow_handle(workflow_id)
        call_info = await handle.query(VoiceCallWorkflow.get_call_status)
        logger.info(f"Call status for {workflow_id}: {call_info}")
    except Exception as e:
        logger.error(f"Failed to get workflow {workflow_id}: {e}")
        return {"error": "Workflow not found"}, 404

    # Generate WebSocket URL
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{request.url.hostname}/ws/media/{workflow_id}"

    # Generate TwiML
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="workflow_id" value="{workflow_id}" />
        </Stream>
    </Connect>
</Response>"""

    return twiml, 200, {"Content-Type": "application/xml"}


@router.post("/status/{workflow_id}")
async def handle_status_callback(workflow_id: str, request: Request):
    """
    Handle Twilio status callbacks.
    Signals workflow about major call state changes only.
    """
    form_data = await request.form()
    call_status = form_data.get("CallStatus")
    call_sid = form_data.get("CallSid")

    logger.info(f"Call status update: workflow={workflow_id}, status={call_status}")

    temporal_client: TemporalClient = request.app.state.temporal_client

    try:
        handle = temporal_client.get_workflow_handle(workflow_id)

        # Signal major status changes to workflow
        await handle.signal(
            VoiceCallWorkflow.call_status_changed,
            call_status
        )

        # Store call SID if this is the answered event
        if call_status == "in-progress" and call_sid:
            await handle.signal(
                VoiceCallWorkflow.set_call_sid,
                call_sid
            )

    except Exception as e:
        logger.error(f"Failed to signal workflow {workflow_id}: {e}")

    return {"status": "ok"}