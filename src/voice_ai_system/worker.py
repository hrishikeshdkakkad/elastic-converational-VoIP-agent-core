"""Temporal worker for executing workflows and activities."""

import asyncio
import signal
import sys

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from src.voice_ai_system.activities import (
    database_activities,
    session_activities,
    twilio_activities,
)
from src.voice_ai_system.config import settings
from src.voice_ai_system.services.database import init_engine, dispose_engine
from src.voice_ai_system.utils.logging import configure_logging
from src.voice_ai_system.workflows.call_workflow import VoiceCallWorkflow

# Configure logging
configure_logging()
logger = structlog.get_logger(__name__)


async def run_worker():
    """Run the Temporal worker."""
    logger.info(
        "Starting Temporal worker",
        temporal_address=settings.temporal_address,
        namespace=settings.temporal_namespace,
        task_queue=settings.worker_task_queue,
    )

    # Connect to Temporal
    try:
        client = await Client.connect(
            target_host=settings.temporal_address,
            namespace=settings.temporal_namespace,
        )
        logger.info("Connected to Temporal server")
    except Exception as e:
        logger.error("Failed to connect to Temporal", error=str(e))
        sys.exit(1)

    # Initialise the database engine once per worker process
    await init_engine(settings.database_url)

    # Collect all activities
    activities = [
        # Twilio activities
        twilio_activities.initiate_twilio_call,
        twilio_activities.terminate_twilio_call,
        twilio_activities.get_twilio_call_status,
        # Redis session activities
        session_activities.create_session_record,
        session_activities.update_session_status,
        session_activities.cleanup_session_record,
        session_activities.get_session_record,
        # Database activities
        database_activities.create_call_record,
        database_activities.update_call_record,
        database_activities.mark_call_as_failed,
        database_activities.save_transcript_batch,
        database_activities.save_call_event,
        database_activities.get_call_transcripts,
        database_activities.get_call_by_workflow_id,
    ]

    # Create worker
    worker = Worker(
        client,
        task_queue=settings.worker_task_queue,
        workflows=[VoiceCallWorkflow],
        activities=activities,
        max_concurrent_activities=settings.max_concurrent_activities,
        max_concurrent_workflow_tasks=settings.max_concurrent_workflows,
    )

    logger.info(
        "Worker configured",
        workflows=["VoiceCallWorkflow"],
        activities_count=len(activities),
        max_concurrent_activities=settings.max_concurrent_activities,
        max_concurrent_workflows=settings.max_concurrent_workflows,
    )

    # Run worker
    try:
        logger.info("Worker started and ready to process tasks")
        await worker.run()
    except asyncio.CancelledError:
        logger.info("Worker cancelled, shutting down gracefully")
    except Exception as e:
        logger.error("Worker error", error=str(e), exc_info=True)
        raise
    finally:
        await client.close()
        await dispose_engine()
        logger.info("Worker shutdown complete")


def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating graceful shutdown")
    sys.exit(0)


def main():
    """Main entry point for worker."""
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Run worker
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error("Fatal error in worker", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
