"""Opt-in PostgreSQL 17 acceptance for C2.2A gate outcome persistence."""

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
PREVIOUS_HEAD = "20260905_0009"
CURRENT_HEAD = "20260906_0012"
LEGACY_OUTCOMES = (
    "ADMITTED",
    "ALREADY_CHECKED_IN",
    "UNKNOWN_CREDENTIAL",
    "WRONG_EVENT",
    "INACTIVE_CREDENTIAL",
    "SUPERSEDED_CREDENTIAL",
    "INELIGIBLE_GUEST",
    "COMPANION_LIMIT_EXCEEDED",
)
NEW_OUTCOMES = (
    "PRIMARY_REQUIRED",
    "COMPANION_ENTITLEMENT_EXHAUSTED",
    "INSUFFICIENT_COMPANION_SLOTS",
    "EVENT_NOT_ACTIVE",
)
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


def _seed_invitation(engine) -> dict[str, UUID]:
    identifiers = {
        "administrator_id": uuid4(),
        "event_id": uuid4(),
        "guest_id": uuid4(),
        "credential_id": uuid4(),
        "attendance_id": uuid4(),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, singleton_key, username, password_hash, role, is_active
                ) VALUES (
                    :administrator_id, 1, 'synthetic-c22a-admin', '$argon2id$synthetic',
                    'SUPER_ADMIN', true
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
                    :event_id, 'Synthetic C2.2A Event', 'Synthetic Bride',
                    'Synthetic Groom', :event_datetime, 'Synthetic Venue', NULL, NULL,
                    'ACTIVE', 2, :administrator_id
                )
                """
            ),
            {
                **identifiers,
                "event_datetime": datetime(2027, 6, 12, 14, 30, tzinfo=UTC),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO guests (
                    id, event_id, full_name, phone_number, email, invitation_type,
                    allowed_companions, table_number, category, status
                ) VALUES (
                    :guest_id, :event_id, 'Synthetic C2.2A Guest', NULL, NULL,
                    'Synthetic', 2, NULL, NULL, 'ACTIVE'
                )
                """
            ),
            identifiers,
        )
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
                "token_hash": "1" * 64,
                "image_storage_key": (
                    f"{identifiers['event_id'].hex}/{identifiers['guest_id'].hex}/"
                    f"{identifiers['credential_id'].hex}.png"
                ),
                "image_sha256": "2" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO guest_attendance_records (
                    id, event_id, guest_id, first_qr_credential_id,
                    companions_admitted, checked_in_by
                ) VALUES (
                    :attendance_id, :event_id, :guest_id, :credential_id,
                    0, :administrator_id
                )
                """
            ),
            identifiers,
        )
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
                    :attempt_id, :event_id, :fingerprint, :guest_id,
                    :credential_id, :attendance_id, 'ADMITTED',
                    0, :request_id, 'PRIMARY',
                    0, true, 0, 0, 0, :administrator_id
                )
                """
            ),
            {
                **identifiers,
                "attempt_id": uuid4(),
                "fingerprint": "3" * 64,
                "request_id": uuid4(),
            },
        )
    return identifiers


def _attempt_parameters(
    identifiers: dict[str, UUID],
    *,
    ordinal: int,
    outcome: str,
    attendance: bool | None,
    invitation_references: bool,
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "event_id": identifiers["event_id"],
        "fingerprint": f"{ordinal:064x}",
        "guest_id": identifiers["guest_id"] if invitation_references else None,
        "credential_id": (identifiers["credential_id"] if invitation_references else None),
        "attendance_id": identifiers["attendance_id"] if attendance else None,
        "outcome": outcome,
        "request_id": uuid4(),
        "arrival_mode": "PRIMARY" if outcome != "PRIMARY_REQUIRED" else "COMPANION",
        "administrator_id": identifiers["administrator_id"],
    }


def _insert_attempt(connection, parameters: dict[str, object]) -> None:
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
                0, :request_id, :arrival_mode,
                NULL, false, 0, NULL, NULL, :administrator_id
            )
            """
        ),
        parameters,
    )


def _assert_live_outcome_constraints(engine, identifiers: dict[str, UUID]) -> None:
    valid_shapes: list[tuple[str, bool | None, bool]] = [
        ("ALREADY_CHECKED_IN", True, True),
        ("UNKNOWN_CREDENTIAL", False, False),
        ("WRONG_EVENT", False, False),
        ("INACTIVE_CREDENTIAL", False, True),
        ("SUPERSEDED_CREDENTIAL", False, True),
        ("INELIGIBLE_GUEST", False, True),
        ("COMPANION_LIMIT_EXCEEDED", False, True),
        ("PRIMARY_REQUIRED", False, True),
        ("COMPANION_ENTITLEMENT_EXHAUSTED", True, True),
        ("INSUFFICIENT_COMPANION_SLOTS", True, True),
        ("EVENT_NOT_ACTIVE", False, True),
    ]
    with engine.begin() as connection:
        for ordinal, (outcome, attendance, references) in enumerate(valid_shapes, start=10):
            _insert_attempt(
                connection,
                _attempt_parameters(
                    identifiers,
                    ordinal=ordinal,
                    outcome=outcome,
                    attendance=attendance,
                    invitation_references=references,
                ),
            )

    invalid_shapes = [
        ("UNAPPROVED_OUTCOME", False, False),
        ("PRIMARY_REQUIRED", True, True),
        ("EVENT_NOT_ACTIVE", False, False),
        ("COMPANION_ENTITLEMENT_EXHAUSTED", False, False),
        ("INSUFFICIENT_COMPANION_SLOTS", False, False),
        ("UNKNOWN_CREDENTIAL", False, True),
        ("ADMITTED", False, True),
    ]
    for ordinal, (outcome, attendance, references) in enumerate(invalid_shapes, start=40):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_attempt(
                connection,
                _attempt_parameters(
                    identifiers,
                    ordinal=ordinal,
                    outcome=outcome,
                    attendance=attendance,
                    invitation_references=references,
                ),
            )

    with engine.connect() as connection:
        accepted = set(connection.execute(text("SELECT outcome FROM gate_scan_attempts")).scalars())
    assert accepted == set(LEGACY_OUTCOMES) | set(NEW_OUTCOMES)


def test_c22a_gate_outcome_migration_lifecycle_and_live_constraints(
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
        assert {
            "ck_gate_scan_attempts_outcome",
            "ck_gate_scan_attempts_outcome_references",
        } <= check_names

        assert disposable_alembic.main(["downgrade", PREVIOUS_HEAD]) == 0
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (PREVIOUS_HEAD)
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0

        identifiers = _seed_invitation(engine)
        _assert_live_outcome_constraints(engine, identifiers)

        with pytest.raises(SQLAlchemyError, match="not legacy-representable"):
            disposable_alembic.main(["downgrade", PREVIOUS_HEAD])
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)
    finally:
        if inspect(engine).has_table("gate_scan_attempts"):
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE TABLE gate_scan_attempts CASCADE"))
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
