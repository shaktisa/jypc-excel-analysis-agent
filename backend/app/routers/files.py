"""Workbook endpoints: upload, list, preview, analyse, chart, download."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import telemetry
from ..charts import render_chart_png
from ..deps import current_user, get_meta_or_404, load_workbook_bytes, max_upload_bytes
from ..excel_tools import (
    ExcelToolError,
    create_chart,
    describe_sheet,
    list_sheets,
    load_workbook_from_bytes,
    preview,
    workbook_to_bytes,
)
from ..storage import get_store

router = APIRouter(prefix="/api/workbooks", tags=["workbooks"])


class ChartRequest(BaseModel):
    sheet: str | None = None
    chart_type: str = Field(..., description="bar | column | line | pie | area | scatter")
    data_range: str
    categories_range: str | None = None
    title: str | None = None
    anchor: str = "H2"


def _meta_payload(meta) -> dict:
    return {
        "file_id": meta.file_id,
        "filename": meta.filename,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "latest_version": meta.latest_version,
        "versions": [v.__dict__ for v in meta.versions],
    }


@router.post("")
async def upload_workbook(file: UploadFile, user: str = Depends(current_user)) -> dict:
    """Upload a .xlsx/.xlsm/.csv/.tsv file and store it as version 1."""
    data = await file.read()
    limit = max_upload_bytes()
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than the {limit // (1024 * 1024)} MB limit.")
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")

    with telemetry.track_operation(telemetry.EVT_UPLOAD, user_id=user) as extra:
        try:
            wb = load_workbook_from_bytes(data, file.filename or "upload.xlsx")
        except ExcelToolError as exc:
            raise HTTPException(400, str(exc)) from exc
        extra["sheets"] = len(wb.sheetnames)
        # Always normalise to .xlsx so later versions are a single, consistent format.
        normalised = workbook_to_bytes(wb)
        meta = get_store().create(file.filename or "upload.xlsx", normalised, "Initial upload")

    return {**_meta_payload(meta), "structure": list_sheets(wb)}


@router.get("")
def list_workbooks() -> dict:
    return {"workbooks": [_meta_payload(m) for m in get_store().list_workbooks()]}


@router.get("/{file_id}")
def get_workbook(file_id: str) -> dict:
    return _meta_payload(get_meta_or_404(file_id))


@router.get("/{file_id}/preview")
def preview_workbook(
    file_id: str,
    sheet: str | None = None,
    version: int | None = None,
    max_rows: int = Query(default=100, le=200, ge=1),
) -> dict:
    wb = load_workbook_from_bytes(load_workbook_bytes(file_id, version), "wb.xlsx")
    try:
        return preview(wb, sheet, max_rows)
    except ExcelToolError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{file_id}/analysis")
def analyse_workbook(
    file_id: str,
    sheet: str | None = None,
    version: int | None = None,
    user: str = Depends(current_user),
) -> dict:
    """Deterministic profile of the workbook - no LLM involved, always available."""
    wb = load_workbook_from_bytes(load_workbook_bytes(file_id, version), "wb.xlsx")
    with telemetry.track_operation(telemetry.EVT_ANALYSIS, user_id=user, file_id=file_id):
        try:
            structure = list_sheets(wb)
            targets = [sheet] if sheet else wb.sheetnames
            profiles = [describe_sheet(wb, name) for name in targets]
        except ExcelToolError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"file_id": file_id, "structure": structure, "profiles": profiles}


@router.post("/{file_id}/charts")
def add_chart(
    file_id: str, body: ChartRequest = Body(...), user: str = Depends(current_user)
) -> dict:
    """Create a native Excel chart as a NEW version, and return a PNG preview."""
    wb = load_workbook_from_bytes(load_workbook_bytes(file_id), "wb.xlsx")
    with telemetry.track_operation(
        telemetry.EVT_CHART, user_id=user, file_id=file_id, chart_type=body.chart_type
    ):
        try:
            result = create_chart(
                wb,
                body.sheet,
                body.chart_type,
                body.data_range,
                body.categories_range,
                body.title,
                body.anchor,
            )
            image = render_chart_png(
                wb, body.sheet, body.chart_type, body.data_range,
                body.categories_range, body.title,
            )
        except ExcelToolError as exc:
            raise HTTPException(400, str(exc)) from exc
        meta = get_store().add_version(
            file_id, workbook_to_bytes(wb), f"Added {body.chart_type} chart"
        )
    return {"chart": result, "chart_image": image, "workbook": _meta_payload(meta)}


@router.get("/{file_id}/download")
def download_workbook(
    file_id: str, version: int | None = None, user: str = Depends(current_user)
) -> StreamingResponse:
    meta = get_meta_or_404(file_id)
    data = load_workbook_bytes(file_id, version)
    resolved = version or meta.latest_version
    telemetry.track_event(
        telemetry.EVT_DOWNLOAD,
        {"file_id": file_id, "version": resolved, "outcome": "success"},
        user_id=user,
    )
    stem = meta.filename.rsplit(".", 1)[0]
    import io

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}_v{resolved}.xlsx"'},
    )


@router.delete("/{file_id}")
def delete_workbook(file_id: str) -> dict:
    get_meta_or_404(file_id)
    get_store().delete(file_id)
    return {"deleted": file_id}
