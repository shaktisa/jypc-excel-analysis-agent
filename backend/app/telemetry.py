"""Telemetry: in-process counters plus Azure Application Insights custom events.

Two layers, deliberately:

* **In-process counters** - dependency-free, unit-testable, and exposed at
  ``GET /api/metrics`` so health and success rates are visible even if Azure
  Monitor is unreachable. These reset on restart and are per-replica.
* **Application Insights custom events** - the durable, cross-replica source of
  truth that the Azure Monitor Workbook in ``infra/telemetry-workbook.json``
  queries for success rate, active users, failures and latency.

Export is enabled only when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is set, so
tests and local runs stay completely offline.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("excel_agent.telemetry")

# Event names are a closed set so the Workbook queries can rely on them.
EVT_UPLOAD = "WorkbookUploaded"
EVT_ANALYSIS = "AnalysisRequested"
EVT_CHART = "ChartGenerated"
EVT_CHAT = "ChatTurn"
EVT_TOOL = "AgentToolCall"
EVT_DOWNLOAD = "VersionDownloaded"
EVT_FAILURE = "OperationFailed"

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_durations: dict[str, list[float]] = defaultdict(list)
_users: set[str] = set()
_started_at = datetime.now(UTC)
_client: Any = None
_configured = False


def configure() -> None:
    """Attach the Azure Monitor exporter once, if configured."""
    global _client, _configured
    if _configured:
        return
    _configured = True
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not conn:
        logger.info("App Insights not configured; using in-process counters only.")
        return
    try:
        from applicationinsights import TelemetryClient  # type: ignore

        _client = TelemetryClient(conn)
        logger.info("Application Insights telemetry enabled.")
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning("Could not initialise App Insights: %s", exc)


def track_event(
    name: str,
    properties: dict[str, Any] | None = None,
    measurements: dict[str, float] | None = None,
    user_id: str | None = None,
) -> None:
    """Record a custom event in both telemetry layers."""
    props = {str(k): str(v) for k, v in (properties or {}).items()}
    if user_id:
        props["user_id"] = user_id
        with _lock:
            _users.add(user_id)
    with _lock:
        _counters[name] += 1
        outcome = props.get("outcome")
        if outcome:
            _counters[f"{name}.{outcome}"] += 1
    if _client is not None:  # pragma: no cover - requires live App Insights
        try:
            _client.track_event(name, props, measurements or {})
            _client.flush()
        except Exception as exc:
            logger.debug("Telemetry emit failed: %s", exc)


@contextmanager
def track_operation(
    name: str, user_id: str | None = None, **properties: Any
) -> Iterator[dict[str, Any]]:
    """Time an operation and emit success/failure telemetry automatically.

    Yields a mutable dict so callers can attach extra properties discovered
    during the operation (e.g. the chart type that was ultimately chosen).
    """
    extra: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield extra
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        _record_duration(name, elapsed)
        track_event(
            name,
            {**properties, **extra, "outcome": "failure", "error_type": type(exc).__name__},
            {"duration_ms": elapsed},
            user_id,
        )
        track_event(
            EVT_FAILURE,
            {"operation": name, "error_type": type(exc).__name__, "error": str(exc)[:200]},
            {"duration_ms": elapsed},
            user_id,
        )
        raise
    else:
        elapsed = (time.perf_counter() - start) * 1000
        _record_duration(name, elapsed)
        track_event(
            name, {**properties, **extra, "outcome": "success"}, {"duration_ms": elapsed}, user_id
        )


def _record_duration(name: str, ms: float) -> None:
    with _lock:
        bucket = _durations[name]
        bucket.append(ms)
        if len(bucket) > 500:  # bounded memory
            del bucket[:-500]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(round((pct / 100) * (len(ordered) - 1))), len(ordered) - 1)
    return round(ordered[idx], 2)


def snapshot() -> dict[str, Any]:
    """Point-in-time metrics for ``GET /api/metrics`` and the health dashboard."""
    with _lock:
        counters = dict(_counters)
        durations = {k: list(v) for k, v in _durations.items()}
        user_count = len(_users)

    operations: dict[str, Any] = {}
    for name in {k.split(".")[0] for k in counters}:
        success = counters.get(f"{name}.success", 0)
        failure = counters.get(f"{name}.failure", 0)
        total = success + failure
        if total == 0 and name not in durations:
            continue
        samples = durations.get(name, [])
        operations[name] = {
            "total": total or counters.get(name, 0),
            "success": success,
            "failure": failure,
            "success_rate": round(success / total, 4) if total else None,
            "p50_ms": _percentile(samples, 50),
            "p95_ms": _percentile(samples, 95),
        }

    total_success = sum(v["success"] for v in operations.values())
    total_failure = sum(v["failure"] for v in operations.values())
    total_ops = total_success + total_failure
    return {
        "started_at": _started_at.isoformat(timespec="seconds"),
        "uptime_seconds": int((datetime.now(UTC) - _started_at).total_seconds()),
        "total_users": user_count,
        "total_operations": total_ops,
        "overall_success_rate": round(total_success / total_ops, 4) if total_ops else None,
        "total_failures": total_failure,
        "app_insights_enabled": _client is not None,
        "counters": counters,
        "operations": operations,
    }


def reset() -> None:
    """Test hook."""
    with _lock:
        _counters.clear()
        _durations.clear()
        _users.clear()
