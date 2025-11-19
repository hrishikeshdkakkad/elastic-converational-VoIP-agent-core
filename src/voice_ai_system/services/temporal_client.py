"""Temporal client service for connecting to Temporal server."""

import structlog
from temporalio.client import Client

from src.voice_ai_system.config import settings

logger = structlog.get_logger(__name__)

_temporal_client: Client | None = None


async def get_temporal_client() -> Client:
    """
    Get or create Temporal client connection.

    Returns:
        Temporal client instance
    """
    global _temporal_client

    if _temporal_client is not None:
        return _temporal_client

    logger.info(
        "Connecting to Temporal",
        host=settings.temporal_host,
        port=settings.temporal_port,
        namespace=settings.temporal_namespace,
    )

    try:
        _temporal_client = await Client.connect(
            target_host=settings.temporal_address,
            namespace=settings.temporal_namespace,
        )

        logger.info("Successfully connected to Temporal")
        return _temporal_client

    except Exception as e:
        logger.error("Failed to connect to Temporal", error=str(e))
        raise


async def close_temporal_client() -> None:
    """Close Temporal client connection."""
    global _temporal_client

    if _temporal_client is not None:
        logger.info("Closing Temporal client connection")
        await _temporal_client.close()
        _temporal_client = None
