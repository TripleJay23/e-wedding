"""Protected M5 QR storage containment and atomic-write tests."""

from pathlib import Path
from uuid import uuid4

import pytest

from app.qr import storage as storage_module
from app.qr.storage import QrImageStorage


def test_storage_uses_only_server_controlled_uuid_paths(tmp_path: Path) -> None:
    root = tmp_path / "private" / "qr-codes"
    storage = QrImageStorage(root)
    event_id = uuid4()
    guest_id = uuid4()
    credential_id = uuid4()

    key = storage.store(event_id, guest_id, credential_id, b"synthetic png bytes")

    assert key == f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png"
    assert storage.read(key) == b"synthetic png bytes"
    assert storage.exists(key)
    storage.delete(key)
    assert not storage.exists(key)


@pytest.mark.parametrize(
    "key",
    [
        "../../outside.png",
        "guest-name/phone/credential.png",
        "a" * 32 + "/" + "b" * 32 + "/../outside.png",
        "C:/private/qr.png",
    ],
)
def test_storage_rejects_non_server_controlled_and_traversal_keys(
    tmp_path: Path,
    key: str,
) -> None:
    storage = QrImageStorage(tmp_path / "private" / "qr-codes")
    with pytest.raises(ValueError, match="invalid protected"):
        storage.read(key)


def test_failed_atomic_replace_removes_temporary_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private" / "qr-codes"
    storage = QrImageStorage(root)

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("synthetic QR replace failure")

    monkeypatch.setattr(storage_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        storage.store(uuid4(), uuid4(), uuid4(), b"synthetic")
    assert list(root.rglob("*.tmp")) == []
    assert list(root.rglob("*.png")) == []
