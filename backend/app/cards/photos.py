"""Bounded canonical event-photo validation and metadata stripping."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MIN_PHOTO_SIDE = 256
MAX_PHOTO_SIDE = 12_000
MAX_PHOTO_PIXELS = 40_000_000
SUPPORTED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class EventPhotoValidationError(ValueError):
    """Raised when uploaded image bytes fail the private photo contract."""


@dataclass(frozen=True, slots=True)
class CanonicalEventPhoto:
    """Metadata-free deterministic PNG plus safe technical metadata."""

    content: bytes
    sha256: str
    pixel_width: int
    pixel_height: int


def canonicalize_event_photo(content: bytes, *, maximum_bytes: int) -> CanonicalEventPhoto:
    """Validate one bounded still image, strip metadata, and emit a private PNG."""
    if not content:
        raise EventPhotoValidationError("event photo is empty")
    if len(content) > maximum_bytes:
        raise EventPhotoValidationError("event photo exceeds the configured upload limit")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in SUPPORTED_IMAGE_FORMATS:
                raise EventPhotoValidationError("event photo must be JPEG, PNG, or WebP")
            if getattr(source, "n_frames", 1) != 1:
                raise EventPhotoValidationError("animated event photos are not supported")
            width, height = source.size
            if not _valid_dimensions(width, height):
                raise EventPhotoValidationError("event photo dimensions are outside safe limits")
            source.load()
            transposed = ImageOps.exif_transpose(source)
            canonical = _flatten_to_rgb(transposed)
    except EventPhotoValidationError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as exc:
        raise EventPhotoValidationError("event photo is not a safe readable image") from exc

    output = BytesIO()
    canonical.save(output, format="PNG", optimize=False, compress_level=9)
    png = output.getvalue()
    return CanonicalEventPhoto(
        content=png,
        sha256=hashlib.sha256(png).hexdigest(),
        pixel_width=canonical.width,
        pixel_height=canonical.height,
    )


def _valid_dimensions(width: int, height: int) -> bool:
    return (
        MIN_PHOTO_SIDE <= width <= MAX_PHOTO_SIDE
        and MIN_PHOTO_SIDE <= height <= MAX_PHOTO_SIDE
        and width * height <= MAX_PHOTO_PIXELS
    )


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    return background.convert("RGB")
