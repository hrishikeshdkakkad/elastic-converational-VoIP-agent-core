"""Call-related data models and schemas."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CallStatus(str, Enum):
    """Call status enumeration."""

    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    CANCELED = "canceled"


class CallDirection(str, Enum):
    """Call direction enumeration."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class Speaker(str, Enum):
    """Speaker identification."""

    USER = "user"
    AI = "ai"
    SYSTEM = "system"


class CallWorkflowInput(BaseModel):
    """Input data for starting a call workflow."""

    phone_number: str = Field(..., description="Phone number to call (E.164 format)")
    greeting: str = Field(
        default="Hello! How can I help you today?",
        description="Initial greeting message",
    )
    system_prompt: str | None = Field(
        default=None, description="Custom system prompt for AI behavior"
    )
    max_duration_seconds: int = Field(
        default=1800, description="Maximum call duration (30 minutes default)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata for the call"
    )


class CallWorkflowResult(BaseModel):
    """Result data from a completed call workflow."""

    call_id: UUID
    workflow_id: str
    run_id: str
    status: CallStatus
    phone_number: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    call_sid: str | None
    total_transcript_segments: int
    metadata: dict[str, Any]


class TranscriptSegment(BaseModel):
    """A single segment of conversation transcript."""

    speaker: Speaker
    text: str
    timestamp: datetime
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioChunk(BaseModel):
    """Audio data chunk for processing."""

    data: str | bytes  # IMPORTANT: str first to preserve base64 strings from being converted to bytes
    format: str = Field(default="mulaw", description="Audio format (mulaw, pcm16, etc.)")
    sample_rate: int = Field(default=8000, description="Sample rate in Hz")
    timestamp: datetime


class CallEvent(BaseModel):
    """Call lifecycle event."""

    event_type: str
    event_data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class TwilioMediaStreamEvent(BaseModel):
    """Twilio Media Stream WebSocket event."""

    event: str  # "start", "media", "stop", "mark"
    stream_sid: str | None = None
    media: dict[str, Any] | None = None
    start: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    mark: dict[str, Any] | None = None


class GeminiAudioResponse(BaseModel):
    """Response from Gemini Live API."""

    audio_data: str | bytes | None = None  # IMPORTANT: str first to preserve base64 strings
    text: str | None = None
    is_final: bool = False
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
