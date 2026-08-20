"""M6 private storage traversal, canonical photo, and cleanup primitives."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from app.cards.photos import EventPhotoValidationError, canonicalize_event_photo
from app.cards.storage import EventPhotoStorage, GeneratedCardStorage


def _image_bytes(
    *,
    image_format: str = "JPEG",
    size: tuple[int, int] = (640, 480),
) -> bytes:
    image = Image.new("RGB", size, "#95a17a")
    output = BytesIO()
    image.save(output, format=image_format, comment=b"private metadata must be stripped")
    return output.getvalue()


def test_event_photo_is_metadata_free_deterministic_png() -> None:
    source = _image_bytes()
    first = canonicalize_event_photo(source, maximum_bytes=len(source) + 1)
    second = canonicalize_event_photo(source, maximum_bytes=len(source) + 1)

    assert first == second
    assert first.sha256 == hashlib.sha256(first.content).hexdigest()
    assert (first.pixel_width, first.pixel_height) == (640, 480)
    with Image.open(BytesIO(first.content)) as canonical:
        assert canonical.format == "PNG"
        assert canonical.mode == "RGB"
        assert canonical.info == {}


@pytest.mark.parametrize(
    ("content", "maximum", "message"),
    [
        (b"", 100, "empty"),
        (b"x" * 101, 100, "upload limit"),
        (b"not-an-image", 100, "safe readable"),
        (_image_bytes(size=(128, 128)), 1_000_000, "dimensions"),
    ],
)
def test_event_photo_validation_fails_closed(
    content: bytes,
    maximum: int,
    message: str,
) -> None:
    with pytest.raises(EventPhotoValidationError, match=message):
        canonicalize_event_photo(content, maximum_bytes=maximum)


def test_private_photo_and_card_storage_are_atomic_and_traversal_safe(tmp_path: Path) -> None:
    event_id = uuid4()
    guest_id = uuid4()
    photo_id = uuid4()
    card_id = uuid4()
    content = b"synthetic-private-png"
    photo_storage = EventPhotoStorage(tmp_path / "event-photos")
    card_storage = GeneratedCardStorage(tmp_path / "generated-cards")
    photo_key = EventPhotoStorage.key(event_id, photo_id)
    card_key = GeneratedCardStorage.key(event_id, guest_id, card_id)

    photo_storage.store(photo_key, content)
    card_storage.store(card_key, content)
    assert photo_storage.read(photo_key) == content
    assert card_storage.read(card_key) == content
    assert photo_storage.exists(photo_key)
    assert card_storage.exists(card_key)
    assert not list(tmp_path.rglob("*.tmp"))

    for invalid in (
        "../escape.png",
        "C:/escape.png",
        f"{event_id.hex}\\{photo_id.hex}.png",
        "not-a-uuid.png",
    ):
        with pytest.raises(ValueError, match="invalid protected"):
            photo_storage.read(invalid)

    photo_storage.delete(photo_key)
    card_storage.delete(card_key)
    assert not photo_storage.exists(photo_key)
    assert not card_storage.exists(card_key)
