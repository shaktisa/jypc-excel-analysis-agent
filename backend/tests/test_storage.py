"""Tests for versioned workbook storage."""
from __future__ import annotations

import pytest

from app.storage import WorkbookMeta, WorkbookNotFound


def test_create_then_read_latest(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    assert meta.latest_version == 1
    assert store.read_version(meta.file_id) == sales_bytes


def test_each_edit_makes_a_new_immutable_version(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    store.add_version(meta.file_id, b"PK-version-2", "Added chart")
    store.add_version(meta.file_id, b"PK-version-3", "Added second chart")

    refreshed = store.get_meta(meta.file_id)
    assert refreshed.latest_version == 3
    assert [v.version for v in refreshed.versions] == [1, 2, 3]
    assert [v.note for v in refreshed.versions][1] == "Added chart"
    # v1 is untouched - history is immutable and fully downloadable
    assert store.read_version(meta.file_id, 1) == sales_bytes
    assert store.read_version(meta.file_id, 2) == b"PK-version-2"


def test_version_sizes_are_recorded(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    assert store.get_meta(meta.file_id).versions[0].size_bytes == len(sales_bytes)


def test_missing_workbook_raises(store):
    with pytest.raises(WorkbookNotFound):
        store.get_meta("does-not-exist")


def test_missing_version_raises(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    with pytest.raises(WorkbookNotFound):
        store.read_version(meta.file_id, 99)


def test_list_is_newest_first(store, sales_bytes):
    first = store.create("a.xlsx", sales_bytes, "Initial upload")
    second = store.create("b.xlsx", sales_bytes, "Initial upload")
    store.add_version(second.file_id, sales_bytes, "touch")
    ids = [m.file_id for m in store.list_workbooks()]
    assert ids[0] == second.file_id
    assert first.file_id in ids


def test_delete_removes_everything(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    store.delete(meta.file_id)
    with pytest.raises(WorkbookNotFound):
        store.get_meta(meta.file_id)


def test_meta_json_roundtrip(store, sales_bytes):
    meta = store.create("sales.xlsx", sales_bytes, "Initial upload")
    restored = WorkbookMeta.from_json(store.get_meta(meta.file_id).to_json())
    assert restored.file_id == meta.file_id
    assert restored.versions[0].note == "Initial upload"
