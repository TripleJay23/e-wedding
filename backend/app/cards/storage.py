"""Traversal-safe private PNG storage for M6 photos and generated cards."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import UUID, uuid4

_EVENT_PHOTO_KEY = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}\.png$")
_GENERATED_CARD_KEY = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}\.png$")


class PrivatePngStorage:
    """Atomic server-controlled PNG storage with a closed key grammar."""

    def __init__(self, root: Path, *, key_pattern: re.Pattern[str]) -> None:
        self._root = root.resolve()
        self._key_pattern = key_pattern

    def store(self, storage_key: str, content: bytes) -> None:
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

    def read(self, storage_key: str) -> bytes:
        return self._resolve_key(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        self._resolve_key(storage_key).unlink(missing_ok=True)

    def exists(self, storage_key: str) -> bool:
        return self._resolve_key(storage_key).is_file()

    def _resolve_key(self, storage_key: str) -> Path:
        if self._key_pattern.fullmatch(storage_key) is None:
            raise ValueError("invalid protected PNG storage key")
        target = (self._root / storage_key).resolve()
        if self._root != target and self._root not in target.parents:
            raise ValueError("protected PNG storage key escaped its root")
        return target


class EventPhotoStorage(PrivatePngStorage):
    """Private canonical event photos."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, key_pattern=_EVENT_PHOTO_KEY)

    @staticmethod
    def key(event_id: UUID, photo_id: UUID) -> str:
        return f"{event_id.hex}/{photo_id.hex}.png"


class GeneratedCardStorage(PrivatePngStorage):
    """Private permanent generated guest cards."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, key_pattern=_GENERATED_CARD_KEY)

    @staticmethod
    def key(event_id: UUID, guest_id: UUID, card_id: UUID) -> str:
        return f"{event_id.hex}/{guest_id.hex}/{card_id.hex}.png"
