"""
Audio bridge that streams Twilio audio to Gemini Live API and routes Gemini
responses back to Twilio.

BASED ON: Google's official Get_started_LiveAPI.py example
https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py
"""

import asyncio
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
# Use the correct model for Live API with audio
# Note: gemini-2.0-flash-live-001 is from cookbook, but docs show gemini-2.5-flash-native-audio-preview
# Let's use 2.0 as it's confirmed working in Google's cookbook
MODEL = "models/gemini-2.5-flash-native-audio-preview-09-2025"


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

        # Monitoring counters for backpressure detection
        self.total_frames = 0
        self.dropped_frames = 0

    async def start(self, greeting: str = "", system_prompt: Optional[str] = None):
        """Connect to Gemini Live API and start processing loops."""
        logger.info("Starting audio bridge session %s", self.session_id)

        self._greeting = greeting
        self._system_prompt = system_prompt

        # Start the session runner task (uses async with properly)
        self.session_task = asyncio.create_task(self._run_session())

        # Wait a moment for session to initialize
        await asyncio.sleep(0.5)

    async def _run_session(self):
        """Run the Gemini Live API session with proper async context management."""
        # Build system instruction
        system_text = self._system_prompt or "You are a helpful voice assistant. Be concise and natural."

        # Use simple dict config like Google's example
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
            }
        }

        logger.info("Connecting to Gemini Live API...")

        try:
            # CRITICAL: Use async with properly (like Google's example)
            async with self.client.aio.live.connect(model=MODEL, config=config) as session:
                self.session = session
                logger.info("Gemini Live API session connected successfully")

                # Start processing tasks within the session context
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

        except Exception as exc:
            logger.error(f"Session error: {exc}")
            import traceback
            traceback.print_exc()

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

    async def send_audio_from_twilio(self, audio_data: str):
        """Receive audio from Twilio and queue it for sending to Gemini."""
        if not self.active:
            return

        try:
            self.total_frames += 1

            # Convert Twilio audio to Gemini format
            pcm_audio = twilio_to_gemini(audio_data)

            # Queue it for sending (non-blocking to prevent backpressure)
            # If queue is full, just drop the frame - real-time audio can tolerate some loss
            try:
                # Use types.Blob format as per official docs
                audio_blob = types.Blob(data=pcm_audio, mime_type="audio/pcm;rate=16000")
                self.out_queue.put_nowait(audio_blob)
            except asyncio.QueueFull:
                self.dropped_frames += 1
                queue_depth = self.out_queue.qsize()
                drop_rate = (self.dropped_frames / self.total_frames) * 100

                # Log warning with backpressure metrics
                logger.warning(
                    "Dropped audio frame due to queue full. "
                    "Queue: %d/%d, Dropped: %d/%d (%.1f%%)",
                    queue_depth,
                    self.out_queue.maxsize,
                    self.dropped_frames,
                    self.total_frames,
                    drop_rate
                )
        except Exception as exc:
            logger.error("Error queuing audio from Twilio: %s", exc)

    async def receive_audio_for_twilio(self) -> Optional[str]:
        """Get audio from Gemini to send to Twilio."""
        try:
            return await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.01)
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
            (self.dropped_frames / self.total_frames * 100)
            if self.total_frames > 0
            else 0.0
        )
        return {
            "queue_depth": self.out_queue.qsize(),
            "queue_capacity": self.out_queue.maxsize,
            "queue_utilization": (
                self.out_queue.qsize() / self.out_queue.maxsize * 100
            ),
            "total_frames": self.total_frames,
            "dropped_frames": self.dropped_frames,
            "drop_rate_percent": drop_rate,
        }

    async def _send_realtime(self):
        """
        Background task that reads from out_queue and sends to Gemini.
        Uses send_realtime_input as per official docs.
        """
        logger.info("Starting send_realtime task")
        chunk_count = 0
        try:
            while self.active:
                audio_blob = await self.out_queue.get()

                # Debug logging
                logger.info(f"About to send chunk {chunk_count}: type={type(audio_blob)}, data_len={len(audio_blob.data) if hasattr(audio_blob, 'data') else 'N/A'}")

                # Use send_realtime_input with Blob as per official docs
                await self.session.send_realtime_input(audio=audio_blob)
                chunk_count += 1
                logger.info(f"Successfully sent chunk {chunk_count}")

                if chunk_count % 50 == 0:
                    logger.debug(f"Sent {chunk_count} audio chunks to Gemini")

        except asyncio.CancelledError:
            logger.info(f"send_realtime task cancelled after {chunk_count} chunks")
        except Exception as exc:
            logger.error(f"Error in send_realtime after {chunk_count} chunks: %s", exc)
            logger.error(f"Last message type: {type(audio_blob)}")
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
        logger.info("Starting receive_audio task")
        chunk_count = 0
        turn_count = 0
        try:
            while self.active:
                # CRITICAL: Use session.receive() to get a turn iterator
                logger.info(f"Waiting for turn {turn_count}...")
                turn = self.session.receive()
                turn_count += 1
                logger.info(f"Got turn {turn_count}, iterating responses...")

                async for response in turn:
                    if not self.active:
                        break

                    logger.debug(f"Received response: type={type(response)}, has_data={hasattr(response, 'data')}, has_text={hasattr(response, 'text')}, has_server_content={hasattr(response, 'server_content')}")

                    # Handle audio data (like Google's example)
                    if data := response.data:
                        # Convert Gemini audio to Twilio format
                        try:
                            twilio_audio = gemini_to_twilio(data)
                            self.audio_in_queue.put_nowait(twilio_audio)
                            chunk_count += 1

                            if chunk_count % 50 == 0:
                                logger.debug(f"Received {chunk_count} audio chunks from Gemini")
                        except Exception as exc:
                            logger.error("Failed to convert Gemini audio: %s", exc)
                        continue

                    # Handle text responses (transcriptions)
                    if text := response.text:
                        logger.info(f"Gemini text: {text}")
                        self.transcript_buffer.append(
                            TranscriptSegment(
                                speaker=Speaker.AI,
                                text=text,
                                timestamp=datetime.utcnow(),
                                confidence=1.0,
                            )
                        )

                    # CRITICAL: Handle interruptions (from Google's example)
                    # "If you interrupt the model, it sends a turn_complete.
                    # For interruptions to work, we need to stop playback.
                    # So empty out the audio queue because it may have loaded
                    # much more audio than has played yet."
                    if hasattr(response, 'server_content'):
                        server_content = response.server_content
                        logger.info(f"Got server_content: {server_content}")
                        if server_content and getattr(server_content, 'turn_complete', False):
                            logger.info("Turn complete - clearing audio queue")
                            # Clear the audio queue on turn complete
                            while not self.audio_in_queue.empty():
                                try:
                                    self.audio_in_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break

                logger.info(f"Turn {turn_count} completed")

        except asyncio.CancelledError:
            logger.info(f"receive_audio task cancelled after {chunk_count} chunks and {turn_count} turns")
        except Exception as exc:
            logger.error(f"Error in receive_audio after {chunk_count} chunks and {turn_count} turns: %s", exc)
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


class AudioBridgeManager:
    """Tracks active bridge sessions and supports pre-warming."""

    def __init__(self):
        self.sessions: Dict[str, AudioBridgeSession] = {}
        self.prewarmed_sessions: Dict[str, AudioBridgeSession] = {}

    async def create_session(
        self,
        session_id: str,
        call_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
    ) -> AudioBridgeSession:
        session = AudioBridgeSession(session_id, call_id)
        await session.start(greeting, system_prompt)
        self.sessions[session_id] = session
        return session

    async def prewarm_session(
        self,
        workflow_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
    ) -> None:
        try:
            session = AudioBridgeSession(f"prewarm-{workflow_id}", workflow_id)
            await session.start(greeting, system_prompt)
            self.prewarmed_sessions[workflow_id] = session

            asyncio.create_task(self._cleanup_prewarmed_session(workflow_id, 60))
        except Exception as exc:
            logger.warning("Failed to prewarm session %s: %s", workflow_id, exc)

    async def get_or_create_session(
        self,
        session_id: str,
        workflow_id: str,
        call_id: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
    ) -> AudioBridgeSession:
        if workflow_id in self.prewarmed_sessions:
            session = self.prewarmed_sessions.pop(workflow_id)
            session.session_id = session_id
            session.call_id = call_id
            self.sessions[session_id] = session
            return session

        return await self.create_session(session_id, call_id, greeting, system_prompt)

    async def _cleanup_prewarmed_session(self, workflow_id: str, timeout: int):
        await asyncio.sleep(timeout)
        session = self.prewarmed_sessions.pop(workflow_id, None)
        if session:
            await session.stop()

    async def get_session(self, session_id: str) -> Optional[AudioBridgeSession]:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            await session.stop()

    async def close_all_sessions(self):
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)


audio_bridge_manager = AudioBridgeManager()
