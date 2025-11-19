"""FastAPI application for voice AI system."""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from src.voice_ai_system.api.routes import calls, health, twilio
from src.voice_ai_system.config import settings
from src.voice_ai_system.services.database import init_engine, dispose_engine
from src.voice_ai_system.services.temporal_client import get_temporal_client
from src.voice_ai_system.utils.logging import configure_logging

# Configure logging
configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting voice AI system API", environment=settings.environment)

    # Store settings in app state (CRITICAL: needed by routes)
    app.state.settings = settings

    # Initialise database engine lazily
    await init_engine(settings.database_url)

    # Initialize Temporal client
    try:
        temporal_client = await get_temporal_client()
        app.state.temporal_client = temporal_client
        logger.info("Connected to Temporal", address=settings.temporal_address)
    except Exception as e:
        logger.error("Failed to connect to Temporal", error=str(e))
        raise

    yield

    # Cleanup
    await dispose_engine()
    logger.info("Shutting down voice AI system API")


# Create FastAPI app
app = FastAPI(
    title="Voice AI System",
    description="Production voice AI with Twilio, Gemini, and Temporal",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(calls.router, prefix="/calls", tags=["calls"])
app.include_router(twilio.router, prefix="/twilio", tags=["twilio"])

# Add Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "voice-ai-system",
        "version": "0.1.0",
        "environment": settings.environment,
        "temporal": {
            "address": settings.temporal_address,
            "namespace": settings.temporal_namespace,
        },
    }
