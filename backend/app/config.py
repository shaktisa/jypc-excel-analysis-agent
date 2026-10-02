"""Central configuration. Everything is env-driven; there are no secrets in code.

Azure access uses DefaultAzureCredential (user-assigned managed identity in Azure,
developer identity locally), so no connection strings or API keys are ever needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Azure OpenAI (keyless, via Entra token) ---
    openai_endpoint: str = os.getenv(
        "AZURE_OPENAI_ENDPOINT", "https://commercial-growth-openai.openai.azure.com/"
    )
    openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "commercial-growth-gpt-4.1")
    openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    # --- Blob storage (keyless, via managed identity) ---
    storage_account: str = os.getenv("AZURE_STORAGE_ACCOUNT", "copilotanalytixsa")
    blob_container: str = os.getenv("AZURE_BLOB_CONTAINER", "excel-agent")

    # --- Managed identity ---
    managed_identity_client_id: str | None = os.getenv("AZURE_CLIENT_ID") or None

    # --- Behaviour ---
    local_storage_dir: str = os.getenv("LOCAL_STORAGE_DIR", "./.data")
    # When true the app never talks to Azure: files go to disk and the agent is
    # unavailable. Used by unit tests and offline local development.
    offline: bool = _flag("EXCEL_AGENT_OFFLINE")
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "8"))
    env_label: str = os.getenv("ENV_LABEL", "LOCAL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
