"""Redis client for session state management."""

import json
from typing import Any, Optional
import redis.asyncio as redis

from src.voice_ai_system.config import settings


class RedisSessionStore:
    """
    Redis-based session storage for Gemini session state.

    This provides a shared state store that allows:
    - FastAPI WebSocket handlers to read session configuration
    - Temporal activities to create/update/delete session records
    - Horizontal scaling (multiple API/worker instances share state)
    - Fault tolerance (session survives individual instance restarts)
    """

    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis."""
        if not self._client:
            self._client = await redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )

    async def disconnect(self):
        """Disconnect from Redis."""
        if self._client:
            await self._client.close()
            self._client = None

    async def create_session(
        self,
        workflow_id: str,
        call_id: str,
        phone_number: str,
        greeting: str = "",
        system_prompt: Optional[str] = None,
        max_duration_seconds: int = 1800
    ) -> dict[str, Any]:
        """
        Create a new session record in Redis.

        Args:
            workflow_id: Temporal workflow ID
            call_id: Call UUID
            phone_number: Phone number being called
            greeting: Initial greeting message
            system_prompt: Custom system prompt for AI
            max_duration_seconds: Maximum call duration

        Returns:
            Session data dictionary
        """
        await self.connect()

        session_data = {
            "workflow_id": workflow_id,
            "call_id": call_id,
            "phone_number": phone_number,
            "greeting": greeting,
            "system_prompt": system_prompt or "You are a helpful voice assistant.",
            "max_duration_seconds": max_duration_seconds,
            "status": "pending",
            "created_at": None,  # Set by activity
        }

        # Store as Redis Hash
        key = f"session:{workflow_id}"
        await self._client.hset(
            key,
            mapping={k: json.dumps(v) for k, v in session_data.items()}
        )

        # Set TTL to prevent orphaned sessions
        await self._client.expire(key, settings.redis_session_ttl)

        return session_data

    async def get_session(self, workflow_id: str) -> Optional[dict[str, Any]]:
        """
        Get session data from Redis.

        Args:
            workflow_id: Temporal workflow ID

        Returns:
            Session data dictionary or None if not found
        """
        await self.connect()

        key = f"session:{workflow_id}"
        data = await self._client.hgetall(key)

        if not data:
            return None

        # Deserialize JSON values
        return {k: json.loads(v) for k, v in data.items()}

    async def update_session_status(
        self,
        workflow_id: str,
        status: str,
        **additional_fields
    ) -> bool:
        """
        Update session status and optional additional fields.

        Args:
            workflow_id: Temporal workflow ID
            status: New status (e.g., "in_progress", "completed", "failed")
            **additional_fields: Additional fields to update

        Returns:
            True if session was updated, False if not found
        """
        await self.connect()

        key = f"session:{workflow_id}"
        exists = await self._client.exists(key)

        if not exists:
            return False

        # Update status
        updates = {"status": json.dumps(status)}

        # Update additional fields
        for field, value in additional_fields.items():
            updates[field] = json.dumps(value)

        await self._client.hset(key, mapping=updates)
        return True

    async def delete_session(self, workflow_id: str) -> bool:
        """
        Delete a session record.

        Args:
            workflow_id: Temporal workflow ID

        Returns:
            True if session was deleted, False if not found
        """
        await self.connect()

        key = f"session:{workflow_id}"
        deleted = await self._client.delete(key)
        return deleted > 0

    async def set_session_ttl(self, workflow_id: str, ttl_seconds: int):
        """
        Set or update TTL for a session.

        Args:
            workflow_id: Temporal workflow ID
            ttl_seconds: Time to live in seconds
        """
        await self.connect()

        key = f"session:{workflow_id}"
        await self._client.expire(key, ttl_seconds)


# Global instance
redis_store = RedisSessionStore()
