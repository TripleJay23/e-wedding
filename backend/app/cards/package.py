"""Strict loading and caching of immutable verified template packages."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from zipfile import BadZipFile, ZipFile

from PIL import Image

from app.cards.name_policy import FIRST_AND_LAST, FIRST_NAME_EACH

TEMPLATE_KEY = "wedding_floral"
TEMPLATE_VERSION = 10
RAW_PACKAGE_SHA256 = "b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782"
LOGICAL_PACKAGE_SHA256 = "75446293b53acf292b8900e61042239c01ad80b6ed66365e30710834ded6eed4"
MANIFEST_SHA256 = "ccf84b1cb02716c1d0b5c9b830203d1399675a41aacedd02b2b52ac917bb01be"
APPROVED_PREVIEW_SHA256 = "1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8"
PACKAGE_STORAGE_KEY = "wedding_floral/v10/wedding_floral_v10.template-package.zip"
PREVIEW_STORAGE_KEY = "wedding_floral/v10/wedding_floral_v10_default_preview.png"
CANVAS_SIZE = (1240, 1748)
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_LOGICAL_BYTES = 4 * 1024 * 1024

EXPECTED_LOGICAL_INDEX: Mapping[str, tuple[int, str]] = MappingProxyType(
    {
        "manifest.json": (13468, MANIFEST_SHA256),
        "assets/bottom_left_floral.png": (
            74231,
            "f1f6e07d53bb34147e29e66847ef9ec75b565b4a27f3fed1fe66b128536e319d",
        ),
        "assets/bottom_right_floral.png": (
            74096,
            "031fde58dff37b2854df2096367f841c221a38ecce60ff494e5d3e436d520ac7",
        ),
        "assets/photo_floral_frame.png": (
            141786,
            "8d13590ccbbe36ae62f0e2108f7f944e0d2e164fb7d32a4f52a39d634d4d139a",
        ),
        "assets/photo_gold_rings.png": (
            134226,
            "3e28304cf1f029d3b74a72df6dd4b4f122a24a61cc47b2549e9d7a861c013ea6",
        ),
        "assets/qr_frame.svg": (
            204,
            "1b96180158dfe49f10532331893f80945a240a444f55d04a87f43597205bc568",
        ),
        "assets/top_right_floral.png": (
            108997,
            "fd494cce736a1fef0da9d20154497818c6618a8f234db34d374c0285b1f1c28d",
        ),
        "fonts/body_font.otf": (
            53116,
            "de1eddf3f49fbc0782a1008a87053207f69174a3860f7068520ee4cd901ef926",
        ),
        "fonts/couple_script.ttf": (
            73996,
            "9706c0bb3f05f576e5fe13abcad1bb5e27c76b0b0da055c94e2a2cd88c51bd5d",
        ),
    }
)

SUPPORTED_CAPABILITIES = frozenset(
    {
        "LINE_V1",
        "PHOTO_CROP_EDIT_V1",
        "PHOTO_FOCUS_EDIT_V1",
        "PHOTO_SCALE_EDIT_V1",
        "PHOTO_V1",
        "QR_V1",
        "SHAPE_V1",
        "STATIC_ASSET_V1",
        "TEXT_FIT_V1",
        "TEXT_V1",
    }
)
SUPPORTED_LAYER_TYPES = frozenset({"LINE", "PHOTO", "QR", "SHAPE", "STATIC_ASSET", "TEXT"})
SUPPORTED_PLACEHOLDERS = frozenset(
    {
        "event.address",
        "event.couple_names",
        "event.date",
        "event.primary_photo",
        "event.time",
        "event.venue",
        "guest.full_name",
        "system.qr_image",
    }
)
V10_DISPLAY_NAME_TRANSFORMATIONS: Mapping[str, str] = MappingProxyType(
    {
        "event.couple_names": FIRST_NAME_EACH,
        "guest.full_name": FIRST_AND_LAST,
    }
)


class TemplatePackageError(ValueError):
    """Raised when private package identity or manifest validation fails closed."""


@dataclass(frozen=True, slots=True)
class VerifiedTemplatePackage:
    """Cached immutable bytes and validated generic manifest."""

    manifest: Mapping[str, Any]
    resources: Mapping[str, bytes]
    display_name_transformations: Mapping[str, str]
    raw_package_sha256: str = RAW_PACKAGE_SHA256
    logical_package_sha256: str = LOGICAL_PACKAGE_SHA256
    manifest_sha256: str = MANIFEST_SHA256
    approved_preview_sha256: str = APPROVED_PREVIEW_SHA256

    def resource(self, package_path: str) -> bytes:
        try:
            return self.resources[package_path]
        except KeyError as exc:
            raise TemplatePackageError(
                "manifest referenced an undeclared package resource"
            ) from exc


class VerifiedTemplateStorage:
    """Resolve only the fixed verified V10 package beneath a protected root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def load(self) -> VerifiedTemplatePackage:
        package_path = self._resolve(PACKAGE_STORAGE_KEY)
        preview_path = self._resolve(PREVIEW_STORAGE_KEY)
        return _load_verified_package(str(package_path), str(preview_path))

    def read_approved_preview(self) -> bytes:
        preview_path = self._resolve(PREVIEW_STORAGE_KEY)
        content = preview_path.read_bytes()
        _verify_digest(content, APPROVED_PREVIEW_SHA256, "approved preview")
        _verify_preview(content)
        return content

    def _resolve(self, storage_key: str) -> Path:
        path = PurePosixPath(storage_key)
        if path.is_absolute() or ".." in path.parts or "\\" in storage_key:
            raise TemplatePackageError("invalid protected template storage key")
        target = (self._root.joinpath(*path.parts)).resolve()
        if self._root != target and self._root not in target.parents:
            raise TemplatePackageError("template storage key escaped the protected root")
        return target


@lru_cache(maxsize=4)
def _load_verified_package(
    package_path_text: str,
    preview_path_text: str,
) -> VerifiedTemplatePackage:
    """Verify once per immutable absolute package/preview identity."""
    package_path = Path(package_path_text)
    preview_path = Path(preview_path_text)
    try:
        archive = package_path.read_bytes()
        preview = preview_path.read_bytes()
    except OSError as exc:
        raise TemplatePackageError("verified Wedding Floral V10 package is unavailable") from exc
    if len(archive) > MAX_PACKAGE_BYTES:
        raise TemplatePackageError("template package exceeds the verified size boundary")
    _verify_digest(archive, RAW_PACKAGE_SHA256, "raw package")
    _verify_digest(preview, APPROVED_PREVIEW_SHA256, "approved preview")
    _verify_preview(preview)

    try:
        with ZipFile(BytesIO(archive)) as package:
            infos = package.infolist()
            names = [info.filename for info in infos]
            if len(infos) != len(EXPECTED_LOGICAL_INDEX) or len(set(names)) != len(names):
                raise TemplatePackageError("template package inventory is not exact")
            if set(names) != set(EXPECTED_LOGICAL_INDEX):
                raise TemplatePackageError("template package contains an unexpected logical entry")
            if any(info.is_dir() or not _safe_package_path(info.filename) for info in infos):
                raise TemplatePackageError("template package contains an unsafe entry")
            if sum(info.file_size for info in infos) > MAX_LOGICAL_BYTES:
                raise TemplatePackageError("template package expands beyond its verified boundary")
            resources: dict[str, bytes] = {}
            for info in infos:
                content = package.read(info)
                expected_size, expected_digest = EXPECTED_LOGICAL_INDEX[info.filename]
                if len(content) != expected_size:
                    raise TemplatePackageError("template logical entry size did not match")
                _verify_digest(content, expected_digest, "template logical entry")
                resources[info.filename] = content
    except (BadZipFile, OSError) as exc:
        raise TemplatePackageError("template package is not a readable ZIP archive") from exc

    try:
        manifest = json.loads(resources["manifest.json"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplatePackageError("template manifest is not valid canonical JSON") from exc
    _validate_manifest(manifest)
    return VerifiedTemplatePackage(
        manifest=MappingProxyType(manifest),
        resources=MappingProxyType(resources),
        display_name_transformations=V10_DISPLAY_NAME_TRANSFORMATIONS,
    )


def _verify_digest(content: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise TemplatePackageError(f"{label} failed its SHA-256 integrity check")


def _safe_package_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _verify_preview(content: bytes) -> None:
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            if image.format != "PNG" or image.size != CANVAS_SIZE:
                raise TemplatePackageError("approved preview has invalid image geometry")
    except (OSError, ValueError) as exc:
        raise TemplatePackageError("approved preview is not a readable PNG") from exc


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict):
        raise TemplatePackageError("template manifest root must be an object")
    if manifest.get("schema_version") != 1:
        raise TemplatePackageError("template manifest schema version is unsupported")
    if manifest.get("template_key") != TEMPLATE_KEY or manifest.get("template_version") != 10:
        raise TemplatePackageError("template identity does not match Wedding Floral V10")
    canvas = manifest.get("canvas")
    if not isinstance(canvas, dict) or (canvas.get("width"), canvas.get("height")) != CANVAS_SIZE:
        raise TemplatePackageError("template canvas does not match the approved design")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or set(capabilities) != SUPPORTED_CAPABILITIES:
        raise TemplatePackageError("template capabilities are unsupported or incomplete")
    assets = manifest.get("assets")
    fonts = manifest.get("fonts")
    if not isinstance(assets, dict) or len(assets) != 6:
        raise TemplatePackageError("template asset inventory is invalid")
    if not isinstance(fonts, dict) or len(fonts) != 2:
        raise TemplatePackageError("template font inventory is invalid")
    for declaration in (*assets.values(), *fonts.values()):
        if not isinstance(declaration, dict):
            raise TemplatePackageError("template resource declaration is invalid")
        package_path = declaration.get("package_path")
        if package_path not in EXPECTED_LOGICAL_INDEX:
            raise TemplatePackageError("template resource declaration escaped the logical index")
        expected_size, expected_digest = EXPECTED_LOGICAL_INDEX[package_path]
        if declaration.get("byte_size") != expected_size:
            raise TemplatePackageError("template resource declaration has an invalid size")
        if declaration.get("content_sha256") != expected_digest:
            raise TemplatePackageError("template resource declaration has an invalid digest")
    placeholder_contract = manifest.get("placeholder_contract")
    if not isinstance(placeholder_contract, list):
        raise TemplatePackageError("template placeholder contract is invalid")
    placeholder_keys = {
        entry.get("key") for entry in placeholder_contract if isinstance(entry, dict)
    }
    if placeholder_keys != SUPPORTED_PLACEHOLDERS:
        raise TemplatePackageError("template placeholder contract is not closed")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise TemplatePackageError("template must declare generic layers")
    seen_ids: set[str] = set()
    seen_z: set[int] = set()
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") not in SUPPORTED_LAYER_TYPES:
            raise TemplatePackageError("template contains an unsupported layer")
        layer_id = layer.get("id")
        z_index = layer.get("z_index")
        frame = layer.get("frame")
        if not isinstance(layer_id, str) or layer_id in seen_ids:
            raise TemplatePackageError("template layer IDs must be unique")
        if not isinstance(z_index, int) or z_index in seen_z:
            raise TemplatePackageError("template layer z-indexes must be unique")
        if not _valid_frame(frame, CANVAS_SIZE):
            raise TemplatePackageError("template layer frame is outside the canvas")
        seen_ids.add(layer_id)
        seen_z.add(z_index)


def _valid_frame(frame: Any, canvas: tuple[int, int]) -> bool:
    if not isinstance(frame, dict):
        return False
    values = tuple(frame.get(key) for key in ("x", "y", "width", "height"))
    if not all(isinstance(value, int) for value in values):
        return False
    x, y, width, height = values
    return (
        width > 0
        and height > 0
        and x >= 0
        and y >= 0
        and x + width <= canvas[0]
        and y + height <= canvas[1]
    )
