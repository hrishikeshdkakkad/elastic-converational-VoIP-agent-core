"""
Audio bridge that streams Twilio audio to Gemini Live API and routes Gemini
responses back to Twilio.

BASED ON: Google's official Get_started_LiveAPI.py example
https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py
"""

import asyncio
import concurrent.futures
import logging
from collections import deque
from datetime import datetime
from typing import Dict, Optional

from google import genai
from google.genai import types

from src.voice_ai_system.config import settings
from src.voice_ai_system.models.call import Speaker, TranscriptSegment
from src.voice_ai_system.utils.audio import gemini_to_twilio, twilio_to_gemini

logger = logging.getLogger(__name__)

# Audio format constants
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
# Use a known-good Gemini Live Audio model
# This model is confirmed to stream audio responses in current Live API rollout.
MODEL = "models/gemini-2.0-flash-live-001"


class AudioBridgeSession:
    """Maintains a single Twilio ↔ Gemini audio bridge."""

    def __init__(self, session_id: str, call_id: str):
        self.session_id = session_id
        self.call_id = call_id
        self.session = None
        self.session_task = None
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options={"api_version": "v1alpha"}
        )

        # Queues for audio streaming
        self.audio_in_queue: asyncio.Queue = asyncio.Queue()  # From Gemini
        self.out_queue: asyncio.Queue = asyncio.Queue(maxsize=100)  # To Gemini (increased from 5)
        self.transcript_buffer: deque[TranscriptSegment] = deque(maxlen=50)

        self.active = True
        self.tasks: list[asyncio.Task] = []
        self._greeting = ""
        self._system_prompt = None

        # ThreadPoolExecutor for CPU-bound audio processing
        # Use 2 workers: one for encoding (Twilio->Gemini), one for decoding (Gemini->Twilio)
        self._audio_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix=f"audio-{session_id[:8]}"
        )

        # VAD configuration (with defaults optimized for phone calls)
        # IMPORTANT: Pre-warmed sessions use these defaults and VAD can't be changed mid-session
        # So these defaults should match what twilio.py expects
        self._vad_config = {
            "disabled": False,
            "start_sensitivity": "HIGH",  # More sensitive to detect speech start quickly
            "end_sensitivity": "LOW",     # Less sensitive to avoid cutting off mid-sentence
            "prefix_padding_ms": 200,     # Buffer before speech detection
            "silence_duration_ms": 500    # 500ms silence = end of speech
        }

        # Monitoring counters for backpressure detection
        self.total_frames_sent = 0  # Frames sent to Gemini (from Twilio)
        self.total_frames_received = 0  # Frames received from Gemini
        self.dropped_frames = 0

        # Timing metrics
        self.first_audio_frame_at: Optional[datetime] = None
        self.session_started_at: Optional[datetime] = None
        self._initial_prompt_sent: bool = False

        # Queue depth tracking
        self.max_queue_depth = 0
        self.queue_depth_sum = 0
        self.queue_depth_samples = 0

        # Turn and interaction tracking
        self.ai_turn_count = 0
        self.user_turn_count = 0
        self.interruption_count = 0

        # Track user and AI transcripts separately for turn counting
        self._last_speaker: Optional[Speaker] = None

        # Heartbeat tracking for stuck detection
        self._last_receive_activity: Optional[datetime] = None
        self._current_turn: int = 0

    async def start(self, greeting: str = "", system_prompt: Optional[str] = None, vad_config: Optional[dict] = None):
        """Connect to Gemini Live API and start processing loops.

        Args:
            greeting: Initial greeting message
            system_prompt: System instructions for the AI
            vad_config: Optional VAD configuration override with keys:
                - disabled: bool (default False)
                - start_sensitivity: "HIGH" or "LOW" (default "LOW")
                - end_sensitivity: "HIGH" or "LOW" (default "LOW")
                - prefix_padding_ms: int (default 100)
                - silence_duration_ms: int (default 700)
        """
        logger.info("Starting audio bridge session %s", self.session_id)

        self.session_started_at = datetime.utcnow()
        self._greeting = greeting
        self._system_prompt = system_prompt

        # Update VAD config if provided
        if vad_config:
            self._vad_config.update(vad_config)
            logger.debug(f"VAD config updated: {self._vad_config}")

        # Start the session runner task (uses async with properly)
        self.session_task = asyncio.create_task(self._run_session())

        # Wait a moment for session to initialize
        await asyncio.sleep(0.5)

    async def _run_session(self):
        """Run the Gemini Live API session with proper async context management."""
        # Build system instruction with language specification
        # Important: Explicitly specify English to avoid language detection issues on phone audio
        default_prompt = (
            "You are a helpful voice assistant on a phone call. "
            "Always respond in English. Be concise and natural. "
            "Keep responses brief since this is a phone conversation."
        )
        system_text = self._system_prompt or default_prompt
        logger.debug(f"System prompt: {system_text[:100]}...")

        # Configure VAD for optimal voice call experience
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": {
                "parts": [{"text": system_text}]
            },
            "generation_config": {
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {
                            "voice_name": "Charon"
                        }
                    }
                }
            },
            # Voice Activity Detection configuration
            "realtime_input_config": {
                "automatic_activity_detection": {
                    "disabled": self._vad_config.get("disabled", False),
                    "start_of_speech_sensitivity": types.StartSensitivity.START_SENSITIVITY_LOW if self._vad_config.get('start_sensitivity', 'LOW').upper() == 'LOW' else types.StartSensitivity.START_SENSITIVITY_HIGH,
                    "end_of_speech_sensitivity": types.EndSensitivity.END_SENSITIVITY_LOW if self._vad_config.get('end_sensitivity', 'LOW').upper() == 'LOW' else types.EndSensitivity.END_SENSITIVITY_HIGH,
                    "prefix_padding_ms": self._vad_config.get("prefix_padding_ms", 100),
                    "silence_duration_ms": self._vad_config.get("silence_duration_ms", 700)
                },
                "activity_handling": types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,  # Allow barge-in (interruption)
                "turn_coverage": types.TurnCoverage.TURN_INCLUDES_ALL_INPUT  # Include all input in user's turn
            },
            # Enable transcription for debugging and logging
            "input_audio_transcription": {},
            "output_audio_transcription": {}
        }

        logger.info("Connecting to Gemini Live API...")

        try:
            # CRITICAL: Use async with properly (like Google's example)
            async with self.client.aio.live.connect(model=MODEL, config=config) as session:
                self.session = session
                logger.info("Gemini Live API connected (session=%s, VAD enabled)", self.session_id)

                # Proactively kick off the first assistant turn so we don't wait on VAD silence
                await self._send_initial_prompt()

                # Start processing tasks within the session context
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._ensure_first_audio_frame())
                    tg.create_task(self._heartbeat_monitor())

        except Exception as exc:
            logger.error(f"Session error: {exc}")
            import traceback
            traceback.print_exc()

    async def _send_initial_prompt(self, force: bool = False):
        """Send a greeting to force Gemini to speak even if no user audio is detected yet."""
        if not self.session:
            return

        if self._initial_prompt_sent and not force:
            return

        greeting_text = self._greeting.strip() if self._greeting else ""
        if not greeting_text:
            greeting_text = "Hello! How can I help you today?"

        try:
            await self.session.send(input=greeting_text, end_of_turn=True)
            self._initial_prompt_sent = True
            logger.info("Sent initial prompt to Gemini to kick off first turn")
        except Exception as exc:
            logger.warning("Failed to send initial prompt to Gemini: %s", exc)

    async def _ensure_first_audio_frame(self):
        """
        Watchdog: if Gemini hasn't produced audio soon after connect, nudge with the greeting again.
        """
        for delay_secs in (3, 8):
            await asyncio.sleep(delay_secs)
            if not self.active or self.first_audio_frame_at is not None:
                return

            logger.info(
                "No Gemini audio after %ss; sending greeting again to unblock first turn",
                delay_secs,
            )
            await self._send_initial_prompt(force=True)

        if self.active and self.first_audio_frame_at is None:
            logger.warning("Still no audio from Gemini after proactive greeting attempts")

    async def stop(self):
        """Stop the audio bridge session."""
        logger.info("Stopping audio bridge session %s", self.session_id)
        self.active = False

        # Cancel the session task (this will trigger async with cleanup automatically)
        if self.session_task and not self.session_task.done():
            self.session_task.cancel()
            try:
                await self.session_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Error stopping session task: %s", exc)

        # Shutdown thread pool executor
        # wait=False to avoid blocking, but give it a chance to finish current tasks
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._audio_executor.shutdown, False)
            logger.debug("Audio executor shutdown for session %s", self.session_id)
        except Exception as exc:
            logger.warning("Error shutting down audio executor: %s", exc)

    async def send_audio_from_twilio(self, audio_data: str):
        """Receive audio from Twilio and queue it for sending to Gemini.

        Uses ThreadPoolExecutor to offload CPU-heavy decoding/resampling off the event loop.
        Implements early backpressure: drops frames if queue is >80% full before processing.
        """
        if not self.active:
            return

        try:
            self.total_frames_sent += 1

            # Log audio level periodically to diagnose VAD issues
            if self.total_frames_sent == 1 or self.total_frames_sent % 200 == 0:
                import base64
                import numpy as np
                try:
                    raw_mulaw = base64.b64decode(audio_data)
                    # Check μ-law data characteristics
                    mulaw_arr = np.frombuffer(raw_mulaw, dtype=np.uint8)
                    mulaw_min, mulaw_max, mulaw_mean = mulaw_arr.min(), mulaw_arr.max(), mulaw_arr.mean()
                    logger.info(
                        f"Audio diagnostics (frame {self.total_frames_sent}): "
                        f"mulaw bytes={len(raw_mulaw)}, min={mulaw_min}, max={mulaw_max}, mean={mulaw_mean:.1f}"
                    )
                except Exception as e:
                    logger.warning(f"Audio diagnostics error: {e}")

            # Early backpressure: check queue depth BEFORE doing expensive processing
            queue_depth = self.out_queue.qsize()
            queue_utilization = queue_depth / self.out_queue.maxsize

            # Drop frame early if queue is >80% full to prevent wasting CPU on frames we'll drop anyway
            if queue_utilization > 0.8:
                self.dropped_frames += 1
                drop_rate = (self.dropped_frames / self.total_frames_sent) * 100

                if self.dropped_frames % 10 == 1:  # Log every 10th drop to reduce log spam
                    logger.warning(
                        "Dropped frame (early backpressure): queue %d/%d (%.0f%% full), "
                        "drop rate %.1f%%",
                        queue_depth, self.out_queue.maxsize,
                        queue_utilization * 100, drop_rate
                    )
                return

            # Offload CPU-heavy audio conversion to thread pool
            loop = asyncio.get_event_loop()
            pcm_audio = await loop.run_in_executor(
                self._audio_executor,
                twilio_to_gemini,
                audio_data
            )

            # Log PCM audio levels periodically to verify conversion
            if self.total_frames_sent == 1 or self.total_frames_sent % 200 == 0:
                import numpy as np
                try:
                    pcm_arr = np.frombuffer(pcm_audio, dtype=np.int16)
                    pcm_min, pcm_max = pcm_arr.min(), pcm_arr.max()
                    pcm_rms = np.sqrt(np.mean(pcm_arr.astype(np.float32) ** 2))
                    # Calculate dB relative to full scale (dBFS)
                    dbfs = 20 * np.log10(pcm_rms / 32768.0) if pcm_rms > 0 else -100
                    logger.info(
                        f"PCM diagnostics (frame {self.total_frames_sent}): "
                        f"samples={len(pcm_arr)}, min={pcm_min}, max={pcm_max}, "
                        f"RMS={pcm_rms:.1f}, dBFS={dbfs:.1f}"
                    )
                except Exception as e:
                    logger.warning(f"PCM diagnostics error: {e}")

            if self.total_frames_sent % 50 == 0:
                logger.info(
                    "Twilio inbound frames=%d queue=%d/%d (session=%s)",
                    self.total_frames_sent,
                    self.out_queue.qsize(),
                    self.out_queue.maxsize,
                    self.session_id,
                )

            # Queue it for sending (non-blocking to prevent further backpressure)
            # If queue is full at this point, drop the frame - real-time audio tolerates some loss
            try:
                # Use types.Blob format as per official docs
                audio_blob = types.Blob(data=pcm_audio, mime_type="audio/pcm;rate=16000")
                self.out_queue.put_nowait(audio_blob)

                # Track queue depth for metrics
                queue_depth = self.out_queue.qsize()
                self.max_queue_depth = max(self.max_queue_depth, queue_depth)
                self.queue_depth_sum += queue_depth
                self.queue_depth_samples += 1

            except asyncio.QueueFull:
                self.dropped_frames += 1
                drop_rate = (self.dropped_frames / self.total_frames_sent) * 100

                # Log warning with backpressure metrics
                logger.warning(
                    "Dropped frame (queue full after processing): queue %d/%d, "
                    "dropped %d/%d (%.1f%%)",
                    self.out_queue.maxsize,
                    self.out_queue.maxsize,
                    self.dropped_frames,
                    self.total_frames_sent,
                    drop_rate
                )
        except Exception as exc:
            logger.error("Error queuing audio from Twilio: %s", exc)

    async def receive_audio_for_twilio(self, timeout: float = 0.01) -> Optional[str]:
        """Get audio from Gemini to send to Twilio."""
        try:
            return await asyncio.wait_for(self.audio_in_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def get_transcript_buffer(self) -> list[TranscriptSegment]:
        """Get and clear the transcript buffer."""
        items = list(self.transcript_buffer)
        self.transcript_buffer.clear()
        return items

    def get_metrics(self) -> dict:
        """Get current audio bridge metrics for monitoring."""
        drop_rate = (
            (self.dropped_frames / self.total_frames_sent * 100)
            if self.total_frames_sent > 0
            else 0.0
        )
        avg_queue_depth = (
            (self.queue_depth_sum / self.queue_depth_samples)
            if self.queue_depth_samples > 0
            else 0.0
        )
        return {
            "queue_depth": self.out_queue.qsize(),
            "queue_capacity": self.out_queue.maxsize,
            "queue_utilization": (
                self.out_queue.qsize() / self.out_queue.maxsize * 100
            ),
            "total_audio_frames_sent": self.total_frames_sent,
            "total_audio_frames_received": self.total_frames_received,
            "total_audio_frames_dropped": self.dropped_frames,
            "audio_drop_rate_percent": drop_rate,
            "max_audio_queue_depth": self.max_queue_depth,
            "avg_audio_queue_depth": avg_queue_depth,
            "ai_turn_count": self.ai_turn_count,
            "user_turn_count": self.user_turn_count,
            "interruption_count": self.interruption_count,
            "first_audio_frame_at": self.first_audio_frame_at.isoformat() if self.first_audio_frame_at else None,
            "session_started_at": self.session_started_at.isoformat() if self.session_started_at else None,
        }

    async def _send_realtime(self):
        """
        Background task that reads from out_queue and sends to Gemini.
        Uses send_realtime_input as per official docs.
        """
        logger.info("Starting send_realtime task for session %s", self.session_id)
        chunk_count = 0
        total_bytes_sent = 0
        last_log_time = datetime.utcnow()
        try:
            while self.active:
                audio_blob = await self.out_queue.get()
                await self.session.send_realtime_input(audio=audio_blob)
                chunk_count += 1
                total_bytes_sent += len(audio_blob.data) if hasattr(audio_blob, 'data') else 0

                # Log every 100 chunks or every 5 seconds
                now = datetime.utcnow()
                if chunk_count % 100 == 0 or (now - last_log_time).total_seconds() > 5:
                    logger.info(
                        f"Audio send progress: {chunk_count} chunks, {total_bytes_sent} bytes sent to Gemini "
                        f"(session={self.session_id})"
                    )
                    last_log_time = now

        except asyncio.CancelledError:
            logger.info(f"send_realtime task completed: {chunk_count} chunks, {total_bytes_sent} bytes sent")
        except Exception as exc:
            logger.error(f"Error in send_realtime after {chunk_count} chunks: %s", exc)
            import traceback
            traceback.print_exc()

    async def _listen_audio(self):
        """
        Dummy method for compatibility - actual audio comes from Twilio via send_audio_from_twilio.
        In Google's example, this captures from microphone. For us, Twilio provides the audio.
        """
        # We don't need to do anything here since Twilio sends us audio
        # via send_audio_from_twilio which queues to out_queue
        while self.active:
            await asyncio.sleep(1.0)

    async def _receive_audio(self):
        """
        Background task that reads from the websocket and writes PCM chunks to audio_in_queue.
        Based on Google's receive_audio() method.
        """
        logger.info("Starting receive_audio task for session %s", self.session_id)
        chunk_count = 0
        turn_count = 0
        last_activity_time = datetime.utcnow()
        try:
            while self.active:
                # CRITICAL: During pre-warming, only process Turn 1 (the greeting).
                # Don't start Turn 2 until the session is connected to a real call.
                # This prevents the VAD from getting stuck waiting on silence.
                is_prewarming = self.session_id.startswith("prewarm-")
                if is_prewarming and turn_count >= 1:
                    logger.info(f"Pre-warming complete after turn {turn_count}, waiting for call to connect...")
                    # Wait until session_id changes (indicating session was claimed by a real call)
                    while self.active and self.session_id.startswith("prewarm-"):
                        await asyncio.sleep(0.1)
                    if not self.active:
                        break
                    logger.info(f"Session claimed by real call, resuming with session_id={self.session_id}")

                logger.info(f"Waiting for turn {turn_count + 1} from Gemini (session={self.session_id})")
                turn = self.session.receive()
                turn_count += 1
                self._current_turn = turn_count
                last_activity_time = datetime.utcnow()
                self._last_receive_activity = last_activity_time
                logger.info(f"Started receiving turn {turn_count} (session={self.session_id})")

                async for response in turn:
                    last_activity_time = datetime.utcnow()
                    self._last_receive_activity = last_activity_time
                    if not self.active:
                        break

                    # DEBUG: Log ALL response attributes to understand what Gemini sends
                    response_attrs = []
                    if hasattr(response, 'data') and response.data:
                        response_attrs.append(f"data({len(response.data)})")
                    if hasattr(response, 'text') and response.text:
                        response_attrs.append(f"text({len(response.text)})")
                    if hasattr(response, 'server_content') and response.server_content:
                        sc = response.server_content
                        sc_info = []
                        if getattr(sc, 'turn_complete', False):
                            sc_info.append("turn_complete")
                        if getattr(sc, 'interrupted', False):
                            sc_info.append("interrupted")
                        if getattr(sc, 'generation_complete', False):
                            sc_info.append("generation_complete")
                        if getattr(sc, 'grounding_metadata', None):
                            sc_info.append("grounding_metadata")
                        if sc_info:
                            response_attrs.append(f"server_content({','.join(sc_info)})")
                    if hasattr(response, 'input_transcription') and response.input_transcription:
                        it = response.input_transcription
                        it_text = getattr(it, 'text', '')
                        response_attrs.append(f"input_transcription({len(it_text)} chars)")
                    if hasattr(response, 'output_transcription') and response.output_transcription:
                        ot = response.output_transcription
                        ot_text = getattr(ot, 'text', '')
                        response_attrs.append(f"output_transcription({len(ot_text)} chars)")
                    if hasattr(response, 'tool_call') and response.tool_call:
                        response_attrs.append("tool_call")
                    if hasattr(response, 'tool_call_cancellation') and response.tool_call_cancellation:
                        response_attrs.append("tool_call_cancellation")
                    if hasattr(response, 'setup_complete') and response.setup_complete:
                        response_attrs.append("setup_complete")
                    if hasattr(response, 'go_away') and response.go_away:
                        response_attrs.append("go_away")
                    if hasattr(response, 'session_resumption_update') and response.session_resumption_update:
                        response_attrs.append("session_resumption_update")
                    # VAD activity events
                    if hasattr(response, 'realtime_input') and response.realtime_input:
                        ri = response.realtime_input
                        ri_info = []
                        if hasattr(ri, 'activity_start') and ri.activity_start:
                            ri_info.append("activity_start")
                        if hasattr(ri, 'activity_end') and ri.activity_end:
                            ri_info.append("activity_end")
                        if ri_info:
                            response_attrs.append(f"realtime_input({','.join(ri_info)})")
                            # Also log as INFO since VAD events are important
                            logger.info(f"VAD: {','.join(ri_info)} detected on turn {turn_count}")

                    if response_attrs:
                        logger.debug(f"Turn {turn_count} event: {', '.join(response_attrs)}")

                    # Handle audio data (like Google's example)
                    if data := response.data:
                        logger.info(
                            "Gemini emitted audio chunk len=%d (session=%s)",
                            len(data),
                            self.session_id,
                        )
                        # Convert Gemini audio to Twilio format
                        try:
                            # Track first audio frame timestamp
                            if self.first_audio_frame_at is None:
                                self.first_audio_frame_at = datetime.utcnow()
                                logger.info(f"First audio frame received at {self.first_audio_frame_at.isoformat()}")

                            # Offload CPU-heavy audio conversion to thread pool
                            loop = asyncio.get_event_loop()
                            twilio_audio = await loop.run_in_executor(
                                self._audio_executor,
                                gemini_to_twilio,
                                data
                            )
                            self.audio_in_queue.put_nowait(twilio_audio)
                            chunk_count += 1
                            self.total_frames_received += 1

                            if chunk_count % 50 == 0:
                                logger.debug(f"Received {chunk_count} audio chunks from Gemini")
                        except Exception as exc:
                            logger.error("Failed to convert Gemini audio: %s", exc)
                        continue

                    # Handle text responses (model-generated text)
                    if text := response.text:
                        logger.info(f"Gemini text: {text}")

                        # Track turn count (new AI response = new AI turn if speaker changed)
                        if self._last_speaker != Speaker.AI:
                            self.ai_turn_count += 1
                            self._last_speaker = Speaker.AI

                        self.transcript_buffer.append(
                            TranscriptSegment(
                                speaker=Speaker.AI,
                                text=text,
                                timestamp=datetime.utcnow(),
                                confidence=1.0,
                            )
                        )

                    # Handle input transcriptions (user's speech-to-text)
                    if hasattr(response, 'input_transcription') and response.input_transcription:
                        transcription = response.input_transcription
                        if hasattr(transcription, 'text') and transcription.text:
                            logger.info(f"User transcription: {transcription.text}")

                            # Track turn count (new user input = new user turn if speaker changed)
                            if self._last_speaker != Speaker.USER:
                                self.user_turn_count += 1
                                self._last_speaker = Speaker.USER

                            self.transcript_buffer.append(
                                TranscriptSegment(
                                    speaker=Speaker.USER,
                                    text=transcription.text,
                                    timestamp=datetime.utcnow(),
                                    confidence=getattr(transcription, 'confidence', 0.95),
                                )
                            )

                    # Handle output transcriptions (AI's text-to-speech)
                    if hasattr(response, 'output_transcription') and response.output_transcription:
                        transcription = response.output_transcription
                        if hasattr(transcription, 'text') and transcription.text:
                            logger.info(f"AI output transcription: {transcription.text}")
                            # Store as AI speaker since it's what the AI is saying
                            self.transcript_buffer.append(
                                TranscriptSegment(
                                    speaker=Speaker.AI,
                                    text=transcription.text,
                                    timestamp=datetime.utcnow(),
                                    confidence=1.0,  # AI output is always confident
                                )
                            )

                    # Handle turn completion and interruptions
                    # IMPORTANT: Only clear audio queue on INTERRUPTION, not on normal turn completion
                    # - interrupted=True → User barged in, clear queued audio
                    # - turn_complete=True without interrupted → AI finished normally, don't clear
                    if hasattr(response, 'server_content'):
                        server_content = response.server_content
                        if server_content:
                            is_interrupted = getattr(server_content, 'interrupted', False)
                            is_turn_complete = getattr(server_content, 'turn_complete', False)

                            if is_interrupted:
                                # User interrupted the AI - clear audio queue to stop playback
                                logger.info(f"Turn {turn_count} INTERRUPTED - clearing audio queue")
                                self.interruption_count += 1
                                while not self.audio_in_queue.empty():
                                    try:
                                        self.audio_in_queue.get_nowait()
                                    except asyncio.QueueEmpty:
                                        break
                            elif is_turn_complete:
                                # AI finished speaking normally
                                is_prewarming = self.session_id.startswith("prewarm-")
                                if is_prewarming:
                                    queue_size = self.audio_in_queue.qsize()
                                    logger.info(f"Turn {turn_count} complete during pre-warming - preserving {queue_size} audio frames")
                                else:
                                    logger.info(f"Turn {turn_count} complete - ready for next user input")

                    # Handle go_away - Gemini is ending the session
                    if hasattr(response, 'go_away') and response.go_away:
                        logger.warning(f"Gemini sent go_away signal! Session may be ending. Details: {response.go_away}")

                # Log when turn iteration completes
                logger.info(f"Turn {turn_count} iteration completed, looping to wait for next turn (session={self.session_id})")
                logger.info(f"Audio stats: sent_to_gemini={self.total_frames_sent}, received_from_gemini={self.total_frames_received}, out_queue={self.out_queue.qsize()}")

        except asyncio.CancelledError:
            logger.info(f"receive_audio task completed: {turn_count} turns processed")
        except Exception as exc:
            logger.error(f"Error in receive_audio: %s", exc)
            import traceback
            traceback.print_exc()

    async def _play_audio(self):
        """
        Dummy method for compatibility - actual audio goes to Twilio via receive_audio_for_twilio.
        In Google's example, this plays to speakers. For us, Twilio handles playback.
        """
        # We don't need to do anything here since Twilio polls audio
        # via receive_audio_for_twilio which gets from audio_in_queue
        while self.active:
            await asyncio.sleep(1.0)

    async def _heartbeat_monitor(self):
        """
        Monitor for stuck receive loop - logs warning if no activity for extended period.
        This helps diagnose issues where Gemini stops responding.
        """
        HEARTBEAT_INTERVAL = 5  # Check every 5 seconds
        STUCK_THRESHOLD = 15  # Warn if no activity for 15 seconds (during a turn)

        logger.info(f"Starting heartbeat monitor for session {self.session_id}")

        try:
            while self.active:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                if not self.active:
                    break

                # Log current state every heartbeat
                queue_in_size = self.audio_in_queue.qsize()
                queue_out_size = self.out_queue.qsize()

                # Check if receive loop is stuck
                if self._last_receive_activity:
                    elapsed = (datetime.utcnow() - self._last_receive_activity).total_seconds()

                    # Only warn if we're mid-turn and stuck
                    if elapsed > STUCK_THRESHOLD and self._current_turn > 0:
                        logger.warning(
                            f"HEARTBEAT: No receive activity for {elapsed:.1f}s! "
                            f"Turn={self._current_turn}, session={self.session_id}, "
                            f"in_queue={queue_in_size}, out_queue={queue_out_size}, "
                            f"frames_sent={self.total_frames_sent}, frames_received={self.total_frames_received}"
                        )
                    else:
                        logger.info(
                            f"HEARTBEAT: Turn={self._current_turn}, last_activity={elapsed:.1f}s ago, "
                            f"in_queue={queue_in_size}, out_queue={queue_out_size}, "
                            f"sent={self.total_frames_sent}, received={self.total_frames_received}"
                        )
                else:
                    logger.info(
                        f"HEARTBEAT: Waiting for first turn, session={self.session_id}, "
                        f"in_queue={queue_in_size}, out_queue={queue_out_size}"
                    )

        except asyncio.CancelledError:
            logger.info(f"Heartbeat monitor stopped for session {self.session_id}")
        except Exception as exc:
            logger.error(f"Error in heartbeat monitor: {exc}")


class AudioBridgeManager:
    """Tracks active bridge sessions and supports pre-warming with proper cleanup."""

    # Shorter timeout for pre-warmed sessions (30 seconds instead of 60)
    # Most calls connect within 15 seconds; 30s is generous
    PREWARM_TIMEOUT_SECONDS = 30

    def __init__(self):
        self.sessions: Dict[str, AudioBridgeSession] = {}
        self.prewarmed_sessions: Dict[str, AudioBridgeSession] = {}
        # Track cleanup tasks so we can cancel them when session is claimed
        self._prewarm_cleanup_tasks: Dict[str, asyncio.Task] = {}

    async def create_session(
        self,
        session_id: str,
        call_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
        vad_config: Optional[dict] = None,
    ) -> AudioBridgeSession:
        session = AudioBridgeSession(session_id, call_id)
        await session.start(greeting, system_prompt, vad_config)
        self.sessions[session_id] = session
        return session

    async def prewarm_session(
        self,
        workflow_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
        vad_config: Optional[dict] = None,
    ) -> None:
        """Pre-warm a Gemini session for reduced latency when call connects.

        The session will be automatically cleaned up after PREWARM_TIMEOUT_SECONDS
        if not claimed by get_or_create_session().
        """
        try:
            # Check if we already have a pre-warmed session for this workflow
            if workflow_id in self.prewarmed_sessions:
                logger.warning(
                    f"Pre-warmed session already exists for workflow {workflow_id}, skipping"
                )
                return

            session = AudioBridgeSession(f"prewarm-{workflow_id}", workflow_id)
            await session.start(greeting, system_prompt, vad_config)
            self.prewarmed_sessions[workflow_id] = session

            # Create tracked cleanup task
            cleanup_task = asyncio.create_task(
                self._cleanup_prewarmed_session(workflow_id, self.PREWARM_TIMEOUT_SECONDS),
                name=f"prewarm-cleanup-{workflow_id}"
            )
            self._prewarm_cleanup_tasks[workflow_id] = cleanup_task

            # Auto-remove from tracking dict when task completes
            cleanup_task.add_done_callback(
                lambda t: self._prewarm_cleanup_tasks.pop(workflow_id, None)
            )

            logger.info(
                f"Pre-warmed session created for workflow {workflow_id}, "
                f"will auto-cleanup in {self.PREWARM_TIMEOUT_SECONDS}s if unused"
            )

        except Exception as exc:
            logger.warning("Failed to prewarm session %s: %s", workflow_id, exc)
            # Ensure cleanup on failure
            await self.cleanup_prewarm(workflow_id)

    async def get_or_create_session(
        self,
        session_id: str,
        workflow_id: str,
        call_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
        vad_config: Optional[dict] = None,
    ) -> AudioBridgeSession:
        """Get a pre-warmed session or create a new one."""
        if workflow_id in self.prewarmed_sessions:
            session = self.prewarmed_sessions.pop(workflow_id)

            # Cancel the cleanup task since session is being claimed
            cleanup_task = self._prewarm_cleanup_tasks.pop(workflow_id, None)
            if cleanup_task and not cleanup_task.done():
                cleanup_task.cancel()
                logger.debug(f"Cancelled cleanup task for claimed session {workflow_id}")

            # Log the state of the pre-warmed session
            queue_size = session.audio_in_queue.qsize()
            logger.info(
                f"Reusing pre-warmed session for workflow {workflow_id}: "
                f"audio_queue_size={queue_size}, frames_received={session.total_frames_received}"
            )

            session.session_id = session_id
            session.call_id = call_id
            self.sessions[session_id] = session
            return session

        return await self.create_session(session_id, call_id, greeting, system_prompt, vad_config)

    async def cleanup_prewarm(self, workflow_id: str) -> bool:
        """Explicitly cleanup a pre-warmed session (e.g., when workflow fails).

        Returns True if a session was cleaned up, False if none existed.
        """
        # Cancel cleanup task if exists
        cleanup_task = self._prewarm_cleanup_tasks.pop(workflow_id, None)
        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()

        # Stop and remove session
        session = self.prewarmed_sessions.pop(workflow_id, None)
        if session:
            logger.info(f"Explicitly cleaning up pre-warmed session for workflow {workflow_id}")
            await session.stop()
            return True
        return False

    async def _cleanup_prewarmed_session(self, workflow_id: str, timeout: int):
        """Auto-cleanup task for pre-warmed sessions that weren't claimed."""
        try:
            await asyncio.sleep(timeout)
            session = self.prewarmed_sessions.pop(workflow_id, None)
            if session:
                logger.info(
                    f"Auto-cleaning pre-warmed session for workflow {workflow_id} "
                    f"(unclaimed after {timeout}s)"
                )
                await session.stop()
        except asyncio.CancelledError:
            # Task was cancelled because session was claimed - this is expected
            pass
        except Exception as exc:
            logger.error(f"Error in prewarm cleanup for {workflow_id}: {exc}")

    async def get_session(self, session_id: str) -> Optional[AudioBridgeSession]:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            await session.stop()

    async def close_all_sessions(self):
        """Close all active and pre-warmed sessions."""
        # Close active sessions
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)

        # Cleanup all pre-warmed sessions
        for workflow_id in list(self.prewarmed_sessions.keys()):
            await self.cleanup_prewarm(workflow_id)

        # Cancel any remaining cleanup tasks
        for task in self._prewarm_cleanup_tasks.values():
            if not task.done():
                task.cancel()
        self._prewarm_cleanup_tasks.clear()


audio_bridge_manager = AudioBridgeManager()
