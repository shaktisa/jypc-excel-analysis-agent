"""End-to-end smoke test against a deployed instance.

Usage:  python scripts/smoke_test.py https://<host>
Exits non-zero on the first failure so CI can gate on it.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
USER = f"smoke-{uuid.uuid4().hex[:8]}"
failures: list[str] = []


def call(method: str, path: str, body=None, headers=None, raw=False):
    url = f"{BASE}{path}"
    hdrs = {"X-User-Id": USER, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = resp.read()
        return resp.status, (payload if raw else json.loads(payload or b"{}"))


def multipart(path: str, filename: str, content: bytes):
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    body.write(b"Content-Type: application/octet-stream\r\n\r\n")
    body.write(content)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body.getvalue(),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-User-Id": USER,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.status, json.loads(resp.read())


def check(label: str, fn):
    try:
        result = fn()
        print(f"  PASS  {label}")
        return result
    except Exception as exc:  # noqa: BLE001 - smoke test reports everything
        detail = exc
        if isinstance(exc, urllib.error.HTTPError):
            detail = f"{exc.code} {exc.read()[:400]!r}"
        print(f"  FAIL  {label}: {detail}")
        failures.append(label)
        return None


def sample_csv() -> bytes:
    rows = [
        "Region,Quarter,Units,Revenue",
        "North,Q1,120,24000",
        "North,Q2,150,31000",
        "South,Q1,90,17500",
        "South,Q2,175,36250",
        "East,Q1,60,11000",
        "East,Q2,210,44100",
    ]
    return ("\n".join(rows) + "\n").encode()


print(f"Smoke testing {BASE} as {USER}")

check("health", lambda: call("GET", "/api/health"))
check("SPA served", lambda: call("GET", "/", raw=True))
check("tools listed", lambda: call("GET", "/api/tools"))

upload = check("upload workbook", lambda: multipart("/api/workbooks", "sales.csv", sample_csv()))
if not upload:
    print("\nUpload failed - cannot continue.")
    sys.exit(1)

file_id = upload[1]["file_id"]
print(f"  file_id = {file_id}")

check("preview", lambda: call("GET", f"/api/workbooks/{file_id}/preview"))
check("analysis", lambda: call("GET", f"/api/workbooks/{file_id}/analysis"))

check(
    "chart generation",
    lambda: call(
        "POST",
        f"/api/workbooks/{file_id}/charts",
        {
            "chart_type": "bar",
            "data_range": "C1:D7",
            "categories_range": "A2:A7",
            "title": "Revenue by region",
        },
    ),
)
check("download", lambda: call("GET", f"/api/workbooks/{file_id}/download", raw=True))
check("version history", lambda: call("GET", f"/api/workbooks/{file_id}"))

chat = check(
    "chat (live LLM + tool calls)",
    lambda: call(
        "POST",
        "/api/chat",
        {
            "file_id": file_id,
            "message": "What is the total Revenue, and which region earned the most?",
        },
    ),
)
if chat:
    reply = chat[1]
    print(f"        tools used: {reply.get('tool_calls')}")
    print(f"        reply: {str(reply.get('reply'))[:300]}")

check("metrics", lambda: call("GET", "/api/metrics"))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("All smoke checks passed.")
