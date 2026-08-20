"""Deterministic unstyled QR image generation for the M5-to-M6 boundary."""

from __future__ import annotations

import re
from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M

QR_VERSION = 2
QR_ERROR_CORRECTION = "M"
QR_QUIET_ZONE_MODULES = 4
QR_MODULE_PIXELS = 6
QR_IMAGE_PIXELS = 198
_CREDENTIAL_PATTERN = re.compile(r"^[A-Z2-7]{32}$")


def render_qr_png(credential: str) -> bytes:
    """Encode exactly one credential as a fixed 198x198 metadata-free PNG."""
    if _CREDENTIAL_PATTERN.fullmatch(credential) is None:
        raise ValueError("QR credential does not match the fixed rendering policy")

    qr = qrcode.QRCode(
        version=QR_VERSION,
        error_correction=ERROR_CORRECT_M,
        box_size=QR_MODULE_PIXELS,
        border=QR_QUIET_ZONE_MODULES,
    )
    qr.add_data(credential, optimize=0)
    qr.make(fit=False)
    image = qr.make_image(fill_color="black", back_color="white").get_image()
    if image.size != (QR_IMAGE_PIXELS, QR_IMAGE_PIXELS):
        raise RuntimeError("QR renderer violated the fixed image-size policy")

    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()
