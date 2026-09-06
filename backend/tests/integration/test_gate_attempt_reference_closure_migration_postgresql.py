"""Opt-in PostgreSQL 17 acceptance for C2.2C attempt references."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.cli import disposable_alembic
from app.core.settings import Settings

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
PREVIOUS_HEAD = "20260905_0011"
CURRENT_HEAD = "20260906_0013"
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def configure_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    assert DISPOSABLE_URL is not None
    private_root = tmp_path / "private"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "import-evidence"))
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr-codes"))
    return Settings()


def _seed_reference_origins(engine) -> dict[str, UUID]:
    identifiers = {
        "administrator_id": uuid4(),
        "event_id": uuid4(),
        "gate_guest_id": uuid4(),
        "gate_credential_id": uuid4(),
        "attendance_id": uuid4(),
        "gate_entitlement_id": uuid4(),
        "correction_guest_id": uuid4(),
        "correction_credential_id": uuid4(),
        "correction_id": uuid4(),
        "correction_entitlement_id": uuid4(),
        "pre_primary_guest_id": uuid4(),
        "pre_primary_credential_id": uuid4(),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, singleton_key, username, password_hash, role, is_active
                ) VALUES (
                    :administrator_id, 1, 'synthetic-c22c-admin',
                    '$argon2id$synthetic', 'SUPER_ADMIN', true
                )
                """
            ),
            identifiers,
        )
        connection.execute(
            text(
                """
                INSERT INTO events (
                    id, event_name, bride_name, groom_name, event_datetime,
                    venue, address, description, status,
                    max_companions_per_invitation, created_by
                ) VALUES (
                    :event_id, 'Synthetic C2.2C Event', 'Synthetic Bride',
                    'Synthetic Groom', :event_datetime, 'Synthetic Venue', NULL,
                    NULL, 'ACTIVE', 2, :administrator_id
                )
                """
            ),
            {
                **identifiers,
                "event_datetime": datetime(2027, 6, 12, 14, 30, tzinfo=UTC),
            },
        )
        guest_shapes = (
            ("gate_guest_id", "Gate Origin"),
            ("correction_guest_id", "Correction Origin"),
            ("pre_primary_guest_id", "Before Primary"),
        )
        for guest_key, label in guest_shapes:
            connection.execute(
                text(
                    """
                    INSERT INTO guests (
                        id, event_id, full_name, phone_number, email, invitation_type,
                        allowed_companions, table_number, category, status
                    ) VALUES (
                        :guest_id, :event_id, :full_name, NULL, NULL,
                        'Synthetic', 2, NULL, NULL, 'ACTIVE'
                    )
                    """
                ),
                {
                    **identifiers,
                    "guest_id": identifiers[guest_key],
                    "full_name": f"Synthetic C2.2C {label}",
                },
            )
        credential_shapes = (
            ("gate_guest_id", "gate_credential_id"),
            ("correction_guest_id", "correction_credential_id"),
            ("pre_primary_guest_id", "pre_primary_credential_id"),
        )
        for ordinal, (guest_key, credential_key) in enumerate(credential_shapes, start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO guest_qr_credentials (
                        id, event_id, guest_id, token_hash, image_storage_key,
                        image_sha256, status, replaces_credential_id, issued_by,
                        deactivated_at, superseded_at
                    ) VALUES (
                        :credential_id, :event_id, :guest_id, :token_hash,
                        :image_storage_key, :image_sha256, 'ACTIVE', NULL,
                        :administrator_id, NULL, NULL
                    )
                    """
                ),
                {
                    **identifiers,
                    "guest_id": identifiers[guest_key],
                    "credential_id": identifiers[credential_key],
                    "token_hash": f"{ordinal:064x}",
                    "image_storage_key": (
                        f"{identifiers['event_id'].hex}/{identifiers[guest_key].hex}/"
                        f"{identifiers[credential_key].hex}.png"
                    ),
                    "image_sha256": f"{ordinal + 100:064x}",
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO guest_attendance_records (
                    id, event_id, guest_id, first_qr_credential_id,
                    companions_admitted, checked_in_by
                ) VALUES (
                    :attendance_id, :event_id, :gate_guest_id,
                    :gate_credential_id, 0, :administrator_id
                )
                """
            ),
            identifiers,
        )
        _insert_attempt(
            connection,
            identifiers,
            ordinal=1,
            outcome="ADMITTED",
            arrival_mode="PRIMARY",
            guest_id=identifiers["gate_guest_id"],
            credential_id=identifiers["gate_credential_id"],
            attendance_id=identifiers["attendance_id"],
        )
        connection.execute(
            text(
                """
                INSERT INTO guest_companion_entitlements (
                    id, event_id, guest_id, primary_attendance_id,
                    establishing_correction_id, declared_companions,
                    companions_admitted
                ) VALUES (
                    :gate_entitlement_id, :event_id, :gate_guest_id,
                    :attendance_id, NULL, 2, 0
                )
                """
            ),
            identifiers,
        )
        connection.execute(
            text(
                """
                INSERT INTO guest_attendance_corrections (
                    id, event_id, guest_id, revision_number, previous_correction_id,
                    decision, reason_code, reason_note, companions_admitted,
                    gate_attendance_record_id, previous_effective_status,
                    resulting_effective_status, resulting_effective_source,
                    corrected_by_user_id
                ) VALUES (
                    :correction_id, :event_id, :correction_guest_id, 1, NULL,
                    'SET_ATTENDED', 'MISSED_GATE_SCAN', NULL, 0,
                    NULL, 'NOT_ATTENDED', 'ATTENDED', 'MANUAL', :administrator_id
                )
                """
            ),
            identifiers,
        )
        connection.execute(
            text(
                """
                INSERT INTO guest_companion_entitlements (
                    id, event_id, guest_id, primary_attendance_id,
                    establishing_correction_id, declared_companions,
                    companions_admitted
                ) VALUES (
                    :correction_entitlement_id, :event_id, :correction_guest_id,
                    NULL, :correction_id, 2, 0
                )
                """
            ),
            identifiers,
        )
    return identifiers


def _insert_attempt(
    connection,
    identifiers: dict[str, UUID],
    *,
    ordinal: int,
    outcome: str,
    arrival_mode: str,
    guest_id: UUID | None,
    credential_id: UUID | None,
    attendance_id: UUID | None,
) -> None:
    admitted = outcome == "ADMITTED"
    primary = admitted and arrival_mode == "PRIMARY"
    companion_delta = 1 if admitted and arrival_mode == "COMPANION" else 0
    connection.execute(
        text(
            """
            INSERT INTO gate_scan_attempts (
                id, event_id, credential_fingerprint, guest_id,
                qr_credential_id, attendance_id, outcome,
                requested_companions, request_id, arrival_mode,
                requested_declared_companions, primary_admitted_this_scan,
                companions_admitted_this_scan, declared_companions_after,
                companions_admitted_after, scanned_by
            ) VALUES (
                :id, :event_id, :fingerprint, :guest_id,
                :credential_id, :attendance_id, :outcome,
                :requested_companions, :request_id, :arrival_mode,
                :requested_declaration, :primary_admitted, :companion_delta,
                :declared_after, :admitted_after, :administrator_id
            )
            """
        ),
        {
            **identifiers,
            "id": uuid4(),
            "fingerprint": f"{ordinal:064x}",
            "guest_id": guest_id,
            "credential_id": credential_id,
            "attendance_id": attendance_id,
            "outcome": outcome,
            "requested_companions": companion_delta,
            "request_id": uuid4(),
            "arrival_mode": arrival_mode,
            "requested_declaration": 0 if arrival_mode == "PRIMARY" else None,
            "primary_admitted": primary,
            "companion_delta": companion_delta,
            "declared_after": 1 if admitted else None,
            "admitted_after": companion_delta if admitted else None,
        },
    )


def _assert_complete_c23_reference_matrix(engine, identifiers: dict[str, UUID]) -> None:
    gate_guest = identifiers["gate_guest_id"]
    gate_credential = identifiers["gate_credential_id"]
    correction_guest = identifiers["correction_guest_id"]
    correction_credential = identifiers["correction_credential_id"]
    pre_guest = identifiers["pre_primary_guest_id"]
    pre_credential = identifiers["pre_primary_credential_id"]
    attendance = identifiers["attendance_id"]
    shapes = (
        ("ADMITTED", "COMPANION", gate_guest, gate_credential, attendance),
        ("ADMITTED", "COMPANION", correction_guest, correction_credential, None),
        ("ALREADY_CHECKED_IN", "PRIMARY", gate_guest, gate_credential, attendance),
        ("ALREADY_CHECKED_IN", "PRIMARY", correction_guest, correction_credential, None),
        ("PRIMARY_REQUIRED", "COMPANION", pre_guest, pre_credential, None),
        (
            "COMPANION_ENTITLEMENT_EXHAUSTED",
            "COMPANION",
            gate_guest,
            gate_credential,
            attendance,
        ),
        (
            "COMPANION_ENTITLEMENT_EXHAUSTED",
            "COMPANION",
            correction_guest,
            correction_credential,
            None,
        ),
        (
            "INSUFFICIENT_COMPANION_SLOTS",
            "COMPANION",
            gate_guest,
            gate_credential,
            attendance,
        ),
        (
            "INSUFFICIENT_COMPANION_SLOTS",
            "COMPANION",
            correction_guest,
            correction_credential,
            None,
        ),
        ("EVENT_NOT_ACTIVE", "PRIMARY", pre_guest, pre_credential, None),
        ("EVENT_NOT_ACTIVE", "PRIMARY", gate_guest, gate_credential, attendance),
        ("EVENT_NOT_ACTIVE", "PRIMARY", correction_guest, correction_credential, None),
        ("UNKNOWN_CREDENTIAL", "PRIMARY", None, None, None),
        ("WRONG_EVENT", "PRIMARY", None, None, None),
        ("INACTIVE_CREDENTIAL", "PRIMARY", pre_guest, pre_credential, None),
        ("SUPERSEDED_CREDENTIAL", "PRIMARY", pre_guest, pre_credential, None),
        ("INELIGIBLE_GUEST", "PRIMARY", pre_guest, pre_credential, None),
        ("COMPANION_LIMIT_EXCEEDED", "PRIMARY", pre_guest, pre_credential, None),
    )
    with engine.begin() as connection:
        for ordinal, shape in enumerate(shapes, start=100):
            outcome, arrival_mode, guest_id, credential_id, attendance_id = shape
            _insert_attempt(
                connection,
                identifiers,
                ordinal=ordinal,
                outcome=outcome,
                arrival_mode=arrival_mode,
                guest_id=guest_id,
                credential_id=credential_id,
                attendance_id=attendance_id,
            )


def _assert_invalid_already_references_rejected(engine, identifiers: dict[str, UUID]) -> None:
    invalid_shapes = (
        (None, identifiers["gate_credential_id"], identifiers["attendance_id"]),
        (identifiers["gate_guest_id"], None, identifiers["attendance_id"]),
    )
    for ordinal, (guest_id, credential_id, attendance_id) in enumerate(invalid_shapes, start=200):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_attempt(
                connection,
                identifiers,
                ordinal=ordinal,
                outcome="ALREADY_CHECKED_IN",
                arrival_mode="PRIMARY",
                guest_id=guest_id,
                credential_id=credential_id,
                attendance_id=attendance_id,
            )


def test_c22c_gate_attempt_reference_closure_lifecycle_and_complete_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    assert DISPOSABLE_URL is not None
    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    try:
        assert disposable_alembic.main(["downgrade", "base"]) == 0
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0
        with engine.connect() as connection:
            assert connection.execute(text("SHOW server_version_num")).scalar_one().startswith("17")
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)

        identifiers = _seed_reference_origins(engine)
        with engine.begin() as connection:
            _insert_attempt(
                connection,
                identifiers,
                ordinal=10,
                outcome="ALREADY_CHECKED_IN",
                arrival_mode="PRIMARY",
                guest_id=identifiers["gate_guest_id"],
                credential_id=identifiers["gate_credential_id"],
                attendance_id=identifiers["attendance_id"],
            )

        assert disposable_alembic.main(["downgrade", PREVIOUS_HEAD]) == 0
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0

        _assert_complete_c23_reference_matrix(engine, identifiers)
        _assert_invalid_already_references_rejected(engine, identifiers)

        with pytest.raises(SQLAlchemyError, match="not 0011-representable"):
            disposable_alembic.main(["downgrade", PREVIOUS_HEAD])
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)
            accepted_correction_origin = connection.execute(
                text(
                    """
                    SELECT count(*)
                      FROM gate_scan_attempts
                     WHERE outcome = 'ALREADY_CHECKED_IN'
                       AND guest_id = :correction_guest_id
                       AND qr_credential_id = :correction_credential_id
                       AND attendance_id IS NULL
                    """
                ),
                identifiers,
            ).scalar_one()
            assert accepted_correction_origin == 1
    finally:
        if inspect(engine).has_table("gate_scan_attempts"):
            with engine.begin() as connection:
                connection.execute(
                    text("TRUNCATE TABLE gate_scan_attempts, guest_companion_entitlements CASCADE")
                )
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
