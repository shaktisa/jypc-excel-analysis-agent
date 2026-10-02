"""Tests for the agent: tool gating, dispatch, and the tool-calling loop.

A fake OpenAI client is injected, so these tests assert agent *behaviour* without
any network call or model cost.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent import (
    TIER_P0,
    ExcelAgent,
    ToolNotAvailable,
    active_tools,
    execute_tool,
    tool_schemas,
)


# --------------------------------------------------------------------------- #
# Fake LLM
# --------------------------------------------------------------------------- #
def _tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


class FakeClient:
    """Replays a scripted list of assistant messages."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        message = self._scripted.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def assistant(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


# --------------------------------------------------------------------------- #
# Tool tiering / feature gate
# --------------------------------------------------------------------------- #
def test_p0_tools_are_exposed_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
    names = set(active_tools())
    assert names == {
        "list_sheets",
        "read_range",
        "describe_sheet",
        "compute_aggregate",
        "create_chart",
    }
    assert all(s.tier == TIER_P0 for s in active_tools().values())


def test_p1_tools_are_gated_off(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
    assert "write_cells" not in active_tools()
    assert "set_formula" not in active_tools()
    assert "format_cells" not in active_tools()


def test_p1_tools_appear_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "true")
    names = set(active_tools())
    assert {"write_cells", "set_formula", "format_cells", "conditional_format"} <= names


def test_gated_tool_call_is_rejected_with_a_clear_message(monkeypatch, sales_workbook):
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
    with pytest.raises(ToolNotAvailable, match="P1 milestone"):
        execute_tool(sales_workbook, "write_cells", {"anchor": "A1", "values": [[1]]})


def test_unknown_tool_is_rejected(sales_workbook):
    with pytest.raises(ToolNotAvailable, match="Unknown tool"):
        execute_tool(sales_workbook, "launch_rocket", {})


def test_tool_schemas_are_valid_openai_function_specs(monkeypatch):
    monkeypatch.setenv("ENABLE_WRITE_TOOLS", "false")
    for schema in tool_schemas():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        for field in fn["parameters"].get("required", []):
            assert field in fn["parameters"]["properties"]


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def test_execute_read_tool_returns_data_and_no_mutation(sales_workbook):
    result, mutated = execute_tool(sales_workbook, "describe_sheet", {"sheet": "Sales"})
    assert mutated is False
    assert result["row_count"] == 8


def test_execute_chart_tool_mutates_and_attaches_preview(sales_workbook):
    result, mutated = execute_tool(
        sales_workbook,
        "create_chart",
        {"chart_type": "column", "data_range": "C1:C9", "categories_range": "A2:A9"},
    )
    assert mutated is True
    assert result["chart_image"].startswith("data:image/png;base64,")


def test_execute_tool_emits_telemetry(sales_workbook):
    from app import telemetry

    execute_tool(sales_workbook, "list_sheets", {})
    snap = telemetry.snapshot()
    assert snap["counters"]["AgentToolCall"] == 1
    assert snap["operations"]["AgentToolCall"]["success"] == 1


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #
def test_agent_answers_directly_without_tools(sales_workbook):
    client = FakeClient([assistant(content="Hello, upload a sheet and ask away.")])
    result = ExcelAgent(client=client, deployment="test").run(sales_workbook, "hi")
    assert result.reply.startswith("Hello")
    assert result.tool_calls == []
    assert result.workbook_mutated is False


def test_agent_runs_a_tool_then_answers(sales_workbook):
    client = FakeClient(
        [
            assistant(tool_calls=[_tool_call("c1", "compute_aggregate",
                                             {"cell_range": "C2:C9", "operation": "sum"})]),
            assistant(content="Total revenue is 973,350."),
        ]
    )
    result = ExcelAgent(client=client, deployment="test").run(sales_workbook, "total revenue?")
    assert "973,350" in result.reply
    assert result.tool_calls[0]["tool"] == "compute_aggregate"
    assert result.tool_calls[0]["ok"] is True
    # the tool result was fed back to the model
    tool_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
    assert json.loads(tool_messages[0]["content"])["result"] == 973350.0


def test_agent_chart_request_produces_image_and_marks_mutation(sales_workbook):
    client = FakeClient(
        [
            assistant(tool_calls=[_tool_call("c1", "create_chart", {
                "chart_type": "column", "data_range": "C1:C9", "categories_range": "A2:A9",
                "title": "Revenue by region"})]),
            assistant(content="Here is the chart."),
        ]
    )
    result = ExcelAgent(client=client, deployment="test").run(sales_workbook, "chart it")
    assert result.workbook_mutated is True
    assert len(result.charts) == 1
    assert result.charts[0].startswith("data:image/png;base64,")


def test_agent_recovers_from_a_bad_tool_argument(sales_workbook):
    """A tool error must be returned to the model, not raised to the user."""
    client = FakeClient(
        [
            assistant(tool_calls=[_tool_call("c1", "read_range", {"sheet": "Missing"})]),
            assistant(content="That sheet does not exist; try 'Sales'."),
        ]
    )
    result = ExcelAgent(client=client, deployment="test").run(sales_workbook, "read Missing")
    assert result.tool_calls[0]["ok"] is False
    assert "not found" in result.tool_calls[0]["error"]
    assert "Sales" in result.reply


def test_agent_stops_at_the_step_limit(sales_workbook):
    looping = [
        assistant(tool_calls=[_tool_call(f"c{i}", "list_sheets", {})]) for i in range(20)
    ]
    agent = ExcelAgent(client=FakeClient(looping), deployment="test")
    agent._max_steps = 3
    result = agent.run(sales_workbook, "loop forever")
    assert "tool-call limit" in result.reply
    assert len(result.tool_calls) == 3


def test_agent_passes_history_to_the_model(sales_workbook):
    client = FakeClient([assistant(content="ok")])
    ExcelAgent(client=client, deployment="test").run(
        sales_workbook,
        "and now?",
        history=[{"role": "user", "content": "earlier"},
                 {"role": "assistant", "content": "earlier reply"}],
    )
    contents = [m["content"] for m in client.calls[0]["messages"]]
    assert "earlier" in contents and "earlier reply" in contents
