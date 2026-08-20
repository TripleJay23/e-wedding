"""Private server-controlled storage for original import evidence."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID


class ImportEvidenceStorage:
    """Persist uploads under UUID/digest-derived paths and expose only storage keys."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def store(self, event_id: UUID, batch_id: UUID, digest: str, content: bytes) -> str:
        """Atomically write one private XLSX and return a non-filesystem storage key."""
        storage_key = f"{event_id}/{batch_id}/{digest}.xlsx"
        target = self._resolve_key(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".xlsx.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return storage_key

    def delete(self, storage_key: str) -> None:
        """Remove evidence only when its corresponding DB transaction failed."""
        self._resolve_key(storage_key).unlink(missing_ok=True)

    def _resolve_key(self, storage_key: str) -> Path:
        target = (self._root / storage_key).resolve()
        if self._root != target and self._root not in target.parents:
            raise ValueError("import storage key escaped the protected root")
        return target
