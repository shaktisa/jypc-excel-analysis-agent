"""Shared FastAPI dependencies."""
from __future__ import annotations

import hashlib

from fastapi import Header, HTTPException, Request

from .config import get_settings
from .storage import WorkbookNotFound, get_store


def current_user(
    request: Request,
    x_ms_client_principal_name: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> str:
    """Resolve a stable caller identity for telemetry and (future) data isolation.

    Order of preference:

    1. ``X-MS-CLIENT-PRINCIPAL-NAME`` - injected by App Service Easy Auth once SSO is
       turned on. This is the production path and cannot be spoofed from the browser
       because the platform strips inbound copies of the header.
    2. ``X-User-Id`` - a browser-generated anonymous id, used for the public demo so
       usage metrics are still meaningful.
    3. A hash of the client address as a last resort.
    """
    if x_ms_client_principal_name:
        return x_ms_client_principal_name
    if x_user_id:
        return f"anon:{x_user_id[:64]}"
    host = request.client.host if request.client else "unknown"
    return "anon:" + hashlib.sha256(host.encode()).hexdigest()[:12]


def load_workbook_bytes(file_id: str, version: int | None = None) -> bytes:
    try:
        return get_store().read_version(file_id, version)
    except WorkbookNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Workbook not found: {exc}") from exc


def get_meta_or_404(file_id: str):
    try:
        return get_store().get_meta(file_id)
    except WorkbookNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Workbook not found: {file_id}") from exc


def max_upload_bytes() -> int:
    return get_settings().max_upload_bytes
