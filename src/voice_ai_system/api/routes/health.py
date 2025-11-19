"""Health check endpoints."""

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        service="voice-ai-system",
        version="0.1.0",
    )


@router.get("/health/ready", status_code=status.HTTP_200_OK)
async def readiness_check(request: Request):
    """Readiness check endpoint."""
    # Check Temporal connection
    if not hasattr(request.app.state, "temporal_client"):
        return {
            "status": "not_ready",
            "reason": "Temporal client not initialized",
        }, status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready"}


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness_check():
    """Liveness check endpoint."""
    return {"status": "alive"}
