"""Opaque M5 QR credential primitives."""

from __future__ import annotations

import base64
import hashlib
import secrets

QR_CREDENTIAL_ENTROPY_BYTES = 20
QR_CREDENTIAL_LENGTH = 32


def generate_qr_credential() -> str:
    """Return a fixed-size 160-bit opaque Base32 bearer credential."""
    credential = base64.b32encode(secrets.token_bytes(QR_CREDENTIAL_ENTROPY_BYTES)).decode("ascii")
    if len(credential) != QR_CREDENTIAL_LENGTH:
        raise RuntimeError("QR credential generator returned an invalid length")
    return credential


def hash_qr_credential(credential: str) -> str:
    """Return the deterministic normalized SHA-256 lookup verifier."""
    return hashlib.sha256(credential.encode("ascii")).hexdigest()
