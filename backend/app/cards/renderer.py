"""Generic deterministic Manifest V1 renderer for permanent PNG cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.etree import ElementTree

from PIL import Image, ImageColor, ImageDraw, ImageFont

from app.cards.name_policy import CardNamePolicyError, apply_display_name_transform
from app.cards.package import TemplatePackageError, VerifiedTemplatePackage

RENDERER_CONTRACT_VERSION = "manifest-v1-pillow-v2"


class CardRenderError(ValueError):
    """Raised when supplied render inputs violate the verified manifest contract."""


@dataclass(frozen=True, slots=True)
class TextLayout:
    """Measured single-line text geometry used by rendering and layout tests."""

    layer_id: str
    text: str
    font_size: int
    frame_bounds: tuple[float, float, float, float]
    ink_bounds: tuple[float, float, float, float]
    line_box_bounds: tuple[float, float, float, float]
    baseline_y: float
    origin_x: float
    advance_width: float


class ManifestRenderer:
    """Render generic layers without branching on a template key or asset name."""

    def __init__(self) -> None:
        self._static_asset_cache: dict[tuple[str, str, tuple[int, int], str], Image.Image] = {}

    def render(
        self,
        package: VerifiedTemplatePackage,
        placeholders: Mapping[str, Any],
        *,
        photo_fit: str,
        photo_focus_x: float,
        photo_focus_y: float,
        photo_zoom: float,
    ) -> bytes:
        manifest = package.manifest
        canvas_data = _object(manifest.get("canvas"), "canvas")
        background = _object(canvas_data.get("background"), "canvas background")
        if background.get("type") != "SOLID":
            raise CardRenderError("only solid Manifest V1 canvas backgrounds are supported")
        canvas = Image.new(
            "RGBA",
            (int(canvas_data["width"]), int(canvas_data["height"])),
            _color(background.get("color_hex")),
        )
        layers = manifest.get("layers")
        if not isinstance(layers, list):
            raise CardRenderError("manifest layers are invalid")
        for raw_layer in sorted(layers, key=lambda layer: layer["z_index"]):
            layer = _object(raw_layer, "layer")
            layer_type = layer.get("type")
            if layer_type == "STATIC_ASSET":
                rendered = self._static_asset(package, manifest, layer)
                _composite(canvas, rendered, layer)
            elif layer_type == "PHOTO":
                rendered = self._photo(
                    layer,
                    placeholders,
                    photo_fit=photo_fit,
                    photo_focus_x=photo_focus_x,
                    photo_focus_y=photo_focus_y,
                    photo_zoom=photo_zoom,
                )
                _composite(canvas, rendered, layer)
            elif layer_type == "QR":
                rendered = self._qr(layer, placeholders)
                _composite(canvas, rendered, layer)
            elif layer_type == "SHAPE":
                self._shape(canvas, layer)
            elif layer_type == "LINE":
                self._line(canvas, layer)
            elif layer_type == "TEXT":
                self._text(canvas, package, manifest, layer, placeholders)
            else:
                raise CardRenderError("manifest contains an unsupported generic layer")
        output = BytesIO()
        canvas.save(output, format="PNG", optimize=False, compress_level=9)
        return output.getvalue()

    def measure_text_layouts(
        self,
        package: VerifiedTemplatePackage,
        placeholders: Mapping[str, Any],
    ) -> Mapping[str, TextLayout]:
        """Measure every generic text layer without rasterizing a card."""
        manifest = package.manifest
        layers = manifest.get("layers")
        if not isinstance(layers, list):
            raise CardRenderError("manifest layers are invalid")
        measured: dict[str, TextLayout] = {}
        for raw_layer in layers:
            layer = _object(raw_layer, "layer")
            if layer.get("type") != "TEXT":
                continue
            _, _, _, layout = self._prepare_text(
                package,
                manifest,
                layer,
                placeholders,
            )
            measured[layout.layer_id] = layout
        return measured

    def _static_asset(
        self,
        package: VerifiedTemplatePackage,
        manifest: Mapping[str, Any],
        layer: Mapping[str, Any],
    ) -> Image.Image:
        assets = _object(manifest.get("assets"), "assets")
        declaration = _object(assets.get(layer.get("asset_key")), "asset declaration")
        package_path = str(declaration.get("package_path"))
        frame = _frame(layer)
        fit = str(layer.get("fit", "FILL"))
        cache_key = (package.logical_package_sha256, package_path, frame[2:], fit)
        cached = self._static_asset_cache.get(cache_key)
        if cached is not None:
            return cached.copy()
        resource = package.resource(package_path)
        kind = declaration.get("kind")
        if kind == "RASTER":
            image = _open_rgba(resource, "verified raster asset")
            rendered = _fit_image(image, frame[2:], fit)
        elif kind == "SVG":
            rendered = _rasterize_svg(resource, frame[2:])
        else:
            raise CardRenderError("manifest asset kind is unsupported")
        self._static_asset_cache[cache_key] = rendered.copy()
        return rendered

    def _photo(
        self,
        layer: Mapping[str, Any],
        placeholders: Mapping[str, Any],
        *,
        photo_fit: str,
        photo_focus_x: float,
        photo_focus_y: float,
        photo_zoom: float,
    ) -> Image.Image:
        placeholder_key = str(layer.get("placeholder_key"))
        content = placeholders.get(placeholder_key)
        if not isinstance(content, bytes):
            raise CardRenderError("photo placeholder must contain canonical image bytes")
        if photo_fit not in {"COVER", "CONTAIN"}:
            raise CardRenderError("photo fit is unsupported")
        if not 0.0 <= photo_focus_x <= 1.0 or not 0.0 <= photo_focus_y <= 1.0:
            raise CardRenderError("photo focus must be within 0..1")
        if not 1.0 <= photo_zoom <= 3.0:
            raise CardRenderError("photo zoom must be within 1..3")
        if photo_fit == "CONTAIN" and photo_zoom != 1.0:
            raise CardRenderError("CONTAIN photo fit requires zoom 1.0")
        image = _open_rgba(content, "canonical event photo")
        _, _, width, height = _frame(layer)
        rendered = _fit_photo(
            image,
            (width, height),
            fit=photo_fit,
            focus_x=photo_focus_x,
            focus_y=photo_focus_y,
            zoom=photo_zoom,
        )
        mask = layer.get("mask")
        if isinstance(mask, Mapping) and mask.get("type") == "ELLIPSE":
            alpha = Image.new("L", (width, height), 0)
            ImageDraw.Draw(alpha).ellipse((0, 0, width - 1, height - 1), fill=255)
            rendered.putalpha(Image.composite(rendered.getchannel("A"), alpha, alpha))
        return rendered

    def _qr(
        self,
        layer: Mapping[str, Any],
        placeholders: Mapping[str, Any],
    ) -> Image.Image:
        placeholder_key = str(layer.get("placeholder_key"))
        content = placeholders.get(placeholder_key)
        if not isinstance(content, bytes):
            raise CardRenderError("QR placeholder must contain verified PNG bytes")
        qr = _open_rgba(content, "protected QR image").convert("L")
        _, _, width, height = _frame(layer)
        if qr.size != (width, height):
            qr = qr.resize((width, height), Image.Resampling.NEAREST)
        foreground = _color(layer.get("foreground_color"))
        background = _color(layer.get("background_color"))
        rendered = Image.new("RGBA", qr.size, background)
        ink = Image.new("RGBA", qr.size, foreground)
        threshold = qr.point(lambda value: 255 if value < 128 else 0)
        rendered.alpha_composite(Image.composite(ink, rendered, threshold))
        return rendered

    def _shape(self, canvas: Image.Image, layer: Mapping[str, Any]) -> None:
        x, y, width, height = _frame(layer)
        draw = ImageDraw.Draw(canvas)
        bounds = (x, y, x + width - 1, y + height - 1)
        fill = _color(layer.get("fill_hex"))
        if layer.get("shape") == "ROUNDED_RECTANGLE":
            draw.rounded_rectangle(bounds, radius=int(layer.get("corner_radius", 0)), fill=fill)
        else:
            raise CardRenderError("manifest shape is unsupported")

    def _line(self, canvas: Image.Image, layer: Mapping[str, Any]) -> None:
        x, y, width, height = _frame(layer)
        if layer.get("style") != "SOLID":
            raise CardRenderError("manifest line style is unsupported")
        thickness = int(layer.get("thickness", max(1, height)))
        draw = ImageDraw.Draw(canvas)
        center_y = y + height // 2
        draw.line(
            (x, center_y, x + width, center_y), fill=_color(layer.get("color_hex")), width=thickness
        )

    def _text(
        self,
        canvas: Image.Image,
        package: VerifiedTemplatePackage,
        manifest: Mapping[str, Any],
        layer: Mapping[str, Any],
        placeholders: Mapping[str, Any],
    ) -> None:
        runs, font, letter_spacing, layout = self._prepare_text(
            package,
            manifest,
            layer,
            placeholders,
        )
        draw = ImageDraw.Draw(canvas)
        _draw_runs(
            draw,
            runs,
            (layout.origin_x, layout.baseline_y),
            font=font,
            letter_spacing=letter_spacing,
        )

    def _prepare_text(
        self,
        package: VerifiedTemplatePackage,
        manifest: Mapping[str, Any],
        layer: Mapping[str, Any],
        placeholders: Mapping[str, Any],
    ) -> tuple[
        list[tuple[str, tuple[int, int, int, int]]],
        ImageFont.FreeTypeFont,
        float,
        TextLayout,
    ]:
        style = _object(layer.get("style"), "text style")
        fonts = _object(manifest.get("fonts"), "fonts")
        font_declaration = _object(fonts.get(style.get("font_key")), "font declaration")
        font_bytes = package.resource(str(font_declaration.get("package_path")))
        runs = _resolve_text_runs(
            layer.get("content"),
            placeholders,
            str(style.get("color_hex")),
            package.display_name_transformations,
        )
        combined = "".join(text for text, _ in runs)
        fitting = _object(layer.get("fitting"), "text fitting")
        if len(combined) > int(fitting.get("character_limit", 0)):
            raise CardRenderError("rendered text exceeds the manifest character limit")
        letter_spacing = float(style.get("letter_spacing", 0.0))
        font, layout = _fit_font(
            font_bytes,
            combined,
            layer=layer,
            minimum=int(fitting.get("minimum_font_size")),
            maximum=int(fitting.get("maximum_font_size")),
            configured=int(style.get("font_size")),
            letter_spacing=letter_spacing,
            line_height=float(style.get("line_height", 1.0)),
            horizontal_alignment=str(style.get("horizontal_alignment")),
        )
        if style.get("vertical_alignment") != "CENTER":
            raise CardRenderError("text vertical alignment is unsupported")
        return runs, font, letter_spacing, layout


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CardRenderError(f"manifest {label} is invalid")
    return value


def _frame(layer: Mapping[str, Any]) -> tuple[int, int, int, int]:
    frame = _object(layer.get("frame"), "layer frame")
    try:
        return tuple(int(frame[key]) for key in ("x", "y", "width", "height"))  # type: ignore[return-value]
    except (KeyError, TypeError, ValueError) as exc:
        raise CardRenderError("manifest layer frame is invalid") from exc


def _color(value: Any) -> tuple[int, int, int, int]:
    if not isinstance(value, str):
        raise CardRenderError("manifest color is invalid")
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError as exc:
        raise CardRenderError("manifest color is invalid") from exc
    return (*rgb, 255) if len(rgb) == 3 else rgb


def _open_rgba(content: bytes, label: str) -> Image.Image:
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            return source.convert("RGBA")
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        raise CardRenderError(f"{label} is not a readable bounded image") from exc


def _fit_image(image: Image.Image, size: tuple[int, int], fit: str) -> Image.Image:
    if fit == "FILL":
        return image.resize(size, Image.Resampling.LANCZOS)
    if fit == "CONTAIN":
        return _fit_photo(image, size, fit="CONTAIN", focus_x=0.5, focus_y=0.5, zoom=1.0)
    if fit == "COVER":
        return _fit_photo(image, size, fit="COVER", focus_x=0.5, focus_y=0.5, zoom=1.0)
    raise CardRenderError("manifest asset fit is unsupported")


def _fit_photo(
    image: Image.Image,
    size: tuple[int, int],
    *,
    fit: str,
    focus_x: float,
    focus_y: float,
    zoom: float,
) -> Image.Image:
    target_width, target_height = size
    if fit == "CONTAIN":
        scale = min(target_width / image.width, target_height / image.height)
        resized = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        result = Image.new("RGBA", size, (0, 0, 0, 0))
        result.alpha_composite(
            resized,
            ((target_width - resized.width) // 2, (target_height - resized.height) // 2),
        )
        return result
    scale = max(target_width / image.width, target_height / image.height) * zoom
    resized = image.resize(
        (
            max(target_width, round(image.width * scale)),
            max(target_height, round(image.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    maximum_left = resized.width - target_width
    maximum_top = resized.height - target_height
    left = min(maximum_left, max(0, round(maximum_left * focus_x)))
    top = min(maximum_top, max(0, round(maximum_top * focus_y)))
    return resized.crop((left, top, left + target_width, top + target_height))


def _composite(canvas: Image.Image, rendered: Image.Image, layer: Mapping[str, Any]) -> None:
    opacity = float(layer.get("opacity", 1.0))
    if not 0.0 <= opacity <= 1.0:
        raise CardRenderError("manifest layer opacity is invalid")
    if opacity != 1.0:
        alpha = rendered.getchannel("A").point(lambda value: round(value * opacity))
        rendered.putalpha(alpha)
    rotation = float(layer.get("rotation_degrees", 0.0))
    if rotation:
        rendered = rendered.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=False)
    x, y, _, _ = _frame(layer)
    canvas.alpha_composite(rendered, (x, y))


def _rasterize_svg(content: bytes, size: tuple[int, int]) -> Image.Image:
    """Rasterize the bounded Manifest V1 SVG subset used by verified assets."""
    if (
        len(content) > 64 * 1024
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
    ):
        raise TemplatePackageError("verified SVG asset is outside the safe subset")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise CardRenderError("verified SVG asset is invalid") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise CardRenderError("verified SVG root is unsupported")
    view_box = str(root.attrib.get("viewBox", "")).split()
    if len(view_box) != 4:
        raise CardRenderError("verified SVG viewBox is invalid")
    origin_x, origin_y, view_width, view_height = (float(value) for value in view_box)
    if view_width <= 0 or view_height <= 0:
        raise CardRenderError("verified SVG viewBox is invalid")
    scale_x = size[0] / view_width
    scale_y = size[1] / view_height
    output = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(output)
    for child in root:
        if child.tag.rsplit("}", 1)[-1] != "rect":
            raise CardRenderError("verified SVG element is unsupported")
        x = (float(child.attrib.get("x", 0)) - origin_x) * scale_x
        y = (float(child.attrib.get("y", 0)) - origin_y) * scale_y
        width = float(child.attrib["width"]) * scale_x
        height = float(child.attrib["height"]) * scale_y
        radius = float(child.attrib.get("rx", 0)) * min(scale_x, scale_y)
        fill_value = child.attrib.get("fill")
        fill = None if fill_value in {None, "none"} else _color(fill_value)
        stroke_value = child.attrib.get("stroke")
        stroke = None if stroke_value in {None, "none"} else _color(stroke_value)
        stroke_width = max(
            1,
            round(float(child.attrib.get("stroke-width", 1)) * min(scale_x, scale_y)),
        )
        draw.rounded_rectangle(
            (x, y, x + width, y + height),
            radius=radius,
            fill=fill,
            outline=stroke,
            width=stroke_width,
        )
    return output


def _resolve_text_runs(
    raw_content: Any,
    placeholders: Mapping[str, Any],
    default_color: str,
    display_name_transformations: Mapping[str, str] | None = None,
) -> list[tuple[str, tuple[int, int, int, int]]]:
    if not isinstance(raw_content, list):
        raise CardRenderError("manifest text content is invalid")
    runs: list[tuple[str, tuple[int, int, int, int]]] = []
    for raw_run in raw_content:
        run = _object(raw_run, "text run")
        run_type = run.get("type")
        if run_type == "LITERAL":
            value: Any = run.get("text")
        elif run_type == "PLACEHOLDER":
            key = str(run.get("key"))
            if key not in placeholders:
                raise CardRenderError("required template placeholder is missing")
            value = placeholders[key]
            transform = (display_name_transformations or {}).get(key)
            if transform is not None:
                try:
                    value = apply_display_name_transform(value, transform)
                except CardNamePolicyError as exc:
                    raise CardRenderError("display name could not be derived") from exc
            formatters = run.get("formatters", [])
            if not isinstance(formatters, list):
                raise CardRenderError("manifest text formatters are invalid")
            for formatter in formatters:
                value = _apply_formatter(value, _object(formatter, "text formatter"))
        else:
            raise CardRenderError("manifest text run type is unsupported")
        if not isinstance(value, str):
            raise CardRenderError("formatted text placeholder did not produce text")
        runs.append((value, _color(run.get("fill_color", default_color))))
    return runs


def _apply_formatter(value: Any, formatter: Mapping[str, Any]) -> Any:
    formatter_type = formatter.get("type")
    if formatter_type == "UPPERCASE":
        if not isinstance(value, str):
            raise CardRenderError("UPPERCASE requires text")
        return value.upper()
    if formatter_type == "JOIN_NAMES":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise CardRenderError("JOIN_NAMES requires an explicit ordered name list")
        separator = formatter.get("separator")
        if separator != "AMPERSAND":
            raise CardRenderError("JOIN_NAMES separator is unsupported")
        names = [name for name in value if isinstance(name, str) and name]
        if len(names) != len(value) or not names:
            raise CardRenderError("JOIN_NAMES contains an invalid explicit name")
        return " & ".join(names)
    if formatter_type == "DATE_FORMAT":
        if not isinstance(value, datetime):
            raise CardRenderError("DATE_FORMAT requires a datetime")
        formats = {
            "DATE_WEEKDAY_LONG_UPPER": value.strftime("%A").upper(),
            "DATE_MONTH_LONG_UPPER": value.strftime("%B").upper(),
            "DATE_DAY_OF_MONTH": str(value.day),
            "DATE_YEAR_FOUR_DIGIT": f"{value.year:04d}",
        }
        try:
            return formats[str(formatter.get("format"))]
        except KeyError as exc:
            raise CardRenderError("DATE_FORMAT variant is unsupported") from exc
    if formatter_type == "TIME_FORMAT":
        if not isinstance(value, datetime):
            raise CardRenderError("TIME_FORMAT requires a datetime")
        if formatter.get("format") != "TIME_12H_COMPACT_UPPER":
            raise CardRenderError("TIME_FORMAT variant is unsupported")
        hour = value.hour % 12 or 12
        return f"{hour}:{value.minute:02d} {value.strftime('%p').upper()}"
    raise CardRenderError("manifest text formatter is unsupported")


def _fit_font(
    font_bytes: bytes,
    text: str,
    *,
    layer: Mapping[str, Any],
    minimum: int,
    maximum: int,
    configured: int,
    letter_spacing: float,
    line_height: float,
    horizontal_alignment: str,
) -> tuple[ImageFont.FreeTypeFont, TextLayout]:
    if minimum <= 0 or maximum < minimum or configured < minimum:
        raise CardRenderError("manifest text font-size bounds are invalid")
    if line_height <= 0:
        raise CardRenderError("manifest text line height is invalid")
    x, y, frame_width, frame_height = _frame(layer)
    frame_bounds = (float(x), float(y), float(x + frame_width), float(y + frame_height))
    for size in range(min(configured, maximum), minimum - 1, -1):
        try:
            font = ImageFont.truetype(BytesIO(font_bytes), size=size)
        except OSError as exc:
            raise CardRenderError("verified template font could not be loaded") from exc
        ink_left, ink_top, ink_right, ink_bottom = _ink_bbox(font, text, letter_spacing)
        advance_width = _text_width(font, text, letter_spacing)
        ink_height = float(ink_bottom - ink_top)
        line_box_height = ink_height * line_height
        if horizontal_alignment == "CENTER":
            origin_x = x + (frame_width - advance_width) / 2
        elif horizontal_alignment == "LEFT":
            origin_x = float(x)
        elif horizontal_alignment == "RIGHT":
            origin_x = x + frame_width - advance_width
        else:
            raise CardRenderError("text horizontal alignment is unsupported")

        ascender_anchor_y = y + (frame_height - ink_height) / 2
        ascent, _ = font.getmetrics()
        baseline_y = ascender_anchor_y + ascent
        ink_bounds = (
            origin_x + ink_left,
            baseline_y + ink_top - ascent,
            origin_x + ink_right,
            baseline_y + ink_bottom - ascent,
        )
        leading = (line_box_height - ink_height) / 2
        line_box_bounds = (
            float(x),
            ink_bounds[1] - leading,
            float(x + frame_width),
            ink_bounds[3] + leading,
        )
        if (
            line_box_height <= frame_height
            and line_box_bounds[1] >= frame_bounds[1]
            and line_box_bounds[3] <= frame_bounds[3]
            and ink_bounds[0] >= frame_bounds[0]
            and ink_bounds[1] >= frame_bounds[1]
            and ink_bounds[2] <= frame_bounds[2]
            and ink_bounds[3] <= frame_bounds[3]
        ):
            return font, TextLayout(
                layer_id=str(layer.get("id")),
                text=text,
                font_size=size,
                frame_bounds=frame_bounds,
                ink_bounds=ink_bounds,
                line_box_bounds=line_box_bounds,
                baseline_y=baseline_y,
                origin_x=origin_x,
                advance_width=advance_width,
            )
    raise CardRenderError("rendered text does not fit its approved frame")


def _ink_bbox(
    font: ImageFont.FreeTypeFont,
    text: str,
    letter_spacing: float,
) -> tuple[float, float, float, float]:
    if not text:
        return (0.0, 0.0, 0.0, 0.0)
    if letter_spacing == 0.0:
        left, top, right, bottom = font.getbbox(text, anchor="la")
        return (float(left), float(top), float(right), float(bottom))
    left = float("inf")
    top = float("inf")
    right = float("-inf")
    bottom = float("-inf")
    position = 0.0
    for index, character in enumerate(text):
        char_left, char_top, char_right, char_bottom = font.getbbox(character, anchor="la")
        left = min(left, position + char_left)
        top = min(top, float(char_top))
        right = max(right, position + char_right)
        bottom = max(bottom, float(char_bottom))
        position += float(font.getlength(character))
        if index < len(text) - 1:
            position += letter_spacing
    return (left, top, right, bottom)


def _text_width(font: ImageFont.FreeTypeFont, text: str, letter_spacing: float) -> float:
    if not text:
        return 0.0
    if letter_spacing == 0.0:
        return float(font.getlength(text))
    return sum(float(font.getlength(character)) for character in text) + letter_spacing * (
        len(text) - 1
    )


def _draw_runs(
    draw: ImageDraw.ImageDraw,
    runs: Sequence[tuple[str, tuple[int, int, int, int]]],
    position: tuple[float, float],
    *,
    font: ImageFont.FreeTypeFont,
    letter_spacing: float,
) -> None:
    x, y = position
    if letter_spacing == 0.0:
        for text, color in runs:
            draw.text((x, y), text, font=font, fill=color, anchor="ls")
            x += float(font.getlength(text))
        return
    remaining = sum(len(text) for text, _ in runs)
    for text, color in runs:
        for character in text:
            draw.text((x, y), character, font=font, fill=color, anchor="ls")
            x += float(font.getlength(character))
            remaining -= 1
            if remaining:
                x += letter_spacing
