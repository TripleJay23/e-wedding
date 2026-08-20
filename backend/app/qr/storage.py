"""Protected server-controlled storage for M5 QR bearer images."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import UUID, uuid4

_STORAGE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}\.png$")


class QrImageStorage:
    """Atomically persist private QR PNGs for the later M6 rendering service."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def store(
        self,
        event_id: UUID,
        guest_id: UUID,
        credential_id: UUID,
        content: bytes,
    ) -> str:
        """Write under UUID-only directories and return a protected storage key."""
        storage_key = f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png"
        target = self._resolve_key(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
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

    def read(self, storage_key: str) -> bytes:
        """Return private image bytes to an authorized internal renderer boundary."""
        return self._resolve_key(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        """Report whether the internal artifact still exists without exposing its path."""
        return self._resolve_key(storage_key).is_file()

    def delete(self, storage_key: str) -> None:
        """Remove a newly written artifact after a failed database transaction."""
        self._resolve_key(storage_key).unlink(missing_ok=True)

    def _resolve_key(self, storage_key: str) -> Path:
        if _STORAGE_KEY_PATTERN.fullmatch(storage_key) is None:
            raise ValueError("invalid protected QR storage key")
        target = (self._root / storage_key).resolve()
        if self._root != target and self._root not in target.parents:
            raise ValueError("QR storage key escaped the protected root")
        return target
