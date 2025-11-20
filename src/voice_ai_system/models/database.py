"""SQLAlchemy database models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

from src.voice_ai_system.models.call import CallStatus, Speaker

Base = declarative_base()


class Call(Base):
    __tablename__ = "calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(String, unique=True, nullable=False, index=True)
    run_id = Column(String)
    phone_number = Column(String, nullable=False)
    status = Column(Enum(CallStatus), nullable=False, default=CallStatus.INITIATED)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime)
    duration_seconds = Column(Integer)
    call_sid = Column(String, unique=True)
    meta_data = Column(JSON, default=dict)

    transcripts = relationship("Transcript", back_populates="call", cascade="all, delete-orphan")
    events = relationship("CallEvent", back_populates="call", cascade="all, delete-orphan")
    metrics = relationship("CallMetrics", back_populates="call", cascade="all, delete-orphan", uselist=False)


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(UUID(as_uuid=True), ForeignKey("calls.id"), nullable=False, index=True)
    speaker = Column(Enum(Speaker), nullable=False)
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    confidence = Column(Float)
    meta_data = Column(JSON, default=dict)

    call = relationship("Call", back_populates="transcripts")


class CallEvent(Base):
    __tablename__ = "call_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(UUID(as_uuid=True), ForeignKey("calls.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    event_data = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow)

    call = relationship("Call", back_populates="events")


class CallMetrics(Base):
    __tablename__ = "call_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    workflow_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    # Connection Timing Metrics (all timestamps and durations in ms)
    call_initiated_at = Column(DateTime)
    websocket_connected_at = Column(DateTime)
    call_answered_at = Column(DateTime)
    streaming_started_at = Column(DateTime)
    first_audio_frame_at = Column(DateTime)
    time_to_websocket_ms = Column(Integer)  # Time from call initiation to WS connection
    time_to_answer_ms = Column(Integer)  # Time from call initiation to answered
    time_to_streaming_ms = Column(Integer)  # Time from answered to streaming start
    time_to_first_audio_ms = Column(Integer)  # Time from streaming to first audio

    # Audio Performance Metrics
    total_audio_frames_sent = Column(Integer, default=0)
    total_audio_frames_received = Column(Integer, default=0)
    total_audio_frames_dropped = Column(Integer, default=0)
    audio_drop_rate_percent = Column(Float, default=0.0)
    max_audio_queue_depth = Column(Integer, default=0)
    avg_audio_queue_depth = Column(Float, default=0.0)

    # VAD Metrics
    vad_config = Column(JSON)  # Store VAD configuration used
    vad_trigger_count = Column(Integer, default=0)
    speech_start_count = Column(Integer, default=0)
    speech_end_count = Column(Integer, default=0)
    interruption_count = Column(Integer, default=0)

    # Response Time Metrics
    time_to_first_ai_response_ms = Column(Integer)
    avg_ai_response_time_ms = Column(Float)

    # Turn Metrics
    ai_turn_count = Column(Integer, default=0)
    user_turn_count = Column(Integer, default=0)

    # Connection Quality
    websocket_duration_ms = Column(Integer)
    websocket_reconnect_count = Column(Integer, default=0)
    network_error_count = Column(Integer, default=0)

    # Gemini API Metrics
    gemini_session_duration_ms = Column(Integer)
    gemini_connection_errors = Column(Integer, default=0)
    gemini_model_version = Column(String)

    # Call Quality
    call_completion_status = Column(String)
    disconnection_reason = Column(String)
    error_messages = Column(JSON)

    # Additional Metadata
    twilio_call_sid = Column(String)
    twilio_stream_sid = Column(String)
    meta_data = Column(JSON, default=dict)

    # Relationship
    call = relationship("Call", back_populates="metrics", uselist=False)
