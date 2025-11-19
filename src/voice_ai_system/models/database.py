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
