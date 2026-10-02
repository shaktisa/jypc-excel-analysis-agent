# Excel Analysis Agent

**Live application: https://jypc-excel-analysis-agent.azurewebsites.net**

An agentic web application that lets you upload an Excel workbook, explore it, generate charts, and
ask natural-language questions about your data. The LLM never guesses at numbers — it can only reach
your spreadsheet through a registry of deterministic, unit-tested tools, and every answer it gives is
backed by a real computation over real cells.

---

## 1. What it does

| Capability | How it works |
|---|---|
| **Upload** | `.xlsx` / `.xlsm` / `.csv` / `.tsv` are normalised to a single `.xlsx` representation and stored as an immutable version in Blob Storage. |
| **Preview** | Paginated grid of any sheet, with the detected header row. |
| **Analysis** | Per-column profiling: inferred type, null count, distinct count, min/max/mean for numerics, top values for categoricals. |
| **Charts** | Native Excel charts written into a new workbook version, plus a PNG rendered from the *same* cell range so the UI preview and the workbook can never disagree. |
| **Chat** | A tool-calling agent that answers questions by actually reading ranges and computing aggregates. Each reply reports exactly which tools ran. |
| **Version history** | Every mutation writes a new version; nothing is ever overwritten. Any version can be downloaded. |

### Example

> *"What is the total Revenue, and which region earned the most?"*

```
tools used: list_sheets → read_range(A1:D7) → compute_aggregate(D2:D7, sum)
            → compute_aggregate(D2:D3) → compute_aggregate(D4:D5) → compute_aggregate(D6:D7)

- Total Revenue: 163,850
- Region with highest Revenue: East (55,100)
```

The model did not do the arithmetic. `pandas` did, and the model reported it.

---

## 2. Architecture

```
                 ┌──────────────────────────────┐
  Browser  ────► │  React + Vite SPA            │   static build, served by the API
                 └──────────────┬───────────────┘
                                │  /api/*
                 ┌──────────────▼───────────────┐
                 │  FastAPI (Python 3.12)       │
                 │                              │
                 │  routers/  files, chat,      │
                 │            system            │
                 │  agent.py  tool registry +   │
                 │            tool-call loop    │
                 │  excel_tools.py  openpyxl /  │
                 │            pandas engine     │ ◄── deterministic, 92 unit tests
                 │  charts.py  Excel + PNG      │
                 │  storage.py immutable blobs  │
                 │  telemetry.py counters +     │
                 │            App Insights      │
                 └───┬─────────────┬────────────┘
                     │             │
     managed identity│             │managed identity
                     ▼             ▼
       ┌─────────────────┐   ┌──────────────────────┐
       │ Azure Blob      │   │ Azure OpenAI         │
       │ Storage         │   │ (gpt-4.1, keyless)   │
       └─────────────────┘   └──────────────────────┘
```

Hosted on **Azure App Service for Linux Containers** in the PLG Hackathon subscription, pulling its
image from Azure Container Registry.

### Why this shape

**The LLM is a planner, not a calculator.** `excel_tools.py` is a plain, synchronous Python module
with no awareness of the model. It is exercised directly by unit tests. The agent can only invoke it
through `REGISTRY` in `agent.py`, which validates arguments before anything touches a workbook. This
is what makes the system testable: correctness lives in code that does not depend on a model's mood.

**Tiered tools.** Every tool declares a tier:

- **P0 (shipped, enabled):** `list_sheets`, `read_range`, `describe_sheet`, `compute_aggregate`, `create_chart`
- **P1 (implemented, gated off):** `write_cells`, `set_formula`, `fill_formula`, `format_cells`, `conditional_format`, `add_sheet`, `sort_range`, `autofit_columns`

P1 tools are fully written and unit-tested but are excluded from the model's tool list unless
`ENABLE_WRITE_TOOLS=true`. Read-only analysis was proven end to end before any write path was
exposed to a model, and the deployed instance runs read-only.

**`compute_aggregate` exists because `openpyxl` cannot evaluate formulas.** It reads values with
pandas so the agent gets real numbers rather than formula strings.

**Immutable versioning.** `storage.py` writes `{file_id}/v{n}.xlsx` and never mutates a blob. A
chart or a write produces a new version, so a bad agent action is always recoverable.

**No secrets anywhere.** There is no connection string, no API key, and no client secret in the
repository, the container, or App Service configuration. The app authenticates to Blob Storage and
Azure OpenAI with a **user-assigned managed identity** via `DefaultAzureCredential`; CI/CD
authenticates to Azure with **GitHub OIDC federated credentials**. Azure OpenAI has
`disableLocalAuth=true`, so key-based access is not even possible.

---

## 3. Running it locally

**Prerequisites:** Python 3.12, Node 20, and `az login` (for managed-identity fallback to your own
developer credentials).

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q          # 92 tests
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm ci
npm run dev                                      # proxies /api to :8000
```

To run without any Azure **storage** dependency, set `EXCEL_AGENT_OFFLINE=true`: workbooks are kept
in a local temp directory, so upload, preview, analysis, charts and version history all work with no
cloud account at all. The chat endpoint still calls Azure OpenAI, which `DefaultAzureCredential`
reaches using your `az login` session (you need the `Cognitive Services OpenAI User` role).

### Configuration

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_API_VERSION` | Azure OpenAI target |
| `AZURE_STORAGE_ACCOUNT` / `AZURE_BLOB_CONTAINER` | Blob storage target |
| `AZURE_CLIENT_ID` | Client ID of the user-assigned managed identity |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Enables telemetry export (optional) |
| `ENABLE_WRITE_TOOLS` | Unlocks the P1 write tier (default `false`) |
| `MAX_AGENT_STEPS` | Caps the agent's tool-calling loop (default `8`) |
| `EXCEL_AGENT_OFFLINE` | Uses local filesystem storage instead of Blob Storage |

---

## 4. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe used by the deployment gate |
| `GET` | `/api/metrics` | In-process counters, success rates, p50/p95 latencies |
| `POST` | `/api/files` | Upload a workbook |
| `GET` | `/api/files` | List the caller's workbooks |
| `GET` | `/api/files/{id}` | Metadata and version history |
| `GET` | `/api/files/{id}/preview` | Paginated sheet preview |
| `GET` | `/api/files/{id}/analysis` | Per-column profiling |
| `POST` | `/api/files/{id}/charts` | Create a chart (new version + PNG) |
| `GET` | `/api/files/{id}/download` | Download a specific version |
| `GET` | `/api/tools` | The tool registry exposed to the model |
| `POST` | `/api/chat` | `{file_id, message, history}` → reply plus the tools that ran |

---

## 5. Testing

- **92 backend unit tests** covering the Excel engine, chart generation, storage versioning, the
  agent's tool dispatch and argument validation, telemetry, and the HTTP API.
- **`ruff`** lint gate.
- **`scripts/smoke_test.py`** — a dependency-free end-to-end test that runs against the *deployed*
  URL: health, SPA delivery, tool registry, upload, preview, analysis, chart generation, download,
  version history, a **live LLM chat turn with real tool calls**, and metrics.

```powershell
python scripts\smoke_test.py https://jypc-excel-analysis-agent.azurewebsites.net
```

All eleven checks pass against the live deployment.

---

## 6. CI/CD

`.github/workflows/ci-cd.yml` defines three jobs:

1. **backend** — `ruff` + `pytest` on every push and pull request.
2. **frontend** — `npm ci` + `npm run build`.
3. **deploy** — `main` only, after both gates pass. Logs in with **OIDC** (no stored credentials),
   builds the image with `az acr build`, points App Service at the new tag, and then polls
   `/api/health` up to 30 times. A deployment that does not become healthy fails the run.

Azure identifiers are supplied as repository **variables** (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`) — none of them are secrets, and no secret is required.

---

## 7. Infrastructure

`infra/provision.ps1` is idempotent and provisions everything from nothing:

- two user-assigned managed identities (one for the app, one for CI/CD)
- the GitHub OIDC federated credential
- role assignments: `AcrPull`, `Storage Blob Data Contributor`, `Cognitive Services OpenAI User` for
  the app; `AcrPush` and `Contributor` for CI/CD
- blob container, App Service plan, and the web app, wired to pull from ACR using managed identity

`infra/telemetry-workbook.json` is an Azure Monitor Workbook (deployed) showing success rate,
distinct users, operation volume, failures, uploads, charts, operations over time, per-operation p50/p95,
agent tool usage, and a failure table — all driven by the custom events emitted from `telemetry.py`.

---

## 8. Production-readiness gaps

Stated plainly, because an honest list is more useful than a pretend-complete one:

- **No authentication.** Easy Auth was deliberately disabled so a reviewer can open the public URL
  without a tenant account. Users are separated only by an `X-User-Id` header, which is trivially
  spoofable. For production this becomes Entra ID sign-in with the user object ID as the partition key.
- **No rate limiting or upload quotas.** A single caller can exhaust the plan.
- **No virus scanning** of uploaded files, and no blob lifecycle policy, so storage grows forever.
- **`/api/metrics` counters are per-replica and in-process.** They reset on restart and would diverge
  if the app scaled out; App Insights is the durable source of truth.
- **Single B1 instance, no autoscale or redundancy.**
- **P1 write tools are disabled.** They are implemented and tested but have not been validated
  end to end against a live model, so they are not exposed.
- **Charts support a focused set of types** rather than the full Excel surface.

---

## 9. AI-generated content disclosure

This project was built with substantial assistance from an AI coding assistant (GitHub Copilot CLI).
The AI generated the majority of the application code, tests, infrastructure script, CI/CD workflow
and this README. All architectural decisions — the deterministic tool-registry design, the P0/P1
tiering, immutable versioning, and the no-secrets managed-identity posture — were directed and
reviewed by me, and every claim in this document was verified against the running system rather than
assumed. The failures found along the way (a role assigned to a client ID instead of a principal ID,
and a telemetry client given a connection string where it expected an instrumentation key) were
diagnosed from real Azure responses and container logs.
