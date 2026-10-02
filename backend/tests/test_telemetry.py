"""Tests for the telemetry layer that powers /api/metrics and the Azure Workbook."""
from __future__ import annotations

import pytest

from app import telemetry


def test_track_event_counts_and_tracks_users():
    telemetry.track_event("Thing", {"outcome": "success"}, user_id="a")
    telemetry.track_event("Thing", {"outcome": "success"}, user_id="b")
    telemetry.track_event("Thing", {"outcome": "failure"}, user_id="a")

    snap = telemetry.snapshot()
    assert snap["counters"]["Thing"] == 3
    assert snap["total_users"] == 2
    assert snap["operations"]["Thing"]["success_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_track_operation_records_success_and_duration():
    with telemetry.track_operation("Op", user_id="u"):
        pass
    op = telemetry.snapshot()["operations"]["Op"]
    assert op["success"] == 1 and op["failure"] == 0
    assert op["p95_ms"] >= 0


def test_track_operation_records_failure_and_reraises():
    with pytest.raises(ValueError):
        with telemetry.track_operation("Op", user_id="u"):
            raise ValueError("boom")

    snap = telemetry.snapshot()
    assert snap["operations"]["Op"]["failure"] == 1
    assert snap["counters"][telemetry.EVT_FAILURE] == 1
    assert snap["total_failures"] == 1


def test_extra_properties_can_be_attached_mid_operation():
    with telemetry.track_operation("Op") as extra:
        extra["chart_type"] = "pie"
    assert telemetry.snapshot()["operations"]["Op"]["success"] == 1


def test_snapshot_is_safe_when_empty():
    snap = telemetry.snapshot()
    assert snap["total_operations"] == 0
    assert snap["overall_success_rate"] is None
    assert snap["uptime_seconds"] >= 0


def test_duration_buffer_is_bounded():
    for _ in range(600):
        telemetry._record_duration("Op", 1.0)
    assert len(telemetry._durations["Op"]) == 500
