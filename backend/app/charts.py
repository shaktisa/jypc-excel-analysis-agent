"""Chart image rendering.

``excel_tools.create_chart`` writes a *native* Excel chart into the workbook so the
downloaded file behaves like a real spreadsheet. That chart is an XML part, which a
browser cannot display, so this module renders an equivalent PNG purely for preview
in the UI. The two always come from the same data range.
"""
from __future__ import annotations

import base64
import io
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless container - must be set before pyplot import
import matplotlib.pyplot as plt  # noqa: E402

from .excel_tools import ExcelToolError, _bounds, _sheet  # noqa: E402

_PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"]


def _column_values(ws, cell_range: str) -> tuple[list[str], list[list[float]], int]:
    min_row, min_col, max_row, max_col = _bounds(ws, cell_range)
    headers: list[str] = []
    series: list[list[float]] = []
    numeric_cells = 0
    for col in range(min_col, max_col + 1):
        cells = [ws.cell(row=r, column=col).value for r in range(min_row, max_row + 1)]
        head, body = cells[0], cells[1:]
        headers.append(str(head) if head is not None else f"Series {col - min_col + 1}")
        numeric_cells += sum(1 for v in body if _is_numeric(v))
        series.append([_num(v) for v in body])
    return headers, series, numeric_cells


def _is_numeric(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _labels(ws, cell_range: str | None, count: int) -> list[str]:
    if not cell_range:
        return [str(i + 1) for i in range(count)]
    min_row, min_col, max_row, max_col = _bounds(ws, cell_range)
    out = [
        str(ws.cell(row=r, column=c).value)
        for r in range(min_row, max_row + 1)
        for c in range(min_col, max_col + 1)
        if ws.cell(row=r, column=c).value is not None
    ]
    if len(out) == count + 1:  # categories range included the header
        out = out[1:]
    return (out + [""] * count)[:count]


def render_chart_png(
    wb,
    sheet: str | None,
    chart_type: str,
    data_range: str,
    categories_range: str | None = None,
    title: str | None = None,
) -> str:
    """Render the same data as a base64 PNG data-URI for in-browser preview."""
    ws = _sheet(wb, sheet)
    headers, series, numeric_cells = _column_values(ws, data_range)
    if not series or not numeric_cells:
        raise ExcelToolError(f"No numeric data found in range '{data_range}'.")
    length = max(len(s) for s in series)
    labels = _labels(ws, categories_range, length)
    kind = chart_type.strip().lower()

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=110)
    try:
        if kind == "pie":
            values = [abs(v) for v in series[0]]
            ax.pie(values, labels=labels, autopct="%1.1f%%", colors=_PALETTE, startangle=90)
            ax.axis("equal")
        elif kind in {"bar", "column"}:
            width = 0.8 / len(series)
            positions = range(length)
            for i, (head, values) in enumerate(zip(headers, series, strict=False)):
                offsets = [p + i * width - 0.4 + width / 2 for p in positions]
                padded = (values + [0.0] * length)[:length]
                if kind == "bar":
                    ax.barh(offsets, padded, height=width, label=head, color=_PALETTE[i % 6])
                else:
                    ax.bar(offsets, padded, width=width, label=head, color=_PALETTE[i % 6])
            if kind == "bar":
                ax.set_yticks(list(positions), labels)
            else:
                ax.set_xticks(list(positions), labels, rotation=45, ha="right")
            if len(series) > 1:
                ax.legend(fontsize=8)
        elif kind == "scatter":
            x = series[0]
            for i, (head, values) in enumerate(
                zip(headers[1:], series[1:], strict=False), start=1
            ):
                ax.scatter(x[: len(values)], values, label=head, color=_PALETTE[i % 6])
            ax.legend(fontsize=8)
        else:  # line / area
            for i, (head, values) in enumerate(zip(headers, series, strict=False)):
                padded = (values + [0.0] * length)[:length]
                ax.plot(range(length), padded, label=head, color=_PALETTE[i % 6], marker="o")
                if kind == "area":
                    ax.fill_between(range(length), padded, alpha=0.25, color=_PALETTE[i % 6])
            ax.set_xticks(range(length), labels, rotation=45, ha="right")
            if len(series) > 1:
                ax.legend(fontsize=8)

        if title:
            ax.set_title(title, fontsize=12, fontweight="bold")
        if kind != "pie":
            ax.grid(axis="y", alpha=0.25, linestyle="--")
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    finally:
        plt.close(fig)
