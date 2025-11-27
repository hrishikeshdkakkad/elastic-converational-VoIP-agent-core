"""
Refactored Twilio WebSocket routes with audio streaming outside Temporal.

This implementation keeps real-time audio processing out of Temporal's hot path,
only using Temporal for coarse-grained events and orchestration.
"""

import asyncio
from datetime import datetime

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
    - All background tasks are tracked and properly cleaned up
    """
    await websocket.accept()

    # Track WebSocket connection time
    websocket_connected_at = datetime.utcnow()
    logger.info(f"Media stream WebSocket connected for workflow {workflow_id} at {websocket_connected_at.isoformat()}")

    # Get Temporal client and workflow handle
    temporal_client: TemporalClient = websocket.app.state.temporal_client
    handle = temporal_client.get_workflow_handle(workflow_id)

    # Session state
    stream_sid = None
    audio_session = None
    streaming_ended_sent = False  # Track if we've signaled streaming_ended

    # Track ALL background tasks for proper cleanup
    background_tasks: set[asyncio.Task] = set()

    def _create_tracked_task(coro, name: str = None) -> asyncio.Task:
        """Create a task and track it for cleanup."""
        task = asyncio.create_task(coro, name=name)
        background_tasks.add(task)
        # Auto-remove from set when task completes
        task.add_done_callback(background_tasks.discard)
        return task

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

                streaming_started_at = datetime.utcnow()
                logger.info(
                    f"Media stream started: workflow={workflow_id}, "
                    f"stream={stream_sid}, call={call_sid}, "
                    f"started_at={streaming_started_at.isoformat()}"
                )

                # Get call configuration from workflow (one-time query)
                call_config = await handle.query(VoiceCallWorkflow.get_call_config)

                # Track WebSocket metrics via Temporal activity (tracked task)
                _create_tracked_task(
                    _update_websocket_metrics(
                        temporal_client,
                        workflow_id,
                        call_config.get("call_id"),
                        websocket_connected_at,
                        streaming_started_at,
                        call_sid,
                        stream_sid
                    ),
                    name=f"metrics-update-{stream_sid}"
                )

                # Default VAD configuration optimized for phone calls
                # Note: silence_duration_ms controls how long silence triggers end-of-speech
                # Too short (100ms) = cuts off mid-sentence; too long (1000ms+) = slow response
                vad_config = {
                    "disabled": False,  # VAD must be enabled for Gemini to detect when to speak
                    "start_sensitivity": "HIGH",  # More sensitive to detect speech start quickly
                    "end_sensitivity": "LOW",  # Less sensitive to avoid cutting off mid-sentence
                    "prefix_padding_ms": 200,  # Buffer before speech detection
                    "silence_duration_ms": 500  # 500ms silence = end of speech (reasonable pause)
                }

                # Override with call-specific VAD config if provided
                if call_config.get("vad_config"):
                    vad_config.update(call_config.get("vad_config"))

                # Create or reuse prewarmed audio bridge session (outside Temporal)
                audio_session = await audio_bridge_manager.get_or_create_session(
                    session_id=stream_sid,
                    workflow_id=workflow_id,
                    call_id=str(call_config.get("call_id")),
                    greeting=call_config.get("greeting", ""),
                    system_prompt=call_config.get("system_prompt"),
                    vad_config=vad_config,
                )

                # Start dedicated playback task (20ms cadence, independent of inbound frames)
                _create_tracked_task(
                    _playback_task(audio_session, websocket, stream_sid),
                    name=f"playback-{stream_sid}"
                )

                # CRITICAL: Immediately flush any pre-warmed audio to avoid silence
                # Pre-warming generates audio before call connects - send it now!
                _create_tracked_task(
                    _flush_prewarmed_audio(audio_session, websocket, stream_sid),
                    name=f"flush-prewarm-{stream_sid}"
                )

                # Start periodic transcript sync task
                _create_tracked_task(
                    _sync_transcripts_to_workflow(audio_session, handle),
                    name=f"transcript-sync-{stream_sid}"
                )

                # Start periodic metrics sync task
                _create_tracked_task(
                    _sync_metrics_to_workflow(audio_session, handle, workflow_id),
                    name=f"metrics-sync-{stream_sid}"
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
                    # Use tracked task to avoid blocking the WebSocket event loop
                    _create_tracked_task(
                        audio_session.send_audio_from_twilio(audio_base64),
                        name=f"audio-send-{stream_sid}"
                    )

                    # NOTE: Outbound audio is now handled by dedicated playback_task
                    # We no longer poll for audio here to avoid gating responses on inbound frames

            elif event_type == "stop":
                # Stream stopped
                logger.info(f"Media stream stopped: {stream_sid}")

                # Signal Temporal that streaming has ended (coarse event)
                # Guard: only send if we have a stream_sid and haven't sent already
                if stream_sid and not streaming_ended_sent:
                    await handle.signal(
                        VoiceCallWorkflow.streaming_ended,
                        {"stream_sid": stream_sid}
                    )
                    streaming_ended_sent = True
                    logger.info(f"Sent streaming_ended signal for {stream_sid}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {workflow_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Cancel ALL tracked background tasks
        if background_tasks:
            logger.info(f"Cancelling {len(background_tasks)} background tasks for {workflow_id}")
            for task in background_tasks:
                if not task.done():
                    task.cancel()

            # Wait for all tasks to complete cancellation (with timeout)
            if background_tasks:
                try:
                    await asyncio.wait(background_tasks, timeout=2.0)
                except Exception as e:
                    logger.warning(f"Error waiting for task cancellation: {e}")

        if audio_session:
            # Send final transcripts to workflow
            try:
                final_transcripts = await audio_session.get_transcript_buffer()
                if final_transcripts:
                    await handle.signal(
                        VoiceCallWorkflow.transcripts_available,
                        [t.model_dump() for t in final_transcripts]
                    )
            except Exception as e:
                logger.warning(f"Failed to send final transcripts: {e}")

            # Close audio bridge session
            await audio_bridge_manager.close_session(stream_sid)

        # Signal streaming ended ONLY if not already sent
        # This prevents duplicate signals when "stop" event was received
        if stream_sid and not streaming_ended_sent:
            try:
                await handle.signal(
                    VoiceCallWorkflow.streaming_ended,
                    {"stream_sid": stream_sid}
                )
                logger.info(f"Sent streaming_ended signal for {stream_sid} (cleanup path)")
            except Exception as e:
                logger.warning(f"Failed to signal streaming_ended in cleanup: {e}")


async def _flush_prewarmed_audio(audio_session, websocket, stream_sid: str):
    """
    Immediately flush any pre-warmed audio to avoid initial silence.

    When using pre-warming, Gemini generates audio before the call connects.
    This function sends that buffered audio immediately to eliminate the
    several-second delay users experience.
    """
    try:
        flushed_frames = 0
        start_time = asyncio.get_event_loop().time()
        max_wait_time = 2.0  # Wait up to 2 seconds for pre-warmed audio
        empty_attempts = 0
        max_empty_attempts = 5  # Try a few times even if queue appears empty

        logger.info(f"Starting aggressive pre-warm audio flush for stream {stream_sid}")

        # Aggressively drain the queue with longer timeout for pre-warmed audio
        while (asyncio.get_event_loop().time() - start_time) < max_wait_time:
            # Use longer timeout (100ms) to catch pre-warmed audio still being processed
            response_audio = await audio_session.receive_audio_for_twilio(timeout=0.1)

            if not response_audio:
                empty_attempts += 1
                if empty_attempts >= max_empty_attempts:
                    break
                # Brief wait before retrying
                await asyncio.sleep(0.05)
                continue

            # Reset empty attempts counter when we find audio
            empty_attempts = 0
            flushed_frames += 1

            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": response_audio},
            }
            await websocket.send_json(media_message)

            # Tiny sleep to prevent blocking but stay aggressive
            if flushed_frames % 10 == 0:
                await asyncio.sleep(0.001)

        elapsed = asyncio.get_event_loop().time() - start_time
        if flushed_frames > 0:
            logger.info(
                f"Successfully flushed {flushed_frames} pre-warmed audio frames in {elapsed:.3f}s "
                f"for stream {stream_sid}"
            )
        else:
            logger.warning(
                f"No pre-warmed audio found after {elapsed:.3f}s for stream {stream_sid}"
            )
    except Exception as e:
        logger.error(f"Error flushing pre-warmed audio: {e}")


async def _playback_task(audio_session, websocket, stream_sid: str):
    """
    Dedicated playback task that drains audio_in_queue on a 20ms cadence.

    This decouples outbound audio from inbound media events, ensuring Gemini's
    responses are sent immediately regardless of whether the caller is speaking.

    Critical fix: Without this, Gemini audio sits in queue until next inbound frame.
    """
    frame_count = 0
    try:
        while True:
            # Poll at 20ms intervals (typical audio frame duration)
            await asyncio.sleep(0.020)

            # Drain all available audio from queue
            response_audio = await audio_session.receive_audio_for_twilio()
            if response_audio:
                frame_count += 1
                # Send immediately to Twilio
                media_message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": response_audio},
                }
                await websocket.send_json(media_message)

                if frame_count % 50 == 0:
                    logger.debug(f"Sent {frame_count} audio frames to Twilio for stream {stream_sid}")

    except asyncio.CancelledError:
        logger.info(f"Playback task cancelled after sending {frame_count} frames")
    except Exception as e:
        logger.error(f"Error in playback task: {e}")


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


async def _sync_metrics_to_workflow(audio_session, workflow_handle, workflow_id: str):
    """
    Periodically sync metrics from audio bridge to Temporal workflow.
    Sends all tracked metrics including audio frames, queue depth, turns, etc.
    """
    while True:
        try:
            await asyncio.sleep(5.0)  # Sync metrics every 5 seconds

            # Get current metrics from audio bridge
            metrics = audio_session.get_metrics()

            # Add workflow_id for activity processing
            metrics["workflow_id"] = workflow_id

            # Send metrics to workflow
            await workflow_handle.signal(
                VoiceCallWorkflow.update_metrics,
                metrics
            )

            logger.debug(f"Synced metrics to workflow {workflow_id}: {metrics}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error syncing metrics: {e}")


@router.post("/twiml/{workflow_id}")
async def generate_twiml(workflow_id: str, request: Request):
    """
    Generate TwiML for Twilio with WebSocket streaming.
    This endpoint remains unchanged as it just sets up the connection.
    """
    logger.info(f"Generating TwiML for workflow {workflow_id}")

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

    # Generate TwiML - no <Say> needed since pre-warmed Gemini audio plays immediately
    # The pre-warming system now handles the greeting with near-zero latency
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

        # Track call_answered_at timestamp for metrics
        if call_status in ["answered", "in-progress"]:
            from datetime import datetime
            call_answered_at = datetime.utcnow()
            logger.info(f"Call answered at {call_answered_at.isoformat()}")

            # Send metrics update with call_answered_at
            await handle.signal(
                VoiceCallWorkflow.update_metrics,
                {
                    "workflow_id": workflow_id,
                    "call_answered_at": call_answered_at.isoformat(),
                }
            )

    except Exception as e:
        logger.error(f"Failed to signal workflow {workflow_id}: {e}")

    return {"status": "ok"}


@router.post("/stream-status/{workflow_id}")
async def handle_stream_status_callback(workflow_id: str, request: Request):
    """
    Handle Twilio Stream status callbacks from the <Stream> element.

    Prevents Twilio retry/backoff when the callback URL returns 404 and gives us
    visibility into stream lifecycle events (start, stop, media server ack).
    """
    content_type = request.headers.get("content-type", "")
    stream_sid = status = event = None

    try:
        if "application/json" in content_type:
            payload = await request.json()
            stream_sid = payload.get("StreamSid") or payload.get("streamSid")
            status = payload.get("Status") or payload.get("status")
            event = payload.get("Event") or payload.get("event")
        else:
            form_data = await request.form()
            stream_sid = form_data.get("StreamSid") or form_data.get("streamSid")
            status = form_data.get("Status") or form_data.get("status")
            event = form_data.get("Event") or form_data.get("event")
    except Exception as exc:
        # Always respond 200 to avoid Twilio retries; log for debugging
        try:
            raw_body = (await request.body()).decode(errors="ignore")
        except Exception:
            raw_body = "<unavailable>"
        logger.warning(
            "Stream status parse failed",
            workflow_id=workflow_id,
            error=str(exc),
            content_type=content_type,
            body_preview=raw_body[:500],
        )
        return {"status": "ok"}

    logger.info(
        "Stream status update",
        workflow_id=workflow_id,
        stream_sid=stream_sid,
        status=status,
        stream_event=event,
    )

    return {"status": "ok"}


async def _update_websocket_metrics(
    temporal_client: TemporalClient,
    workflow_id: str,
    call_id: str,
    websocket_connected_at: datetime,
    streaming_started_at: datetime,
    call_sid: str,
    stream_sid: str
) -> None:
    """
    Update WebSocket connection and streaming metrics in the database.
    """
    try:
        # Get workflow handle
        handle = temporal_client.get_workflow_handle(workflow_id)

        # Prepare metrics data
        metrics_data = {
            "workflow_id": workflow_id,
            "call_id": call_id,
            "websocket_connected_at": websocket_connected_at.isoformat(),
            "streaming_started_at": streaming_started_at.isoformat(),
            "twilio_call_sid": call_sid,
            "twilio_stream_sid": stream_sid,
        }

        # Signal the workflow to update its metrics
        await handle.signal(VoiceCallWorkflow.update_metrics, metrics_data)

        logger.info(
            f"Metrics update triggered for workflow {workflow_id}: "
            f"WebSocket connected at {websocket_connected_at.isoformat()}, "
            f"streaming started at {streaming_started_at.isoformat()}"
        )

    except Exception as e:
        logger.error(f"Failed to update WebSocket metrics: {e}")
