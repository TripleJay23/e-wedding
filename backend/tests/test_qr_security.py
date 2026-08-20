"""M5 opaque credential and deterministic QR decode verification."""

import re
from io import BytesIO

import zxingcpp
from PIL import Image

from app.qr.credentials import generate_qr_credential, hash_qr_credential
from app.qr.images import (
    QR_ERROR_CORRECTION,
    QR_IMAGE_PIXELS,
    QR_MODULE_PIXELS,
    QR_QUIET_ZONE_MODULES,
    QR_VERSION,
    render_qr_png,
)


def test_credentials_are_unique_fixed_high_entropy_csprng_values() -> None:
    credentials = {generate_qr_credential() for _ in range(256)}

    assert len(credentials) == 256
    assert all(re.fullmatch(r"[A-Z2-7]{32}", credential) for credential in credentials)
    assert all(re.fullmatch(r"[0-9a-f]{64}", hash_qr_credential(value)) for value in credentials)
    assert len({hash_qr_credential(value) for value in credentials}) == 256


def test_qr_png_decodes_to_only_the_credential_with_fixed_safe_geometry() -> None:
    credential = generate_qr_credential()
    guest_pii = ("Synthetic Guest", "+255700000001", "guest@example.test", "Table 9")

    content = render_qr_png(credential)
    image = Image.open(BytesIO(content))
    decoded = zxingcpp.read_barcodes(image)

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.size == (QR_IMAGE_PIXELS, QR_IMAGE_PIXELS) == (198, 198)
    assert image.info == {}
    assert len(decoded) == 1
    assert decoded[0].text == credential
    assert all(value not in decoded[0].text for value in guest_pii)
    assert QR_VERSION == 2
    assert QR_ERROR_CORRECTION == "M"
    assert QR_QUIET_ZONE_MODULES == 4
    assert QR_MODULE_PIXELS == 6

    quiet_zone_pixels = QR_QUIET_ZONE_MODULES * QR_MODULE_PIXELS
    grayscale = image.convert("L")
    assert all(
        grayscale.getpixel((x, y)) == 255
        for x in range(QR_IMAGE_PIXELS)
        for y in range(quiet_zone_pixels)
    )
    assert all(
        grayscale.getpixel((x, y)) == 255
        for x in range(quiet_zone_pixels)
        for y in range(QR_IMAGE_PIXELS)
    )
