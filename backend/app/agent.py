"""The Excel agent: an LLM tool-calling loop over the deterministic engine.

Design notes
------------
* The model never touches a workbook. It may only pick a registered tool and supply
  JSON arguments, which are validated and executed by :mod:`app.excel_tools`. That
  keeps every spreadsheet behaviour deterministic and unit-testable without a model.
* Tools are **tiered**. ``P0`` tools (read / analyse / chart) ship enabled. ``P1``
  tools (create, update, formulas, formatting) are fully implemented and tested but
  stay disabled behind ``ENABLE_WRITE_TOOLS`` until that milestone is hardened.
* Authentication to Azure OpenAI uses an Entra token from DefaultAzureCredential -
  there is no API key anywhere in the code, config or pipeline.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook

from . import excel_tools as tools
from . import telemetry
from .charts import render_chart_png
from .config import get_settings

logger = logging.getLogger("excel_agent.agent")

TIER_P0 = "P0"
TIER_P1 = "P1"


def write_tools_enabled() -> bool:
    """P1 create/update tools are opt-in until that milestone is hardened."""
    return os.getenv("ENABLE_WRITE_TOOLS", "false").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict]
    mutates: bool = False
    #: extra, non-workbook output merged into the API response (e.g. chart images)
    enrich: Callable[[Workbook, dict, dict], dict] | None = field(default=None, compare=False)


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_SHEET = {"type": "string", "description": "Sheet name. Omit for the active sheet."}
_RANGE = {"type": "string", "description": "A1-style range, e.g. 'A1:D20'."}


def _chart_enrich(wb: Workbook, args: dict, result: dict) -> dict:
    """Attach a PNG preview of the chart that was just added to the workbook."""
    try:
        image = render_chart_png(
            wb,
            args.get("sheet"),
            args["chart_type"],
            args["data_range"],
            args.get("categories_range"),
            args.get("title"),
        )
        return {"chart_image": image}
    except Exception as exc:  # preview is best-effort; the Excel chart still exists
        logger.warning("Chart preview failed: %s", exc)
        return {"chart_image_error": str(exc)}


# --------------------------------------------------------------------------- #
# Tool registry
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, ToolSpec] = {
    # ---------------- P0: read, analyse, visualise ----------------
    "list_sheets": ToolSpec(
        name="list_sheets",
        tier=TIER_P0,
        description="List every sheet with its row/column counts. Call this first.",
        parameters=_schema({}),
        handler=lambda wb, **kw: tools.list_sheets(wb),
    ),
    "read_range": ToolSpec(
        name="read_range",
        tier=TIER_P0,
        description="Read cell values from a sheet or range to inspect the actual data.",
        parameters=_schema(
            {
                "sheet": _SHEET,
                "cell_range": _RANGE,
                "max_rows": {"type": "integer", "description": "Default 50, max 200."},
            }
        ),
        handler=lambda wb, **kw: tools.read_range(
            wb, kw.get("sheet"), kw.get("cell_range"), min(int(kw.get("max_rows", 50)), 200)
        ),
    ),
    "describe_sheet": ToolSpec(
        name="describe_sheet",
        tier=TIER_P0,
        description=(
            "Profile a sheet: per-column inferred type, null counts, distinct counts, "
            "numeric stats (min/max/mean/median/sum) and top categorical values."
        ),
        parameters=_schema({"sheet": _SHEET, "cell_range": _RANGE}),
        handler=lambda wb, **kw: tools.describe_sheet(wb, kw.get("sheet"), kw.get("cell_range")),
    ),
    "compute_aggregate": ToolSpec(
        name="compute_aggregate",
        tier=TIER_P0,
        description=(
            "Compute sum/mean/min/max/count/median/std over a range and return the real "
            "number. Use this instead of guessing, and before quoting any figure."
        ),
        parameters=_schema(
            {
                "sheet": _SHEET,
                "cell_range": _RANGE,
                "operation": {
                    "type": "string",
                    "enum": ["sum", "mean", "min", "max", "count", "median", "std"],
                },
            },
            ["cell_range", "operation"],
        ),
        handler=lambda wb, **kw: tools.compute_aggregate(
            wb, kw.get("sheet"), kw["cell_range"], kw["operation"]
        ),
    ),
    "create_chart": ToolSpec(
        name="create_chart",
        tier=TIER_P0,
        description=(
            "Add a native Excel chart (bar, column, line, pie, area, scatter) built from a "
            "data range, and return a PNG preview. data_range should include the header row "
            "so series are named."
        ),
        parameters=_schema(
            {
                "sheet": _SHEET,
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "column", "line", "pie", "area", "scatter"],
                },
                "data_range": {
                    "type": "string",
                    "description": "Numeric columns incl. header row, e.g. 'B1:C13'.",
                },
                "categories_range": {
                    "type": "string",
                    "description": "Axis labels, e.g. 'A2:A13'. X values for scatter.",
                },
                "title": _STR,
                "anchor": {"type": "string", "description": "Where to place it, default 'H2'."},
            },
            ["chart_type", "data_range"],
        ),
        handler=lambda wb, **kw: tools.create_chart(
            wb,
            kw.get("sheet"),
            kw["chart_type"],
            kw["data_range"],
            kw.get("categories_range"),
            kw.get("title"),
            kw.get("anchor", "H2"),
        ),
        mutates=True,
        enrich=_chart_enrich,
    ),
    # ---------------- P1: create & update (implemented, gated) ----------------
    "write_cells": ToolSpec(
        name="write_cells",
        tier=TIER_P1,
        description="Write a 2-D block of values with its top-left corner at an anchor cell.",
        parameters=_schema(
            {
                "sheet": _SHEET,
                "anchor": {"type": "string", "description": "Top-left cell, e.g. 'A1'."},
                "values": {"type": "array", "items": {"type": "array", "items": {}}},
            },
            ["anchor", "values"],
        ),
        handler=lambda wb, **kw: tools.write_cells(wb, kw.get("sheet"), kw["anchor"], kw["values"]),
        mutates=True,
    ),
    "set_formula": ToolSpec(
        name="set_formula",
        tier=TIER_P1,
        description="Set an Excel formula in a single cell, e.g. '=SUM(B2:B13)'.",
        parameters=_schema({"sheet": _SHEET, "cell": _STR, "formula": _STR}, ["cell", "formula"]),
        handler=lambda wb, **kw: tools.set_formula(wb, kw.get("sheet"), kw["cell"], kw["formula"]),
        mutates=True,
    ),
    "fill_formula": ToolSpec(
        name="fill_formula",
        tier=TIER_P1,
        description="Fill a range with a formula template using {row} and {col} placeholders.",
        parameters=_schema(
            {"sheet": _SHEET, "cell_range": _RANGE, "formula_template": _STR},
            ["cell_range", "formula_template"],
        ),
        handler=lambda wb, **kw: tools.fill_formula(
            wb, kw.get("sheet"), kw["cell_range"], kw["formula_template"]
        ),
        mutates=True,
    ),
    "format_cells": ToolSpec(
        name="format_cells",
        tier=TIER_P1,
        description="Apply font, fill colour, number format, alignment and borders to a range.",
        parameters=_schema(
            {
                "sheet": _SHEET,
                "cell_range": _RANGE,
                "bold": {"type": "boolean"},
                "italic": {"type": "boolean"},
                "font_size": {"type": "number"},
                "font_color": {"type": "string", "description": "Hex, e.g. 'FFFFFF'."},
                "fill_color": {"type": "string", "description": "Hex, e.g. '1F4E78'."},
                "number_format": {"type": "string", "description": "e.g. '#,##0.00' or '0.0%'."},
                "align": {"type": "string", "enum": ["left", "center", "right"]},
                "wrap_text": {"type": "boolean"},
                "border": {"type": "boolean"},
                "column_width": {"type": "number"},
            },
            ["cell_range"],
        ),
        handler=lambda wb, **kw: tools.format_cells(
            wb, kw.pop("sheet", None), kw.pop("cell_range"), **kw
        ),
        mutates=True,
    ),
    "conditional_format": ToolSpec(
        name="conditional_format",
        tier=TIER_P1,
        description="Add conditional formatting: color_scale, data_bar, cell_is or formula.",
        parameters=_schema(
            {
                "sheet": _SHEET,
                "cell_range": _RANGE,
                "rule_type": {
                    "type": "string",
                    "enum": ["color_scale", "data_bar", "cell_is", "formula"],
                },
                "operator": {
                    "type": "string",
                    "enum": ["greaterThan", "lessThan", "between", "equal", "notEqual"],
                },
                "value": {"type": "number"},
                "value2": {"type": "number"},
                "formula": _STR,
                "color": {"type": "string", "description": "Hex fill, default 'FFC7CE'."},
            },
            ["cell_range", "rule_type"],
        ),
        handler=lambda wb, **kw: tools.conditional_format(
            wb, kw.pop("sheet", None), kw.pop("cell_range"), kw.pop("rule_type"), **kw
        ),
        mutates=True,
    ),
    "add_sheet": ToolSpec(
        name="add_sheet",
        tier=TIER_P1,
        description="Add a new empty sheet to the workbook.",
        parameters=_schema({"name": _STR}, ["name"]),
        handler=lambda wb, **kw: tools.add_sheet(wb, kw["name"]),
        mutates=True,
    ),
    "sort_range": ToolSpec(
        name="sort_range",
        tier=TIER_P1,
        description="Sort a range by a header name or column letter, keeping the header row.",
        parameters=_schema(
            {
                "sheet": _SHEET,
                "cell_range": _RANGE,
                "by_column": _STR,
                "descending": {"type": "boolean"},
            },
            ["cell_range", "by_column"],
        ),
        handler=lambda wb, **kw: tools.sort_range(
            wb,
            kw.get("sheet"),
            kw["cell_range"],
            kw["by_column"],
            bool(kw.get("descending", False)),
        ),
        mutates=True,
    ),
    "autofit_columns": ToolSpec(
        name="autofit_columns",
        tier=TIER_P1,
        description="Resize every column to fit its widest value.",
        parameters=_schema({"sheet": _SHEET}),
        handler=lambda wb, **kw: tools.autofit_columns(wb, kw.get("sheet")),
        mutates=True,
    ),
}


def active_tools() -> dict[str, ToolSpec]:
    """Tools currently exposed to the model, honouring the P1 feature gate."""
    allow_write = write_tools_enabled()
    return {
        name: spec
        for name, spec in REGISTRY.items()
        if spec.tier == TIER_P0 or allow_write
    }


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in active_tools().values()
    ]


class ToolNotAvailable(KeyError):
    """Raised when the model asks for a tool that is unknown or feature-gated."""


def execute_tool(wb: Workbook, name: str, arguments: dict[str, Any]) -> tuple[dict, bool]:
    """Run one tool against the workbook. Returns ``(result, workbook_mutated)``."""
    available = active_tools()
    if name not in available:
        if name in REGISTRY:
            raise ToolNotAvailable(
                f"Tool '{name}' is part of the {REGISTRY[name].tier} milestone and is not "
                f"enabled in this deployment."
            )
        raise ToolNotAvailable(f"Unknown tool '{name}'.")
    spec = available[name]
    args = dict(arguments or {})
    with telemetry.track_operation(telemetry.EVT_TOOL, tool=name, tier=spec.tier):
        result = spec.handler(wb, **args)
        if spec.enrich:
            result = {**result, **spec.enrich(wb, arguments or {}, result)}
    return result, spec.mutates


SYSTEM_PROMPT = """You are an expert spreadsheet analyst embedded in a web app.

You are working with ONE workbook that is already loaded. Follow this method:
1. Call `list_sheets` first to learn the structure.
2. Call `describe_sheet` and/or `read_range` to see the real data before you claim anything.
3. Use `compute_aggregate` for every number you quote. NEVER estimate or invent figures.
4. When a visual would help - or the user asks for a chart/graph - call `create_chart`.
   Include the header row in `data_range` so series are labelled, and pass
   `categories_range` for the axis labels.

Answer in concise Markdown. Lead with the direct answer, then a short supporting
breakdown. Call out data-quality problems you notice (nulls, outliers, inconsistent
types). If a request needs editing the workbook (writing cells, formulas, formatting),
explain that those capabilities are part of the next milestone and are not enabled yet.
"""


@dataclass
class AgentResult:
    reply: str
    tool_calls: list[dict[str, Any]]
    workbook_mutated: bool
    charts: list[str]


class ExcelAgent:
    """Bounded tool-calling loop over Azure OpenAI."""

    def __init__(self, client: Any = None, deployment: str | None = None) -> None:
        settings = get_settings()
        self._client = client
        self._deployment = deployment or settings.openai_deployment
        self._max_steps = settings.max_agent_steps

    # -- lazily built so tests and offline runs never need Azure --------------
    @property
    def client(self) -> Any:
        if self._client is None:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            from openai import AzureOpenAI

            settings = get_settings()
            credential = DefaultAzureCredential(
                managed_identity_client_id=settings.managed_identity_client_id
            )
            self._client = AzureOpenAI(
                azure_endpoint=settings.openai_endpoint,
                api_version=settings.openai_api_version,
                azure_ad_token_provider=get_bearer_token_provider(
                    credential, "https://cognitiveservices.azure.com/.default"
                ),
            )
        return self._client

    def run(self, wb: Workbook, question: str, history: list[dict] | None = None) -> AgentResult:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in history or []:
            if turn.get("role") in {"user", "assistant"} and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": question})

        performed: list[dict[str, Any]] = []
        charts: list[str] = []
        mutated = False

        for _ in range(self._max_steps):
            response = self.client.chat.completions.create(
                model=self._deployment, messages=messages, tools=tool_schemas(), tool_choice="auto"
            )
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                return AgentResult(
                    reply=message.content or "I could not produce an answer.",
                    tool_calls=performed,
                    workbook_mutated=mutated,
                    charts=charts,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )

            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result, did_mutate = execute_tool(wb, name, args)
                    mutated = mutated or did_mutate
                    if result.get("chart_image"):
                        charts.append(result["chart_image"])
                    performed.append({"tool": name, "arguments": args, "ok": True})
                    payload = {k: v for k, v in result.items() if k != "chart_image"}
                except (tools.ExcelToolError, ToolNotAvailable) as exc:
                    payload = {"error": str(exc)}
                    performed.append(
                        {"tool": name, "arguments": args, "ok": False, "error": str(exc)}
                    )
                except Exception as exc:  # unexpected - surface to the model, keep serving
                    logger.exception("Tool %s failed", name)
                    payload = {"error": f"Internal error in {name}: {exc}"}
                    performed.append(
                        {"tool": name, "arguments": args, "ok": False, "error": str(exc)}
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(payload, default=str)[:12000],
                    }
                )

        return AgentResult(
            reply=(
                "I reached the tool-call limit for this turn. Here is what I gathered so far - "
                "please narrow the question and ask again."
            ),
            tool_calls=performed,
            workbook_mutated=mutated,
            charts=charts,
        )
