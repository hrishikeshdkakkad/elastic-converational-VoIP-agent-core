"""Temporal activities for Twilio interactions."""

from typing import Any, Optional
import asyncio
import logging
import threading

from temporalio import activity
from twilio.rest import Client

from src.voice_ai_system.config import settings

# Enable Twilio SDK debug logging
logging.basicConfig()
logging.getLogger('twilio').setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


# Singleton Twilio client for connection reuse
# Thread-safe initialization using a lock
_twilio_client: Optional[Client] = None
_twilio_client_lock = threading.Lock()


def get_twilio_client() -> Client:
    """Get or create a singleton Twilio client.

    The Twilio Client uses HTTP connection pooling internally,
    so reusing the same client improves performance and reduces
    connection overhead.

    Thread-safe implementation for use across multiple activities.
    """
    global _twilio_client

    if _twilio_client is not None:
        return _twilio_client

    with _twilio_client_lock:
        # Double-check after acquiring lock
        if _twilio_client is None:
            logger.info("Creating singleton Twilio client")
            _twilio_client = Client(
                settings.twilio_account_sid,
                settings.twilio_auth_token
            )

    return _twilio_client


@activity.defn(name="initiate_twilio_call")
async def initiate_twilio_call(params: dict[str, Any]) -> dict[str, Any]:
    """
    Initiate an outbound call via Twilio.

    Args:
        params: Dictionary containing:
            - call_id: Call identifier
            - phone_number: Phone number to call
            - workflow_id: Workflow identifier for callback URL

    Returns:
        Dictionary with call_sid and status
    """
    activity.logger.info(
        f"Initiating Twilio call to {params['phone_number']}",
        extra={"call_id": params["call_id"]},
    )

    # Get singleton Twilio client (reuses connections)
    client = get_twilio_client()

    # Build callback URLs and WebSocket URL
    base_url = settings.base_url
    status_callback_url = f"{base_url}/twilio/status/{params['workflow_id']}"

    # Generate WebSocket URL for Media Streams
    ws_scheme = "wss" if base_url.startswith("https") else "ws"
    ws_host = base_url.replace("https://", "").replace("http://", "")
    ws_url = f"{ws_scheme}://{ws_host}/twilio/ws/media/{params['workflow_id']}"

    activity.logger.info(f"🔗 Twilio - WebSocket: {ws_url}, Status: {status_callback_url}")

    # Generate inline TwiML with WebSocket URL embedded
    # This bypasses the Twilio SDK bug where 'url' parameter is ignored

    # CRITICAL: Add statusCallback to monitor stream connection attempts
    stream_status_callback_url = f"{base_url}/twilio/stream-status/{params['workflow_id']}"

    # OPTIMIZATION: Remove <Say> to execute <Connect><Stream> immediately
    # <Connect> blocks execution, so no need for delay - goes straight to WebSocket
    twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" statusCallback="{stream_status_callback_url}" statusCallbackMethod="POST">
            <Parameter name="workflow_id" value="{params['workflow_id']}" />
        </Stream>
    </Connect>
</Response>"""

    activity.logger.info(
        f"Creating Twilio call",
        extra={
            "phone_number": params['phone_number'],
            "from_number": settings.twilio_phone_number,
            "ws_url": ws_url,
            "status_callback": status_callback_url,
        }
    )

    try:
        # Run sync Twilio call in thread pool (Twilio SDK is sync-only for calls)
        def _create_call():
            result = client.calls.create(
                to=params["phone_number"],
                from_=settings.twilio_phone_number,
                twiml=twiml_content,  # Inline TwiML with WebSocket connection
                status_callback=status_callback_url,
                status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
                status_callback_method='POST'
            )
            return result

        call = await asyncio.to_thread(_create_call)

        activity.logger.info(
            f"Twilio call initiated successfully: {call.sid}",
            extra={"call_sid": call.sid}
        )

        return {
            "call_sid": call.sid,
            "status": call.status,
            "to": call.to,
        }
    except Exception as e:
        activity.logger.error(f"Failed to initiate Twilio call: {str(e)}")
        raise


@activity.defn(name="terminate_twilio_call")
async def terminate_twilio_call(call_sid: str) -> dict[str, Any]:
    """
    Terminate an active Twilio call.

    Args:
        call_sid: Twilio call SID

    Returns:
        Dictionary with termination status
    """
    activity.logger.info(f"Terminating Twilio call {call_sid}")

    # Get singleton Twilio client (reuses connections)
    client = get_twilio_client()

    try:
        # Run sync Twilio update in thread pool
        def _terminate_call():
            return client.calls(call_sid).update(status='completed')

        call = await asyncio.to_thread(_terminate_call)

        activity.logger.info(
            f"Twilio call terminated successfully: {call_sid}",
            extra={"call_sid": call_sid, "status": call.status}
        )

        return {
            "call_sid": call.sid,
            "status": call.status,
        }
    except Exception as e:
        activity.logger.error(f"Failed to terminate Twilio call: {str(e)}")
        raise


@activity.defn(name="get_twilio_call_status")
async def get_twilio_call_status(call_sid: str) -> dict[str, Any]:
    """
    Get current status of a Twilio call.

    Args:
        call_sid: Twilio call SID

    Returns:
        Dictionary with call status and details
    """
    activity.logger.info(f"Getting status for Twilio call {call_sid}")

    # Get singleton Twilio client (reuses connections)
    client = get_twilio_client()

    try:
        # Run sync Twilio fetch in thread pool
        def _fetch_call():
            return client.calls(call_sid).fetch()

        call = await asyncio.to_thread(_fetch_call)

        return {
            "call_sid": call.sid,
            "status": call.status,
            "duration": call.duration,
            "start_time": call.start_time.isoformat() if call.start_time else None,
            "end_time": call.end_time.isoformat() if call.end_time else None,
        }
    except Exception as e:
        activity.logger.error(f"Failed to fetch Twilio call status: {str(e)}")
        raise
