"""Runtime-only synthetic package for repository-safe card engine tests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Any

from PIL import Image

from app.cards.name_policy import FIRST_AND_LAST, FIRST_NAME_EACH
from app.cards.package import (
    SUPPORTED_CAPABILITIES,
    SUPPORTED_PLACEHOLDERS,
    VerifiedTemplatePackage,
)
from app.qr.images import render_qr_png


def _png(size: tuple[int, int], color: str) -> bytes:
    image = Image.new("RGBA", size, color)
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _font_bytes() -> bytes:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    font = next((path for path in candidates if path.is_file()), None)
    if font is None:
        raise AssertionError("a CI image font is required for repository-safe renderer tests")
    return font.read_bytes()


def build_synthetic_package() -> VerifiedTemplatePackage:
    """Build a generic package in memory without publishing private design bytes."""
    font = _font_bytes()
    resources = {
        "assets/frame.svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 232 232">'
            b'<rect x="2" y="2" width="228" height="228" rx="12" fill="none" '
            b'stroke="#95A17A" stroke-width="4"/></svg>'
        ),
        "assets/fill.png": _png((32, 24), "#d9e6cf"),
        "assets/contain.png": _png((16, 32), "#eed9b7"),
        "assets/cover.png": _png((48, 16), "#b7cce8"),
        "assets/unused-a.png": _png((2, 2), "#00000000"),
        "assets/unused-b.png": _png((2, 2), "#00000000"),
        "fonts/body.ttf": font,
        "fonts/script.ttf": font,
    }
    assets = {
        "frame": _declaration(resources, "assets/frame.svg", "SVG"),
        "fill": _declaration(resources, "assets/fill.png", "RASTER"),
        "contain": _declaration(resources, "assets/contain.png", "RASTER"),
        "cover": _declaration(resources, "assets/cover.png", "RASTER"),
        "unused_a": _declaration(resources, "assets/unused-a.png", "RASTER"),
        "unused_b": _declaration(resources, "assets/unused-b.png", "RASTER"),
    }
    fonts = {
        "body": _declaration(resources, "fonts/body.ttf", "FONT"),
        "script": _declaration(resources, "fonts/script.ttf", "FONT"),
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "template_key": "synthetic_test_template",
        "template_version": 10,
        "canvas": {
            "width": 1240,
            "height": 1748,
            "background": {"type": "SOLID", "color_hex": "#fffdf7"},
        },
        "capabilities": sorted(SUPPORTED_CAPABILITIES),
        "assets": assets,
        "fonts": fonts,
        "placeholder_contract": [{"key": key} for key in sorted(SUPPORTED_PLACEHOLDERS)],
        "layers": _layers(),
    }
    return VerifiedTemplatePackage(
        manifest=MappingProxyType(manifest),
        resources=MappingProxyType(resources),
        display_name_transformations=MappingProxyType(
            {
                "event.couple_names": FIRST_NAME_EACH,
                "guest.full_name": FIRST_AND_LAST,
            }
        ),
        raw_package_sha256=_synthetic_digest("package"),
        logical_package_sha256=_synthetic_digest("logical-package"),
        manifest_sha256=_synthetic_digest("manifest"),
        approved_preview_sha256=_synthetic_digest("preview"),
    )


def synthetic_placeholders(qr_payload: str = "A" * 32) -> dict[str, object]:
    moment = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)
    return {
        "event.address": "123 SYNTHETIC ROAD",
        "event.couple_names": ["Jordan Example", "Jessica Example"],
        "event.date": moment,
        "event.primary_photo": _png((900, 700), "#4f6f52"),
        "event.time": moment,
        "event.venue": "SYNTHETIC HALL",
        "guest.full_name": "Synthetic Permanent Guest",
        "system.qr_image": render_qr_png(qr_payload),
    }


def synthetic_preview() -> bytes:
    return _png((1240, 1748), "#fffdf7")


def _synthetic_digest(label: str) -> str:
    return hashlib.sha256(f"repository-safe-synthetic-{label}".encode()).hexdigest()


def logical_index(
    package: VerifiedTemplatePackage,
) -> tuple[dict[str, bytes], dict[str, tuple[int, str]]]:
    manifest_bytes = json.dumps(
        dict(package.manifest), sort_keys=True, separators=(",", ":")
    ).encode()
    resources = {"manifest.json": manifest_bytes, **dict(package.resources)}
    index = {
        path: (len(content), hashlib.sha256(content).hexdigest())
        for path, content in resources.items()
    }
    return resources, index


def _declaration(resources: dict[str, bytes], path: str, kind: str) -> dict[str, object]:
    content = resources[path]
    return {
        "package_path": path,
        "byte_size": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "kind": kind,
    }


def _frame(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def _text_layer(
    layer_id: str,
    z_index: int,
    y: int,
    content: list[dict[str, object]],
    *,
    alignment: str = "CENTER",
    letter_spacing: float = 0.0,
) -> dict[str, object]:
    return {
        "id": layer_id,
        "z_index": z_index,
        "type": "TEXT",
        "frame": _frame(320, y, 800, 90),
        "content": content,
        "style": {
            "font_key": "body",
            "font_size": 40,
            "color_hex": "#243026",
            "letter_spacing": letter_spacing,
            "line_height": 1.0,
            "horizontal_alignment": alignment,
            "vertical_alignment": "CENTER",
        },
        "fitting": {
            "minimum_font_size": 12,
            "maximum_font_size": 40,
            "character_limit": 100,
        },
    }


def _layers() -> list[dict[str, object]]:
    literal = lambda text: {"type": "LITERAL", "text": text}  # noqa: E731
    placeholder = lambda key, formatters=None: {  # noqa: E731
        "type": "PLACEHOLDER",
        "key": key,
        "formatters": formatters or [],
    }
    return [
        {
            "id": "qr_frame",
            "z_index": 0,
            "type": "STATIC_ASSET",
            "asset_key": "frame",
            "frame": _frame(63, 70, 232, 232),
            "fit": "FILL",
        },
        {
            "id": "qr",
            "z_index": 1,
            "type": "QR",
            "placeholder_key": "system.qr_image",
            "frame": _frame(80, 87, 198, 198),
            "foreground_color": "#000000",
            "background_color": "#ffffff",
        },
        {
            "id": "photo",
            "z_index": 2,
            "type": "PHOTO",
            "placeholder_key": "event.primary_photo",
            "frame": _frame(390, 80, 420, 280),
            "mask": {"type": "ELLIPSE"},
        },
        {
            "id": "shape",
            "z_index": 3,
            "type": "SHAPE",
            "shape": "ROUNDED_RECTANGLE",
            "corner_radius": 12,
            "fill_hex": "#f0e5cf",
            "frame": _frame(850, 80, 250, 100),
        },
        {
            "id": "line",
            "z_index": 4,
            "type": "LINE",
            "style": "SOLID",
            "thickness": 3,
            "color_hex": "#95A17A",
            "frame": _frame(850, 220, 250, 5),
        },
        {
            "id": "raster_fill",
            "z_index": 5,
            "type": "STATIC_ASSET",
            "asset_key": "fill",
            "frame": _frame(80, 340, 100, 60),
            "fit": "FILL",
            "opacity": 0.75,
            "rotation_degrees": 5,
        },
        {
            "id": "raster_contain",
            "z_index": 6,
            "type": "STATIC_ASSET",
            "asset_key": "contain",
            "frame": _frame(200, 340, 100, 60),
            "fit": "CONTAIN",
        },
        {
            "id": "raster_cover",
            "z_index": 7,
            "type": "STATIC_ASSET",
            "asset_key": "cover",
            "frame": _frame(320, 340, 100, 60),
            "fit": "COVER",
        },
        _text_layer(
            "guest_name",
            8,
            430,
            [
                literal("WELCOME "),
                placeholder("guest.full_name", [{"type": "UPPERCASE"}]),
            ],
            letter_spacing=1.0,
        ),
        _text_layer(
            "couple_names",
            9,
            540,
            [
                placeholder(
                    "event.couple_names",
                    [{"type": "JOIN_NAMES", "separator": "AMPERSAND"}],
                )
            ],
            alignment="LEFT",
        ),
        _text_layer(
            "date_weekday",
            10,
            650,
            [
                placeholder(
                    "event.date",
                    [{"type": "DATE_FORMAT", "format": "DATE_WEEKDAY_LONG_UPPER"}],
                )
            ],
            alignment="RIGHT",
        ),
        _text_layer(
            "date_month",
            11,
            760,
            [
                placeholder(
                    "event.date",
                    [{"type": "DATE_FORMAT", "format": "DATE_MONTH_LONG_UPPER"}],
                )
            ],
        ),
        _text_layer(
            "date_day",
            12,
            870,
            [placeholder("event.date", [{"type": "DATE_FORMAT", "format": "DATE_DAY_OF_MONTH"}])],
        ),
        _text_layer(
            "date_year",
            13,
            980,
            [
                placeholder(
                    "event.date",
                    [{"type": "DATE_FORMAT", "format": "DATE_YEAR_FOUR_DIGIT"}],
                )
            ],
        ),
        _text_layer(
            "event_time",
            14,
            1090,
            [
                placeholder(
                    "event.time",
                    [{"type": "TIME_FORMAT", "format": "TIME_12H_COMPACT_UPPER"}],
                )
            ],
        ),
        _text_layer("event_venue", 15, 1200, [placeholder("event.venue")]),
        _text_layer("event_address", 16, 1310, [placeholder("event.address")]),
    ]
