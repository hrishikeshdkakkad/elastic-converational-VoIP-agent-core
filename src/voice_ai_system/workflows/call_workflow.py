"""
Refactored Voice Call Workflow - Temporal for orchestration only.

Key changes:
- No longer processes individual audio frames (handled by audio_bridge)
- Receives periodic transcript batches instead of real-time audio
- Focuses on call lifecycle, persistence, and error handling
- Dramatically reduces Temporal activity load
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from temporalio import workflow
from temporalio.common import RetryPolicy

from src.voice_ai_system.models.call import (
    CallStatus,
    CallWorkflowInput,
    CallWorkflowResult,
    TranscriptSegment,
)


@workflow.defn(name="VoiceCallWorkflow")
class VoiceCallWorkflow:
    """
    Refactored workflow focusing on orchestration, not media processing.
    Audio streaming happens outside Temporal via audio_bridge service.
    """

    def __init__(self) -> None:
        """Initialize workflow state."""
        # Workflow identification
        self.workflow_id: str = workflow.info().workflow_id
        self.run_id: str = workflow.info().run_id

        # Call state
        self.call_id: Optional[UUID] = None
        self.call_sid: Optional[str] = None
        self.phone_number: str = ""
        self.status: CallStatus = CallStatus.INITIATED
        self.started_at: Optional[datetime] = None
        self.ended_at: Optional[datetime] = None

        # Streaming state
        self.stream_sid: Optional[str] = None
        self.streaming_active: bool = False

        # Transcripts (received in batches, not per-frame)
        self.transcript_segments: list[TranscriptSegment] = []

        # Call configuration (for audio_bridge)
        self.greeting: str = ""
        self.system_prompt: Optional[str] = None

        # Control flags
        self.call_ended: bool = False
        self.max_duration_reached: bool = False

    @workflow.run
    async def run(self, input_data: CallWorkflowInput) -> CallWorkflowResult:
        """
        Main workflow execution - orchestration only.
        """
        workflow.logger.info(
            f"Starting refactored call workflow for {input_data.phone_number}"
        )

        self.phone_number = input_data.phone_number
        self.greeting = input_data.greeting
        self.system_prompt = input_data.system_prompt
        self.started_at = workflow.now()

        try:
            # Step 1: Initialize call in database
            self.call_id = await workflow.execute_activity(
                "create_call_record",
                args=[
                    {
                        "workflow_id": self.workflow_id,
                        "run_id": self.run_id,
                        "phone_number": self.phone_number,
                        "status": self.status.value,
                        "metadata": input_data.metadata,
                    }
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            workflow.logger.info(f"Call record created: {self.call_id}")

            # Step 2: Create Redis session record for audio_bridge
            await workflow.execute_activity(
                "create_session_record",
                args=[
                    self.workflow_id,
                    str(self.call_id),
                    self.phone_number,
                    self.greeting,
                    self.system_prompt,
                    input_data.max_duration_seconds,
                ],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            workflow.logger.info(f"Redis session record created: {self.workflow_id}")

            # Step 3: Initiate Twilio call
            result = await workflow.execute_activity(
                "initiate_twilio_call",
                args=[
                    {
                        "call_id": str(self.call_id),
                        "phone_number": self.phone_number,
                        "workflow_id": self.workflow_id,
                    }
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            self.call_sid = result["call_sid"]
            workflow.logger.info(f"Twilio call initiated: {self.call_sid}")

            # Step 4: Wait for call to connect with iterative verification
            # Uses multiple shorter timeouts with Twilio API verification to avoid race conditions
            # where status callbacks/WebSocket signals arrive just after timeout
            workflow.logger.info(f"Waiting for call to connect... status: {self.status}, streaming: {self.streaming_active}, call_ended: {self.call_ended}")

            call_actually_connected = False
            max_attempts = 3
            timeout_per_attempt = 12  # Total: 3 * 12 = 36 seconds

            for attempt in range(max_attempts):
                workflow.logger.info(f"🔍 Connection check attempt {attempt + 1}/{max_attempts}")

                # Wait for either success or failure condition
                connected = await workflow.wait_condition(
                    lambda: (
                        self.status == CallStatus.IN_PROGRESS
                        or self.streaming_active
                        or self.call_ended
                    ),
                    timeout=timedelta(seconds=timeout_per_attempt),
                )

                workflow.logger.info(
                    f"Wait attempt {attempt + 1} result: connected={connected}, "
                    f"status={self.status}, streaming={self.streaming_active}, "
                    f"call_ended={self.call_ended}"
                )

                # Check for definite failure (call explicitly ended)
                if self.call_ended:
                    workflow.logger.warning(f"❌ Call ended before connection (status: {self.status})")
                    break

                # Check for success via signals
                if self.status == CallStatus.IN_PROGRESS or self.streaming_active:
                    workflow.logger.info(f"✅ Call connected via signal (attempt {attempt + 1})")
                    call_actually_connected = True
                    break

                # Timeout occurred - verify with Twilio API before giving up
                if self.call_sid and attempt < max_attempts - 1:
                    workflow.logger.info(f"⏱️ Timeout on attempt {attempt + 1} - verifying with Twilio API...")

                    try:
                        call_state = await workflow.execute_activity(
                            "get_twilio_call_status",
                            args=[self.call_sid],
                            start_to_close_timeout=timedelta(seconds=5),
                        )

                        api_status = call_state.get("status", "unknown")
                        workflow.logger.info(f"📞 Twilio API reports status: {api_status}")

                        # Check if call is in active states (still has a chance to connect)
                        if api_status in ["ringing", "queued"]:
                            workflow.logger.info(f"Call still {api_status}, continuing to wait (attempt {attempt + 1})")
                            continue  # Keep waiting

                        elif api_status == "in-progress":
                            # API says connected but signal hasn't arrived yet - trust the API
                            workflow.logger.info("✅ Call connected per Twilio API (signal not yet received)")
                            self.status = CallStatus.IN_PROGRESS
                            call_actually_connected = True
                            break

                        elif api_status in ["completed", "busy", "no-answer", "failed", "canceled"]:
                            # Call has definitely failed
                            workflow.logger.warning(f"❌ Call failed per Twilio API: {api_status}")
                            self.call_ended = True
                            break

                        else:
                            # Unknown status - keep trying
                            workflow.logger.warning(f"⚠️ Unknown Twilio status '{api_status}', continuing...")
                            continue

                    except Exception as e:
                        workflow.logger.error(f"Failed to verify call status with Twilio API: {e}")
                        # On API error, continue with remaining attempts
                        continue

            # Final determination
            if not call_actually_connected:
                # Call never connected after all attempts
                workflow.logger.warning(
                    f"❌ Call failed to connect after {max_attempts} attempts "
                    f"(status={self.status}, streaming={self.streaming_active}, ended={self.call_ended})"
                )
                if self.status == CallStatus.INITIATED or self.status == CallStatus.RINGING:
                    self.status = CallStatus.NO_ANSWER
                await self._cleanup_call()
                return self._build_result()

            workflow.logger.info(f"✅ Call connected successfully! Status: {self.status}, Streaming: {self.streaming_active}")

            # Step 5: Monitor call until completion
            # Note: Audio streaming happens in audio_bridge, not here
            await workflow.wait_condition(
                lambda: self.call_ended or self.max_duration_reached,
                timeout=timedelta(seconds=input_data.max_duration_seconds),
            )

            if not self.call_ended:
                workflow.logger.info("Max duration reached, ending call")
                self.max_duration_reached = True
                self.call_ended = True

            # Step 6: Cleanup and finalize
            await self._cleanup_call()

            workflow.logger.info(
                f"Call completed: {self.call_id} with {len(self.transcript_segments)} segments"
            )

            return self._build_result()

        except Exception as e:
            workflow.logger.error(f"Call workflow failed: {str(e)}")
            self.status = CallStatus.FAILED
            await self._cleanup_call()
            raise

    async def _cleanup_call(self) -> None:
        """Cleanup call resources and finalize state."""
        workflow.logger.info("Cleaning up call resources")

        self.ended_at = workflow.now()

        # Terminate Twilio call if still active
        if self.call_sid:
            await workflow.execute_activity(
                "terminate_twilio_call",
                args=[self.call_sid],
                start_to_close_timeout=timedelta(seconds=10),
            )

        # Persist final transcripts
        if self.transcript_segments:
            await workflow.execute_activity(
                "save_transcript_batch",
                args=[str(self.call_id), [t.model_dump() for t in self.transcript_segments]],
                start_to_close_timeout=timedelta(seconds=30),
            )

        # Update call record in database
        duration_seconds = None
        if self.started_at and self.ended_at:
            duration_seconds = int((self.ended_at - self.started_at).total_seconds())

        await workflow.execute_activity(
            "update_call_record",
            args=[
                str(self.call_id),
                {
                    "status": self.status.value,
                    "ended_at": self.ended_at,  # Pass datetime object directly, not ISO string
                    "duration_seconds": duration_seconds,
                    "call_sid": self.call_sid,
                },
            ],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Cleanup Redis session record
        final_status = "completed" if self.status == CallStatus.COMPLETED else "failed"
        await workflow.execute_activity(
            "cleanup_session_record",
            args=[self.workflow_id, final_status, 300],  # 5 min TTL
            start_to_close_timeout=timedelta(seconds=10),
        )

    def _build_result(self) -> CallWorkflowResult:
        """Build workflow result object."""
        duration_seconds = None
        if self.started_at and self.ended_at:
            duration_seconds = int((self.ended_at - self.started_at).total_seconds())

        return CallWorkflowResult(
            call_id=self.call_id or uuid4(),
            workflow_id=self.workflow_id,
            run_id=self.run_id,
            status=self.status,
            phone_number=self.phone_number,
            started_at=self.started_at or workflow.now(),
            ended_at=self.ended_at,
            duration_seconds=duration_seconds,
            call_sid=self.call_sid,
            total_transcript_segments=len(self.transcript_segments),
            metadata={},
        )

    # === Coarse-Grained Signals (not per-frame) ===

    @workflow.signal
    async def streaming_started(self, data: dict) -> None:
        """Signal: Media streaming started (once per call)."""
        self.stream_sid = data.get("stream_sid")
        self.streaming_active = True
        workflow.logger.info(f"Streaming started: {self.stream_sid}")

    @workflow.signal
    async def streaming_ended(self, data: dict) -> None:
        """Signal: Media streaming ended (once per call)."""
        # Idempotent - check if already ended to avoid duplicate processing
        if self.call_ended:
            workflow.logger.debug(
                f"streaming_ended signal received but call already ended "
                f"(duplicate signal ignored for stream_sid: {data.get('stream_sid')})"
            )
            return

        self.streaming_active = False
        self.call_ended = True
        workflow.logger.info(f"Streaming ended: {self.stream_sid}")

    @workflow.signal
    async def transcripts_available(self, transcripts: list[dict]) -> None:
        """
        Signal: Batch of transcripts available (periodic, not per-frame).
        This dramatically reduces Temporal load compared to per-audio-chunk processing.
        """
        for t in transcripts:
            segment = TranscriptSegment(**t)
            self.transcript_segments.append(segment)

        workflow.logger.info(f"Received {len(transcripts)} transcript segments")

    @workflow.signal
    async def call_status_changed(self, status: str) -> None:
        """Signal: Call status changed (Twilio webhook)."""
        workflow.logger.info(f"📞 Call status changed to: {status} (current status: {self.status}, call_ended: {self.call_ended})")

        # Map Twilio statuses to CallStatus enum
        status_mapping = {
            "initiated": CallStatus.INITIATED,
            "ringing": CallStatus.RINGING,
            "answered": CallStatus.IN_PROGRESS,  # Handle "answered" for outbound calls
            "in-progress": CallStatus.IN_PROGRESS,
            "completed": CallStatus.COMPLETED,
            "busy": CallStatus.BUSY,
            "no-answer": CallStatus.NO_ANSWER,
            "failed": CallStatus.FAILED,
            "canceled": CallStatus.CANCELED,
        }

        mapped_status = status_mapping.get(status)
        if mapped_status:
            old_status = self.status
            self.status = mapped_status
            workflow.logger.info(f"✅ Status mapped: {status} -> {mapped_status} (changed from {old_status} to {self.status})")
        else:
            workflow.logger.warning(f"Unknown Twilio status: {status}")

        # Handle call termination states (idempotent)
        if status in ["completed", "busy", "no-answer", "failed", "canceled"]:
            if not self.call_ended:
                workflow.logger.info(f"🔴 Setting call_ended=True due to terminal status: {status}")
                self.call_ended = True
            else:
                workflow.logger.debug(f"Terminal status '{status}' received but call already ended (duplicate ignored)")

    @workflow.signal
    async def set_call_sid(self, call_sid: str) -> None:
        """Signal: Set Twilio call SID."""
        self.call_sid = call_sid
        workflow.logger.info(f"Call SID set: {call_sid}")

    # === Queries ===

    @workflow.query
    def get_call_status(self) -> str:
        """Query: Get current call status."""
        return self.status.value

    @workflow.query
    def get_call_config(self) -> dict[str, Any]:
        """Query: Get call configuration for audio_bridge."""
        return {
            "call_id": str(self.call_id) if self.call_id else None,
            "greeting": self.greeting,
            "system_prompt": self.system_prompt,
        }

    @workflow.query
    def get_transcript_count(self) -> int:
        """Query: Get current transcript segment count."""
        return len(self.transcript_segments)