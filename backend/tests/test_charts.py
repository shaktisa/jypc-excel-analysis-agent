"""Tests for chart PNG rendering (the browser-previewable half of charting)."""
from __future__ import annotations

import base64

import pytest

from app.charts import render_chart_png
from app.excel_tools import ExcelToolError, new_workbook


@pytest.mark.parametrize("chart_type", ["bar", "column", "line", "pie", "area", "scatter"])
def test_render_returns_png_data_uri(sales_workbook, chart_type):
    data_range = "C1:D9" if chart_type == "scatter" else "C1:C9"
    uri = render_chart_png(sales_workbook, "Sales", chart_type, data_range, "A2:A9", "Revenue")
    assert uri.startswith("data:image/png;base64,")
    payload = base64.b64decode(uri.split(",", 1)[1])
    assert payload[:8] == b"\x89PNG\r\n\x1a\n", "must be a real PNG"
    assert len(payload) > 1000


def test_render_rejects_empty_range():
    wb = new_workbook("Empty")
    with pytest.raises(ExcelToolError, match="No numeric data"):
        render_chart_png(wb, "Empty", "bar", "A1:A5")


def test_render_handles_nulls_without_crashing(sales_workbook):
    uri = render_chart_png(sales_workbook, "Sales", "column", "C1:C9", "A2:A9", "With a gap")
    assert uri.startswith("data:image/png;base64,")
