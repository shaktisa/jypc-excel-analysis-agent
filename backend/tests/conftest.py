"""Shared fixtures. Every test runs fully offline - no Azure, no LLM."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("EXCEL_AGENT_OFFLINE", "true")
os.environ.setdefault("ENABLE_WRITE_TOOLS", "false")

from app import telemetry  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.excel_tools import new_workbook, workbook_to_bytes  # noqa: E402
from app.storage import LocalWorkbookStore, reset_store  # noqa: E402

SALES_ROWS = [
    ["Region", "Quarter", "Revenue", "Units"],
    ["North", "Q1", 125000, 540],
    ["North", "Q2", 143500, 612],
    ["South", "Q1", 98000, 410],
    ["South", "Q2", 87250, 365],
    ["East", "Q1", 210400, 905],
    ["East", "Q2", 232900, 988],
    ["West", "Q1", 76300, 318],
    ["West", "Q2", None, 402],
]


@pytest.fixture
def sales_workbook():
    """A small, realistic workbook with a deliberate null for data-quality tests."""
    wb = new_workbook("Sales")
    ws = wb.active
    for row in SALES_ROWS:
        ws.append(row)
    return wb


@pytest.fixture
def sales_bytes(sales_workbook) -> bytes:
    return workbook_to_bytes(sales_workbook)


@pytest.fixture
def store(tmp_path):
    get_settings.cache_clear()
    s = LocalWorkbookStore(tmp_path)
    reset_store(s)
    yield s
    reset_store(None)


@pytest.fixture(autouse=True)
def clean_telemetry():
    telemetry.reset()
    yield
    telemetry.reset()
