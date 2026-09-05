"""Opt-in PostgreSQL 17 acceptance for C2.2B admission references."""

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
PREVIOUS_HEAD = "20260905_0010"
CURRENT_HEAD = "20260906_0012"
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


def _seed_provenance_forms(engine) -> dict[str, UUID]:
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
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, singleton_key, username, password_hash, role, is_active
                ) VALUES (
                    :administrator_id, 1, 'synthetic-c22b-admin',
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
                    :event_id, 'Synthetic C2.2B Event', 'Synthetic Bride',
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
        for guest_key, name in (
            ("gate_guest_id", "Gate Origin"),
            ("correction_guest_id", "Correction Origin"),
        ):
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
                    "full_name": f"Synthetic C2.2B {name}",
                },
            )
        for ordinal, (guest_key, credential_key) in enumerate(
            (
                ("gate_guest_id", "gate_credential_id"),
                ("correction_guest_id", "correction_credential_id"),
            ),
            start=1,
        ):
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
                        f"{identifiers['event_id'].hex}/"
                        f"{identifiers[guest_key].hex}/"
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
            ordinal=10,
            guest_id=identifiers["gate_guest_id"],
            credential_id=identifiers["gate_credential_id"],
            attendance_id=identifiers["attendance_id"],
            arrival_mode="PRIMARY",
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
                    :attendance_id, NULL, 1, 0
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
                    'SET_ATTENDED', 'MISSED_GATE_SCAN', NULL, 1,
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
                    NULL, :correction_id, 1, 0
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
    guest_id: UUID | None,
    credential_id: UUID | None,
    attendance_id: UUID | None,
    arrival_mode: str,
) -> None:
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
                :credential_id, :attendance_id, 'ADMITTED',
                0, :request_id, :arrival_mode,
                NULL, :primary_admitted, :companion_delta, 1,
                :companions_after, :administrator_id
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
            "request_id": uuid4(),
            "arrival_mode": arrival_mode,
            "primary_admitted": arrival_mode == "PRIMARY",
            "companion_delta": 0 if arrival_mode == "PRIMARY" else 1,
            "companions_after": 0 if arrival_mode == "PRIMARY" else 1,
        },
    )


def _assert_live_admitted_reference_constraints(engine, identifiers: dict[str, UUID]) -> None:
    with engine.begin() as connection:
        _insert_attempt(
            connection,
            identifiers,
            ordinal=11,
            guest_id=identifiers["gate_guest_id"],
            credential_id=identifiers["gate_credential_id"],
            attendance_id=identifiers["attendance_id"],
            arrival_mode="COMPANION",
        )
        _insert_attempt(
            connection,
            identifiers,
            ordinal=12,
            guest_id=identifiers["correction_guest_id"],
            credential_id=identifiers["correction_credential_id"],
            attendance_id=None,
            arrival_mode="COMPANION",
        )
        connection.execute(
            text(
                """
                UPDATE guest_companion_entitlements
                   SET companions_admitted = 1
                 WHERE id IN (:gate_entitlement_id, :correction_entitlement_id)
                """
            ),
            identifiers,
        )

    invalid_shapes = (
        (
            identifiers["gate_guest_id"],
            identifiers["gate_credential_id"],
            None,
            "PRIMARY",
        ),
        (None, identifiers["gate_credential_id"], identifiers["attendance_id"], "PRIMARY"),
        (identifiers["gate_guest_id"], None, identifiers["attendance_id"], "PRIMARY"),
        (None, identifiers["gate_credential_id"], None, "COMPANION"),
        (identifiers["gate_guest_id"], None, None, "COMPANION"),
        (
            identifiers["gate_guest_id"],
            identifiers["gate_credential_id"],
            identifiers["attendance_id"],
            "UNKNOWN",
        ),
    )
    for ordinal, (guest_id, credential_id, attendance_id, arrival_mode) in enumerate(
        invalid_shapes, start=20
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_attempt(
                connection,
                identifiers,
                ordinal=ordinal,
                guest_id=guest_id,
                credential_id=credential_id,
                attendance_id=attendance_id,
                arrival_mode=arrival_mode,
            )

    with engine.connect() as connection:
        accepted = connection.execute(
            text(
                """
                SELECT arrival_mode, attendance_id
                  FROM gate_scan_attempts
                 WHERE outcome = 'ADMITTED'
                 ORDER BY credential_fingerprint
                """
            )
        ).all()
    assert accepted == [
        ("PRIMARY", identifiers["attendance_id"]),
        ("COMPANION", identifiers["attendance_id"]),
        ("COMPANION", None),
    ]


def test_c22b_companion_admission_reference_lifecycle_and_live_constraints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_test_environment(monkeypatch, tmp_path)
    assert DISPOSABLE_URL is not None
    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    try:
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0
        with engine.connect() as connection:
            assert connection.execute(text("SHOW server_version_num")).scalar_one().startswith("17")
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)
        check_names = {
            constraint["name"]
            for constraint in inspect(engine).get_check_constraints("gate_scan_attempts")
        }
        assert "ck_gate_scan_attempts_outcome_references" in check_names

        assert disposable_alembic.main(["downgrade", PREVIOUS_HEAD]) == 0
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (PREVIOUS_HEAD)
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0

        identifiers = _seed_provenance_forms(engine)
        _assert_live_admitted_reference_constraints(engine, identifiers)

        with pytest.raises(SQLAlchemyError, match="not legacy-representable"):
            disposable_alembic.main(["downgrade", PREVIOUS_HEAD])
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)
    finally:
        if inspect(engine).has_table("gate_scan_attempts"):
            with engine.begin() as connection:
                connection.execute(
                    text("TRUNCATE TABLE gate_scan_attempts, guest_companion_entitlements CASCADE")
                )
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
