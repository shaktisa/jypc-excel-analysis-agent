"""Versioned workbook storage.

Every mutation writes a brand-new immutable version blob, so the full edit history
of a workbook is downloadable and nothing is ever overwritten in place.

Layout::

    <container>/workbooks/<file_id>/meta.json
    <container>/workbooks/<file_id>/v1.xlsx
    <container>/workbooks/<file_id>/v2.xlsx
    ...

Two interchangeable backends implement the same protocol:

* :class:`BlobWorkbookStore`  - Azure Blob Storage via managed identity (production).
* :class:`LocalWorkbookStore` - the local filesystem (unit tests / offline dev).
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class VersionInfo:
    version: int
    created_at: str
    note: str
    size_bytes: int


@dataclass
class WorkbookMeta:
    file_id: str
    filename: str
    created_at: str
    updated_at: str
    latest_version: int
    versions: list[VersionInfo] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(raw: str | bytes) -> WorkbookMeta:
        data = json.loads(raw)
        versions = [VersionInfo(**v) for v in data.pop("versions", [])]
        return WorkbookMeta(versions=versions, **data)


class WorkbookStore(Protocol):
    def list_workbooks(self) -> list[WorkbookMeta]: ...
    def get_meta(self, file_id: str) -> WorkbookMeta: ...
    def read_version(self, file_id: str, version: int | None = None) -> bytes: ...
    def create(self, filename: str, data: bytes, note: str) -> WorkbookMeta: ...
    def add_version(self, file_id: str, data: bytes, note: str) -> WorkbookMeta: ...
    def delete(self, file_id: str) -> None: ...


class WorkbookNotFound(KeyError):
    """Raised when a workbook id or version does not exist."""


# --------------------------------------------------------------------------- #
# Local filesystem backend
# --------------------------------------------------------------------------- #
class LocalWorkbookStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root) / "workbooks"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _dir(self, file_id: str) -> Path:
        return self.root / file_id

    def _meta_path(self, file_id: str) -> Path:
        return self._dir(file_id) / "meta.json"

    def list_workbooks(self) -> list[WorkbookMeta]:
        out: list[WorkbookMeta] = []
        for meta_file in self.root.glob("*/meta.json"):
            out.append(WorkbookMeta.from_json(meta_file.read_text("utf-8")))
        return sorted(out, key=lambda m: m.updated_at, reverse=True)

    def get_meta(self, file_id: str) -> WorkbookMeta:
        path = self._meta_path(file_id)
        if not path.exists():
            raise WorkbookNotFound(file_id)
        return WorkbookMeta.from_json(path.read_text("utf-8"))

    def read_version(self, file_id: str, version: int | None = None) -> bytes:
        meta = self.get_meta(file_id)
        version = version or meta.latest_version
        path = self._dir(file_id) / f"v{version}.xlsx"
        if not path.exists():
            raise WorkbookNotFound(f"{file_id}/v{version}")
        return path.read_bytes()

    def create(self, filename: str, data: bytes, note: str) -> WorkbookMeta:
        file_id = uuid.uuid4().hex[:12]
        self._dir(file_id).mkdir(parents=True, exist_ok=True)
        now = _utcnow()
        meta = WorkbookMeta(
            file_id=file_id,
            filename=filename,
            created_at=now,
            updated_at=now,
            latest_version=0,
            versions=[],
        )
        self._write_version(meta, data, note)
        return meta

    def add_version(self, file_id: str, data: bytes, note: str) -> WorkbookMeta:
        with self._lock:
            meta = self.get_meta(file_id)
            self._write_version(meta, data, note)
            return meta

    def _write_version(self, meta: WorkbookMeta, data: bytes, note: str) -> None:
        version = meta.latest_version + 1
        (self._dir(meta.file_id) / f"v{version}.xlsx").write_bytes(data)
        meta.latest_version = version
        meta.updated_at = _utcnow()
        meta.versions.append(
            VersionInfo(
                version=version, created_at=meta.updated_at, note=note, size_bytes=len(data)
            )
        )
        self._meta_path(meta.file_id).write_text(meta.to_json(), "utf-8")

    def delete(self, file_id: str) -> None:
        shutil.rmtree(self._dir(file_id), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Azure Blob backend (managed identity, no keys)
# --------------------------------------------------------------------------- #
class BlobWorkbookStore:
    def __init__(self, settings: Settings) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import ContainerClient

        credential = DefaultAzureCredential(
            managed_identity_client_id=settings.managed_identity_client_id
        )
        self._container = ContainerClient(
            account_url=f"https://{settings.storage_account}.blob.core.windows.net",
            container_name=settings.blob_container,
            credential=credential,
        )
        try:
            self._container.create_container()
        except Exception:  # already exists / insufficient rights to create
            pass
        self._lock = threading.Lock()

    @staticmethod
    def _meta_blob(file_id: str) -> str:
        return f"workbooks/{file_id}/meta.json"

    @staticmethod
    def _version_blob(file_id: str, version: int) -> str:
        return f"workbooks/{file_id}/v{version}.xlsx"

    def list_workbooks(self) -> list[WorkbookMeta]:
        out: list[WorkbookMeta] = []
        for blob in self._container.list_blobs(name_starts_with="workbooks/"):
            if blob.name.endswith("/meta.json"):
                raw = self._container.download_blob(blob.name).readall()
                out.append(WorkbookMeta.from_json(raw))
        return sorted(out, key=lambda m: m.updated_at, reverse=True)

    def get_meta(self, file_id: str) -> WorkbookMeta:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            raw = self._container.download_blob(self._meta_blob(file_id)).readall()
        except ResourceNotFoundError as exc:
            raise WorkbookNotFound(file_id) from exc
        return WorkbookMeta.from_json(raw)

    def read_version(self, file_id: str, version: int | None = None) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        meta = self.get_meta(file_id)
        version = version or meta.latest_version
        try:
            return self._container.download_blob(self._version_blob(file_id, version)).readall()
        except ResourceNotFoundError as exc:
            raise WorkbookNotFound(f"{file_id}/v{version}") from exc

    def create(self, filename: str, data: bytes, note: str) -> WorkbookMeta:
        file_id = uuid.uuid4().hex[:12]
        now = _utcnow()
        meta = WorkbookMeta(
            file_id=file_id,
            filename=filename,
            created_at=now,
            updated_at=now,
            latest_version=0,
            versions=[],
        )
        self._write_version(meta, data, note)
        return meta

    def add_version(self, file_id: str, data: bytes, note: str) -> WorkbookMeta:
        with self._lock:
            meta = self.get_meta(file_id)
            self._write_version(meta, data, note)
            return meta

    def _write_version(self, meta: WorkbookMeta, data: bytes, note: str) -> None:
        version = meta.latest_version + 1
        self._container.upload_blob(
            self._version_blob(meta.file_id, version), data, overwrite=False
        )
        meta.latest_version = version
        meta.updated_at = _utcnow()
        meta.versions.append(
            VersionInfo(
                version=version, created_at=meta.updated_at, note=note, size_bytes=len(data)
            )
        )
        self._container.upload_blob(
            self._meta_blob(meta.file_id), meta.to_json().encode("utf-8"), overwrite=True
        )

    def delete(self, file_id: str) -> None:
        for blob in self._container.list_blobs(name_starts_with=f"workbooks/{file_id}/"):
            self._container.delete_blob(blob.name)


_store: WorkbookStore | None = None


def get_store() -> WorkbookStore:
    """Return the process-wide store, choosing a backend from configuration."""
    global _store
    if _store is None:
        settings = get_settings()
        if settings.offline:
            _store = LocalWorkbookStore(settings.local_storage_dir)
        else:
            try:
                _store = BlobWorkbookStore(settings)
            except Exception:  # pragma: no cover - fall back so the UI still works
                _store = LocalWorkbookStore(settings.local_storage_dir)
    return _store


def reset_store(store: WorkbookStore | None = None) -> None:
    """Test hook to inject or clear the singleton."""
    global _store
    _store = store
