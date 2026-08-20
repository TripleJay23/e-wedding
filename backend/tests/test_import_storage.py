"""Private import-evidence storage path and atomic-write tests."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.imports import storage as storage_module
from app.imports.storage import ImportEvidenceStorage


def test_storage_uses_only_server_controlled_identity_and_digest(tmp_path: Path) -> None:
    storage = ImportEvidenceStorage(tmp_path / "private" / "import-evidence")
    event_id = uuid4()
    batch_id = uuid4()
    digest = "a" * 64

    key = storage.store(event_id, batch_id, digest, b"synthetic private bytes")

    assert key == f"{event_id}/{batch_id}/{digest}.xlsx"
    target = tmp_path / "private" / "import-evidence" / key
    assert target.read_bytes() == b"synthetic private bytes"
    storage.delete(key)
    assert not target.exists()


def test_storage_rejects_traversal_keys(tmp_path: Path) -> None:
    storage = ImportEvidenceStorage(tmp_path / "private" / "import-evidence")
    with pytest.raises(ValueError, match="escaped"):
        storage.delete("../../outside.xlsx")


def test_failed_atomic_replace_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private" / "import-evidence"
    storage = ImportEvidenceStorage(root)
    event_id = uuid4()
    batch_id = uuid4()

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(storage_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        storage.store(event_id, batch_id, "b" * 64, b"synthetic")
    assert list(root.rglob("*.tmp")) == []
