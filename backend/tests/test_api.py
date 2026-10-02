"""End-to-end API tests against the real FastAPI app with a local store."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import chat as chat_router


@pytest.fixture
def client(store):
    chat_router.set_agent(None)
    with TestClient(app) as c:
        yield c
    chat_router.set_agent(None)


def _upload(client, sales_bytes, name="sales.xlsx"):
    return client.post(
        "/api/workbooks",
        files={"file": (name, io.BytesIO(sales_bytes), "application/octet-stream")},
        headers={"X-User-Id": "tester"},
    )


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["write_tools_enabled"] is False


def test_tools_endpoint_lists_only_p0(client):
    body = client.get("/api/tools").json()
    assert body["write_tools_enabled"] is False
    assert {t["name"] for t in body["tools"]} == {
        "list_sheets", "read_range", "describe_sheet", "compute_aggregate", "create_chart"
    }


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
def test_upload_returns_metadata_and_structure(client, sales_bytes):
    response = _upload(client, sales_bytes)
    assert response.status_code == 200
    body = response.json()
    assert body["latest_version"] == 1
    assert body["structure"]["sheets"][0]["name"] == "Sales"
    assert body["versions"][0]["note"] == "Initial upload"


def test_upload_csv_is_normalised_to_xlsx(client):
    response = client.post(
        "/api/workbooks",
        files={"file": ("d.csv", io.BytesIO(b"A,B\n1,2\n"), "text/csv")},
    )
    assert response.status_code == 200
    file_id = response.json()["file_id"]
    assert client.get(f"/api/workbooks/{file_id}/preview").json()["values"][1] == [1, 2]


def test_upload_rejects_unsupported_type(client):
    response = client.post(
        "/api/workbooks", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")}
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/api/workbooks", files={"file": ("a.xlsx", io.BytesIO(b""), "application/octet-stream")}
    )
    assert response.status_code == 400


def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr("app.routers.files.max_upload_bytes", lambda: 10)
    response = client.post(
        "/api/workbooks",
        files={"file": ("a.xlsx", io.BytesIO(b"x" * 100), "application/octet-stream")},
    )
    assert response.status_code == 413


# --------------------------------------------------------------------------- #
# Read / analyse
# --------------------------------------------------------------------------- #
def test_list_and_get_workbook(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    assert file_id in [w["file_id"] for w in client.get("/api/workbooks").json()["workbooks"]]
    assert client.get(f"/api/workbooks/{file_id}").json()["filename"] == "sales.xlsx"


def test_preview_returns_grid(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    body = client.get(f"/api/workbooks/{file_id}/preview").json()
    assert body["values"][0] == ["Region", "Quarter", "Revenue", "Units"]
    assert body["sheets"] == ["Sales"]


def test_analysis_profiles_every_sheet(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    body = client.get(f"/api/workbooks/{file_id}/analysis").json()
    revenue = next(
        c for c in body["profiles"][0]["columns"] if c["name"] == "Revenue"
    )
    assert revenue["stats"]["sum"] == 973350
    assert revenue["nulls"] == 1


def test_unknown_workbook_is_404(client):
    assert client.get("/api/workbooks/nope/analysis").status_code == 404


def test_bad_sheet_name_is_400(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    assert client.get(f"/api/workbooks/{file_id}/preview?sheet=Ghost").status_code == 400


# --------------------------------------------------------------------------- #
# Charts + versioning
# --------------------------------------------------------------------------- #
def test_chart_creates_new_version_and_returns_image(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    response = client.post(
        f"/api/workbooks/{file_id}/charts",
        json={
            "chart_type": "column",
            "data_range": "C1:C9",
            "categories_range": "A2:A9",
            "title": "Revenue",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chart_image"].startswith("data:image/png;base64,")
    assert body["workbook"]["latest_version"] == 2
    assert body["workbook"]["versions"][1]["note"] == "Added column chart"


def test_chart_with_bad_range_is_400(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    response = client.post(
        f"/api/workbooks/{file_id}/charts",
        json={"chart_type": "column", "data_range": "oops"},
    )
    assert response.status_code == 400


def test_download_each_version(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    client.post(
        f"/api/workbooks/{file_id}/charts",
        json={"chart_type": "pie", "data_range": "C1:C9", "categories_range": "A2:A9"},
    )
    v1 = client.get(f"/api/workbooks/{file_id}/download?version=1")
    v2 = client.get(f"/api/workbooks/{file_id}/download")
    assert v1.status_code == v2.status_code == 200
    assert v1.content[:2] == b"PK" and v2.content[:2] == b"PK"
    assert v1.content != v2.content, "v2 contains the chart, v1 must not"
    assert 'filename="sales_v2.xlsx"' in v2.headers["content-disposition"]


def test_delete_workbook(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    assert client.delete(f"/api/workbooks/{file_id}").status_code == 200
    assert client.get(f"/api/workbooks/{file_id}").status_code == 404


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
class StubAgent:
    def __init__(self, result):
        self._result = result

    def run(self, wb, question, history=None):
        return self._result


def test_chat_returns_reply_and_tool_trace(client, sales_bytes):
    from app.agent import AgentResult

    chat_router.set_agent(
        StubAgent(AgentResult("Revenue totals 973,350.", [{"tool": "compute_aggregate",
                                                           "arguments": {}, "ok": True}],
                              False, []))
    )
    file_id = _upload(client, sales_bytes).json()["file_id"]
    body = client.post("/api/chat", json={"file_id": file_id, "message": "total?"}).json()
    assert "973,350" in body["reply"]
    assert body["tool_calls"][0]["tool"] == "compute_aggregate"
    assert body["new_version"] is None


def test_chat_persists_a_new_version_when_the_agent_mutates(client, sales_bytes):
    from app.agent import AgentResult

    chat_router.set_agent(StubAgent(AgentResult("Chart added.", [], True, ["data:image/png;base64,x"])))
    file_id = _upload(client, sales_bytes).json()["file_id"]
    body = client.post("/api/chat", json={"file_id": file_id, "message": "chart it"}).json()
    assert body["new_version"] == 2
    assert client.get(f"/api/workbooks/{file_id}").json()["latest_version"] == 2


def test_chat_on_missing_workbook_is_404(client):
    assert client.post("/api/chat", json={"file_id": "nope", "message": "hi"}).status_code == 404


def test_chat_model_failure_degrades_gracefully(client, sales_bytes):
    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("model down")

    chat_router.set_agent(Boom())
    file_id = _upload(client, sales_bytes).json()["file_id"]
    response = client.post("/api/chat", json={"file_id": file_id, "message": "hi"})
    assert response.status_code == 502
    assert "Deterministic analysis" in response.json()["detail"]


def test_chat_rejects_empty_message(client, sales_bytes):
    file_id = _upload(client, sales_bytes).json()["file_id"]
    assert client.post("/api/chat", json={"file_id": file_id, "message": ""}).status_code == 422


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_metrics_track_users_operations_and_success_rate(client, sales_bytes):
    _upload(client, sales_bytes)
    file_id = _upload(client, sales_bytes).json()["file_id"]
    client.get(f"/api/workbooks/{file_id}/analysis", headers={"X-User-Id": "tester"})
    client.post(
        f"/api/workbooks/{file_id}/charts",
        json={"chart_type": "line", "data_range": "C1:C9"},
        headers={"X-User-Id": "second-user"},
    )

    body = client.get("/api/metrics").json()
    assert body["total_users"] >= 2
    assert body["operations"]["WorkbookUploaded"]["success"] == 2
    assert body["operations"]["ChartGenerated"]["success"] == 1
    assert body["overall_success_rate"] == 1.0
    assert body["operations"]["AnalysisRequested"]["p95_ms"] >= 0


def test_metrics_record_failures(client, sales_bytes):
    client.post("/api/workbooks", files={"file": ("a.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    body = client.get("/api/metrics").json()
    assert body["total_failures"] >= 1
    assert body["counters"]["OperationFailed"] >= 1
    assert body["overall_success_rate"] < 1.0
