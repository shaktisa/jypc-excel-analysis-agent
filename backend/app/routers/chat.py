"""Conversational endpoint backed by the tool-calling agent."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import telemetry
from ..agent import ExcelAgent, active_tools, write_tools_enabled
from ..deps import current_user, load_workbook_bytes
from ..excel_tools import load_workbook_from_bytes, workbook_to_bytes
from ..storage import get_store

logger = logging.getLogger("excel_agent.chat")
router = APIRouter(prefix="/api", tags=["chat"])

_agent: ExcelAgent | None = None


def get_agent() -> ExcelAgent:
    global _agent
    if _agent is None:
        _agent = ExcelAgent()
    return _agent


def set_agent(agent: ExcelAgent | None) -> None:
    """Test hook for injecting a stubbed agent."""
    global _agent
    _agent = agent


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    file_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list)


@router.get("/tools")
def list_available_tools() -> dict:
    """Expose the live tool surface so the UI can show what the agent can do."""
    return {
        "write_tools_enabled": write_tools_enabled(),
        "tools": [
            {"name": s.name, "tier": s.tier, "description": s.description, "mutates": s.mutates}
            for s in active_tools().values()
        ],
    }


@router.post("/chat")
def chat(body: ChatRequest = Body(...), user: str = Depends(current_user)) -> dict:
    data = load_workbook_bytes(body.file_id)
    wb = load_workbook_from_bytes(data, "wb.xlsx")

    with telemetry.track_operation(
        telemetry.EVT_CHAT, user_id=user, file_id=body.file_id
    ) as extra:
        try:
            result = get_agent().run(
                wb, body.message, [t.model_dump() for t in body.history][-10:]
            )
        except Exception as exc:
            logger.exception("Agent turn failed")
            raise HTTPException(
                502,
                "The analysis model is unavailable right now. Deterministic analysis and "
                "chart generation still work from the Analysis tab.",
            ) from exc
        extra["tool_calls"] = len(result.tool_calls)

        version = None
        if result.workbook_mutated:
            meta = get_store().add_version(
                body.file_id, workbook_to_bytes(wb), f"Agent: {body.message[:80]}"
            )
            version = meta.latest_version

    return {
        "reply": result.reply,
        "tool_calls": result.tool_calls,
        "charts": result.charts,
        "new_version": version,
    }
