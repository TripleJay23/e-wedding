"""Exact private V10 recovery, generic rendering, and QR geometry verification."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

from app.cards import package as package_module
from app.cards import renderer as renderer_module
from app.cards.package import (
    APPROVED_PREVIEW_SHA256,
    CANVAS_SIZE,
    EXPECTED_LOGICAL_INDEX,
    LOGICAL_PACKAGE_SHA256,
    MANIFEST_SHA256,
    RAW_PACKAGE_SHA256,
    TemplatePackageError,
    VerifiedTemplateStorage,
)
from app.cards.preview import PREVIEW_QR_SENTINEL, render_preview_qr_png
from app.cards.renderer import CardRenderError, ManifestRenderer
from app.qr.images import render_qr_png

PRIVATE_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "storage" / "private" / "templates"
PRIVATE_PACKAGE = (
    PRIVATE_TEMPLATE_ROOT / "wedding_floral" / "v10" / "wedding_floral_v10.template-package.zip"
)
PRIVATE_PREVIEW = (
    PRIVATE_TEMPLATE_ROOT / "wedding_floral" / "v10" / "wedding_floral_v10_default_preview.png"
)

pytestmark = pytest.mark.skipif(
    not PRIVATE_PACKAGE.is_file() or not PRIVATE_PREVIEW.is_file(),
    reason="exact ignored Wedding Floral V10 recovery is not present",
)


def _photo_bytes(size: tuple[int, int] = (900, 700), color: str = "#b8c7a2") -> bytes:
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _placeholders(qr_payload: str = "A" * 32) -> dict[str, object]:
    event_datetime = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)
    return {
        "event.address": "123 SYNTHETIC ROAD",
        "event.couple_names": ["JORDAN", "JESSICA"],
        "event.date": event_datetime,
        "event.primary_photo": _photo_bytes(),
        "event.time": event_datetime,
        "event.venue": "SYNTHETIC HALL",
        "guest.full_name": "SYNTHETIC GUEST",
        "system.qr_image": render_qr_png(qr_payload),
    }


def test_exact_recovered_package_preview_and_all_logical_entries_verify() -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()

    assert hashlib.sha256(PRIVATE_PACKAGE.read_bytes()).hexdigest() == RAW_PACKAGE_SHA256
    assert hashlib.sha256(PRIVATE_PREVIEW.read_bytes()).hexdigest() == APPROVED_PREVIEW_SHA256
    assert package.raw_package_sha256 == RAW_PACKAGE_SHA256
    assert package.logical_package_sha256 == LOGICAL_PACKAGE_SHA256
    assert package.manifest_sha256 == MANIFEST_SHA256
    assert package.approved_preview_sha256 == APPROVED_PREVIEW_SHA256
    assert set(package.resources) == set(EXPECTED_LOGICAL_INDEX)
    assert len(package.manifest["assets"]) == 6
    assert len(package.manifest["fonts"]) == 2
    assert package.manifest["template_key"] == "wedding_floral"
    assert package.manifest["template_version"] == 10
    assert (package.manifest["canvas"]["width"], package.manifest["canvas"]["height"]) == (
        1240,
        1748,
    )
    for package_path, (size, digest) in EXPECTED_LOGICAL_INDEX.items():
        content = package.resources[package_path]
        assert len(content) == size
        assert hashlib.sha256(content).hexdigest() == digest


def test_package_loader_is_cached_and_fails_closed_on_tampering(tmp_path: Path) -> None:
    storage = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT)
    assert storage.load() is storage.load()

    recovered = tmp_path / "templates" / "wedding_floral" / "v10"
    recovered.mkdir(parents=True)
    archive = bytearray(PRIVATE_PACKAGE.read_bytes())
    archive[-1] ^= 1
    (recovered / "wedding_floral_v10.template-package.zip").write_bytes(archive)
    (recovered / "wedding_floral_v10_default_preview.png").write_bytes(PRIVATE_PREVIEW.read_bytes())
    with pytest.raises(TemplatePackageError, match="raw package failed"):
        VerifiedTemplateStorage(tmp_path / "templates").load()


def test_generic_renderer_is_deterministic_and_preserves_real_qr_geometry() -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()
    renderer = ManifestRenderer()
    payload = "A" * 32

    first = renderer.render(
        package,
        _placeholders(payload),
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )
    second = renderer.render(
        package,
        _placeholders(payload),
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )

    assert first == second
    with Image.open(BytesIO(first)) as card:
        assert card.format == "PNG"
        assert card.size == CANVAS_SIZE
        assert card.info == {}
        qr_crop = card.crop((80, 87, 278, 285))
        decoded = zxingcpp.read_barcodes(qr_crop)
        assert [result.text for result in decoded] == [payload]
        assert qr_crop.size == (198, 198)
        frame_crop = card.convert("RGB").crop((63, 70, 295, 302))
        assert (149, 161, 122) in set(frame_crop.get_flattened_data())
    assert PREVIEW_QR_SENTINEL.encode() not in first


def test_generic_renderer_reuses_immutable_static_layers_within_a_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()
    renderer = ManifestRenderer()
    original_open = renderer_module._open_rgba
    static_opens = 0

    def count_static_opens(content: bytes, label: str) -> Image.Image:
        nonlocal static_opens
        if label == "verified raster asset":
            static_opens += 1
        return original_open(content, label)

    monkeypatch.setattr(renderer_module, "_open_rgba", count_static_opens)
    renderer.render(
        package,
        _placeholders(),
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )
    first_render_opens = static_opens
    renderer.render(
        package,
        _placeholders(),
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )

    assert first_render_opens > 0
    assert static_opens == first_render_opens


@pytest.mark.parametrize(
    ("size", "fit", "focus_x", "focus_y", "zoom"),
    [
        ((500, 900), "COVER", 0.0, 0.0, 1.0),
        ((900, 500), "COVER", 1.0, 1.0, 2.0),
        ((700, 700), "CONTAIN", 0.5, 0.5, 1.0),
    ],
)
def test_generic_photo_framing_supports_portrait_landscape_and_square(
    size: tuple[int, int],
    fit: str,
    focus_x: float,
    focus_y: float,
    zoom: float,
) -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()
    placeholders = _placeholders()
    placeholders["event.primary_photo"] = _photo_bytes(size, "#4f6f52")
    rendered = ManifestRenderer().render(
        package,
        placeholders,
        photo_fit=fit,
        photo_focus_x=focus_x,
        photo_focus_y=focus_y,
        photo_zoom=zoom,
    )
    with Image.open(BytesIO(rendered)) as card:
        assert card.size == CANVAS_SIZE


def test_preview_qr_is_decodable_but_never_required_for_permanent_render() -> None:
    preview = render_preview_qr_png()
    with Image.open(BytesIO(preview)) as image:
        assert [result.text for result in zxingcpp.read_barcodes(image)] == [PREVIEW_QR_SENTINEL]


@pytest.mark.parametrize(
    ("fit", "focus_x", "focus_y", "zoom", "message"),
    [
        ("STRETCH", 0.5, 0.5, 1.0, "photo fit"),
        ("COVER", -0.1, 0.5, 1.0, "photo focus"),
        ("COVER", 0.5, 0.5, 3.1, "photo zoom"),
        ("CONTAIN", 0.5, 0.5, 1.1, "CONTAIN"),
    ],
)
def test_renderer_rejects_controls_outside_manifest_bounds(
    fit: str,
    focus_x: float,
    focus_y: float,
    zoom: float,
    message: str,
) -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()
    with pytest.raises(CardRenderError, match=message):
        ManifestRenderer().render(
            package,
            _placeholders(),
            photo_fit=fit,
            photo_focus_x=focus_x,
            photo_focus_y=focus_y,
            photo_zoom=zoom,
        )


def test_manifest_validator_rejects_every_closed_contract_boundary() -> None:
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()

    def manifest() -> dict[str, object]:
        return json.loads(json.dumps(dict(package.manifest)))

    invalid_roots: list[tuple[object, str]] = [
        ([], "root"),
        ({**manifest(), "schema_version": 2}, "schema version"),
        ({**manifest(), "template_key": "other"}, "identity"),
        ({**manifest(), "canvas": {}}, "canvas"),
        ({**manifest(), "capabilities": []}, "capabilities"),
        ({**manifest(), "assets": {}}, "asset inventory"),
        ({**manifest(), "fonts": {}}, "font inventory"),
        ({**manifest(), "placeholder_contract": {}}, "placeholder contract"),
        ({**manifest(), "placeholder_contract": []}, "not closed"),
        ({**manifest(), "layers": []}, "generic layers"),
    ]
    for candidate, message in invalid_roots:
        with pytest.raises(TemplatePackageError, match=message):
            package_module._validate_manifest(candidate)

    for field, value, message in (
        ("declaration", "invalid", "declaration"),
        ("package_path", "outside.bin", "logical index"),
        ("byte_size", -1, "invalid size"),
        ("content_sha256", "0" * 64, "invalid digest"),
    ):
        candidate = manifest()
        assets = candidate["assets"]
        assert isinstance(assets, dict)
        key = next(iter(assets))
        declaration = assets[key]
        if field == "declaration":
            assets[key] = value
        else:
            assert isinstance(declaration, dict)
            declaration[field] = value
        with pytest.raises(TemplatePackageError, match=message):
            package_module._validate_manifest(candidate)

    for field, value, message in (
        ("type", "UNKNOWN", "unsupported layer"),
        ("id", "duplicate", "IDs"),
        ("z_index", 0, "z-indexes"),
        ("frame", {"x": -1, "y": 0, "width": 1, "height": 1}, "outside"),
    ):
        candidate = manifest()
        layers = candidate["layers"]
        assert isinstance(layers, list)
        if field == "id":
            assert isinstance(layers[0], dict)
            assert isinstance(layers[1], dict)
            layers[0]["id"] = value
            layers[1]["id"] = value
        elif field == "z_index":
            assert isinstance(layers[0], dict)
            assert isinstance(layers[1], dict)
            layers[0]["z_index"] = value
            layers[1]["z_index"] = value
        else:
            assert isinstance(layers[0], dict)
            layers[0][field] = value
        with pytest.raises(TemplatePackageError, match=message):
            package_module._validate_manifest(candidate)

    with pytest.raises(TemplatePackageError, match="undeclared"):
        package.resource("missing.bin")
    assert not package_module._safe_package_path("")
    assert not package_module._safe_package_path("../outside")
    assert not package_module._valid_frame([], CANVAS_SIZE)
    assert not package_module._valid_frame({"x": 0, "y": 0, "width": "1", "height": 1}, CANVAS_SIZE)
    with pytest.raises(TemplatePackageError, match="readable PNG"):
        package_module._verify_preview(_photo_bytes((10, 10)))
    with pytest.raises(TemplatePackageError, match="readable PNG"):
        package_module._verify_preview(b"not-an-image")


def test_renderer_helpers_fail_closed_on_invalid_generic_manifest_values() -> None:
    moment = datetime(2027, 6, 12, 14, 30, tzinfo=UTC)
    assert renderer_module._apply_formatter("guest", {"type": "UPPERCASE"}) == "GUEST"
    assert (
        renderer_module._apply_formatter(
            ["JORDAN", "JESSICA"], {"type": "JOIN_NAMES", "separator": "AMPERSAND"}
        )
        == "JORDAN & JESSICA"
    )
    assert (
        renderer_module._apply_formatter(
            moment, {"type": "DATE_FORMAT", "format": "DATE_DAY_OF_MONTH"}
        )
        == "12"
    )
    assert (
        renderer_module._apply_formatter(
            moment, {"type": "TIME_FORMAT", "format": "TIME_12H_COMPACT_UPPER"}
        )
        == "2:30 PM"
    )

    invalid_formatters = [
        (1, {"type": "UPPERCASE"}, "UPPERCASE"),
        ("names", {"type": "JOIN_NAMES", "separator": "AMPERSAND"}, "JOIN_NAMES"),
        (["A"], {"type": "JOIN_NAMES", "separator": "COMMA"}, "separator"),
        (["A", ""], {"type": "JOIN_NAMES", "separator": "AMPERSAND"}, "invalid"),
        ("date", {"type": "DATE_FORMAT", "format": "DATE_DAY_OF_MONTH"}, "datetime"),
        (moment, {"type": "DATE_FORMAT", "format": "UNKNOWN"}, "variant"),
        ("time", {"type": "TIME_FORMAT", "format": "TIME_12H_COMPACT_UPPER"}, "datetime"),
        (moment, {"type": "TIME_FORMAT", "format": "UNKNOWN"}, "variant"),
        ("value", {"type": "UNKNOWN"}, "unsupported"),
    ]
    for value, formatter, message in invalid_formatters:
        with pytest.raises(CardRenderError, match=message):
            renderer_module._apply_formatter(value, formatter)

    with pytest.raises(CardRenderError, match="content"):
        renderer_module._resolve_text_runs({}, {}, "#000000")
    with pytest.raises(CardRenderError, match="missing"):
        renderer_module._resolve_text_runs(
            [{"type": "PLACEHOLDER", "key": "missing"}], {}, "#000000"
        )
    with pytest.raises(CardRenderError, match="formatters"):
        renderer_module._resolve_text_runs(
            [{"type": "PLACEHOLDER", "key": "key", "formatters": {}}],
            {"key": "value"},
            "#000000",
        )
    with pytest.raises(CardRenderError, match="run type"):
        renderer_module._resolve_text_runs([{"type": "UNKNOWN"}], {}, "#000000")
    with pytest.raises(CardRenderError, match="did not produce text"):
        renderer_module._resolve_text_runs([{"type": "LITERAL", "text": 1}], {}, "#000000")

    with pytest.raises(CardRenderError, match="manifest value"):
        renderer_module._object([], "value")
    with pytest.raises(CardRenderError, match="frame"):
        renderer_module._frame({"frame": {"x": 0}})
    with pytest.raises(CardRenderError, match="color"):
        renderer_module._color(1)
    with pytest.raises(CardRenderError, match="color"):
        renderer_module._color("not-a-color")
    with pytest.raises(CardRenderError, match="readable bounded image"):
        renderer_module._open_rgba(b"not-an-image", "test image")
    with pytest.raises(CardRenderError, match="asset fit"):
        renderer_module._fit_image(Image.new("RGBA", (2, 2)), (1, 1), "UNKNOWN")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<!DOCTYPE svg><svg viewBox='0 0 1 1'/>", "safe subset"),
        (b"<svg", "invalid"),
        (b"<rect/>", "root"),
        (b"<svg viewBox='0 0 1'/>", "viewBox"),
        (b"<svg viewBox='0 0 0 1'/>", "viewBox"),
        (b"<svg viewBox='0 0 1 1'><circle/></svg>", "element"),
    ],
)
def test_svg_subset_rejects_unsafe_or_unsupported_content(content: bytes, message: str) -> None:
    with pytest.raises((CardRenderError, TemplatePackageError), match=message):
        renderer_module._rasterize_svg(content, (10, 10))
