"""Health and telemetry endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from .. import telemetry
from ..agent import write_tools_enabled
from ..config import get_settings

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    """Liveness probe used by App Service and the CI smoke test."""
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.env_label,
        "offline": settings.offline,
        "write_tools_enabled": write_tools_enabled(),
    }


@router.get("/metrics")
def metrics() -> dict:
    """Live application metrics: success rates, users, failures, latency."""
    return telemetry.snapshot()
