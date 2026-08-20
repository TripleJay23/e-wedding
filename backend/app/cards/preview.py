"""Synthetic non-resolving QR image for authenticated design previews only."""

from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M

PREVIEW_QR_SENTINEL = "PREVIEW-QR-DO-NOT-SCAN"


def render_preview_qr_png() -> bytes:
    """Render the documented synthetic sentinel under the approved QR geometry."""
    qr = qrcode.QRCode(version=2, error_correction=ERROR_CORRECT_M, box_size=6, border=4)
    qr.add_data(PREVIEW_QR_SENTINEL, optimize=0)
    qr.make(fit=False)
    image = qr.make_image(fill_color="black", back_color="white").get_image()
    if image.size != (198, 198):
        raise RuntimeError("preview QR renderer violated the approved geometry")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()
