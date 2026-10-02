"""Application entrypoint.

Serves the JSON API under ``/api`` and, in the container image, the built React SPA
from ``/``. Frontend and backend remain separate codebases and separate build steps;
they are only co-hosted so the demo has a single public origin and no CORS surface.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import telemetry
from .config import get_settings
from .excel_tools import ExcelToolError
from .routers import chat, files, system

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("excel_agent")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.configure()
    settings = get_settings()
    logger.info(
        "Excel Analysis Agent starting | env=%s offline=%s storage=%s",
        settings.env_label,
        settings.offline,
        settings.storage_account,
    )
    yield


app = FastAPI(
    title="Excel Analysis Agent",
    version="0.1.0",
    description="Upload a spreadsheet, get an AI analysis and generate charts.",
    lifespan=lifespan,
)

# The SPA is served from the same origin in production. Allowed origins are
# configurable so the frontend can also run standalone on Vite's dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ExcelToolError)
async def excel_tool_error_handler(_: Request, exc: ExcelToolError) -> JSONResponse:
    """Spreadsheet problems are user-correctable: 400, not 500."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(system.router)
app.include_router(files.router)
app.include_router(chat.router)


if STATIC_DIR.exists():  # present only in the built container image
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def spa_root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str) -> FileResponse:
        """Client-side routing fallback; /api/* is matched by the routers above."""
        candidate = STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
