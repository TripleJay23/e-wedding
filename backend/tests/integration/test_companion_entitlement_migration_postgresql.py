"""Opt-in PostgreSQL 17 acceptance for the C2.2 migration lifecycle."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.settings import Settings
from app.models.attendance import GuestCompanionEntitlement

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
PREVIOUS_HEAD = "20260821_0008"
CURRENT_HEAD = "20260905_0010"
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


def _seed_legacy_evidence(engine) -> dict[str, object]:
    administrator_id = uuid4()
    event_id = uuid4()
    other_event_id = uuid4()
    guest_ids = {name: uuid4() for name in ("none", "zero", "one", "two", "manual")}
    attendance_ids = {name: uuid4() for name in ("zero", "one", "two")}
    admitted_attempt_ids = {name: uuid4() for name in ("zero", "one", "two")}
    manual_correction_id = uuid4()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, singleton_key, username, password_hash, role, is_active)
                VALUES (:id, 1, 'synthetic-c22-admin', '$argon2id$synthetic',
                        'SUPER_ADMIN', true)
                """
            ),
            {"id": administrator_id},
        )
        for selected_event_id, name in (
            (event_id, "Synthetic C2.2 Event"),
            (other_event_id, "Synthetic C2.2 Other Event"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO events (
                        id, event_name, bride_name, groom_name, event_datetime,
                        venue, address, description, status,
                        max_companions_per_invitation, created_by
                    ) VALUES (
                        :id, :name, 'Synthetic Bride', 'Synthetic Groom', :event_datetime,
                        'Synthetic Venue', NULL, NULL, 'ACTIVE', 2, :administrator_id
                    )
                    """
                ),
                {
                    "id": selected_event_id,
                    "name": name,
                    "event_datetime": datetime(2027, 6, 12, 14, 30, tzinfo=UTC),
                    "administrator_id": administrator_id,
                },
            )
        for name, guest_id in guest_ids.items():
            connection.execute(
                text(
                    """
                    INSERT INTO guests (
                        id, event_id, full_name, phone_number, email, invitation_type,
                        allowed_companions, table_number, category, status
                    ) VALUES (
                        :id, :event_id, :full_name, NULL, NULL, 'Synthetic',
                        2, NULL, NULL, 'ACTIVE'
                    )
                    """
                ),
                {
                    "id": guest_id,
                    "event_id": event_id,
                    "full_name": f"Synthetic C2.2 {name}",
                },
            )

        for ordinal, (name, companions) in enumerate((("zero", 0), ("one", 1), ("two", 2))):
            guest_id = guest_ids[name]
            credential_id = uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO guest_qr_credentials (
                        id, event_id, guest_id, token_hash, image_storage_key,
                        image_sha256, status, replaces_credential_id, issued_by,
                        deactivated_at, superseded_at
                    ) VALUES (
                        :id, :event_id, :guest_id, :token_hash, :image_storage_key,
                        :image_sha256, 'ACTIVE', NULL, :administrator_id, NULL, NULL
                    )
                    """
                ),
                {
                    "id": credential_id,
                    "event_id": event_id,
                    "guest_id": guest_id,
                    "token_hash": f"{ordinal + 1:064x}",
                    "image_storage_key": (f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png"),
                    "image_sha256": f"{ordinal + 101:064x}",
                    "administrator_id": administrator_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO guest_attendance_records (
                        id, event_id, guest_id, first_qr_credential_id,
                        companions_admitted, checked_in_by
                    ) VALUES (
                        :id, :event_id, :guest_id, :credential_id,
                        :companions, :administrator_id
                    )
                    """
                ),
                {
                    "id": attendance_ids[name],
                    "event_id": event_id,
                    "guest_id": guest_id,
                    "credential_id": credential_id,
                    "companions": companions,
                    "administrator_id": administrator_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO gate_scan_attempts (
                        id, event_id, credential_fingerprint, guest_id,
                        qr_credential_id, attendance_id, outcome,
                        requested_companions, scanned_by
                    ) VALUES (
                        :id, :event_id, :fingerprint, :guest_id,
                        :credential_id, :attendance_id, 'ADMITTED',
                        :companions, :administrator_id
                    )
                    """
                ),
                {
                    "id": admitted_attempt_ids[name],
                    "event_id": event_id,
                    "fingerprint": f"{ordinal + 201:064x}",
                    "guest_id": guest_id,
                    "credential_id": credential_id,
                    "attendance_id": attendance_ids[name],
                    "companions": companions,
                    "administrator_id": administrator_id,
                },
            )

        connection.execute(
            text(
                """
                INSERT INTO gate_scan_attempts (
                    id, event_id, credential_fingerprint, guest_id,
                    qr_credential_id, attendance_id, outcome,
                    requested_companions, scanned_by
                )
                SELECT
                    :id, event_id, :fingerprint, guest_id,
                    first_qr_credential_id, id, 'ALREADY_CHECKED_IN', 0, :administrator_id
                  FROM guest_attendance_records
                 WHERE id = :attendance_id
                """
            ),
            {
                "id": uuid4(),
                "fingerprint": f"{301:064x}",
                "administrator_id": administrator_id,
                "attendance_id": attendance_ids["one"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO gate_scan_attempts (
                    id, event_id, credential_fingerprint, guest_id,
                    qr_credential_id, attendance_id, outcome,
                    requested_companions, scanned_by
                ) VALUES (
                    :id, :event_id, :fingerprint, NULL,
                    NULL, NULL, 'UNKNOWN_CREDENTIAL', 0, :administrator_id
                )
                """
            ),
            {
                "id": uuid4(),
                "event_id": event_id,
                "fingerprint": f"{302:064x}",
                "administrator_id": administrator_id,
            },
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
                    :id, :event_id, :guest_id, 1, NULL,
                    'SET_ATTENDED', 'MISSED_GATE_SCAN', NULL, 1,
                    NULL, 'NOT_ATTENDED', 'ATTENDED', 'MANUAL', :administrator_id
                )
                """
            ),
            {
                "id": manual_correction_id,
                "event_id": event_id,
                "guest_id": guest_ids["manual"],
                "administrator_id": administrator_id,
            },
        )

    return {
        "administrator_id": administrator_id,
        "event_id": event_id,
        "other_event_id": other_event_id,
        "guest_ids": guest_ids,
        "attendance_ids": attendance_ids,
        "admitted_attempt_ids": admitted_attempt_ids,
        "manual_correction_id": manual_correction_id,
    }


def _expect_integrity_error(connection, statement: str, parameters: dict[str, object]) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError):
        connection.execute(text(statement), parameters)
    savepoint.rollback()


def _assert_backfill(engine, seeded: dict[str, object]) -> None:
    guest_ids = seeded["guest_ids"]
    attendance_ids = seeded["attendance_ids"]
    admitted_attempt_ids = seeded["admitted_attempt_ids"]
    assert isinstance(guest_ids, dict)
    assert isinstance(attendance_ids, dict)
    assert isinstance(admitted_attempt_ids, dict)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT guest_id, primary_attendance_id, establishing_correction_id,
                       declared_companions, companions_admitted,
                       created_at IS NOT NULL AS has_created_at,
                       updated_at IS NOT NULL AS has_updated_at
                  FROM guest_companion_entitlements
                """
            )
        ).mappings()
        by_guest = {row["guest_id"]: row for row in rows}

        assert guest_ids["none"] not in by_guest
        for name, expected in (("zero", 0), ("one", 1), ("two", 2)):
            row = by_guest[guest_ids[name]]
            assert row["primary_attendance_id"] == attendance_ids[name]
            assert row["establishing_correction_id"] is None
            assert row["declared_companions"] == expected
            assert row["companions_admitted"] == expected
            assert row["has_created_at"] is True
            assert row["has_updated_at"] is True

        manual = by_guest[guest_ids["manual"]]
        assert manual["primary_attendance_id"] is None
        assert manual["establishing_correction_id"] == seeded["manual_correction_id"]
        assert manual["declared_companions"] == 1
        assert manual["companions_admitted"] == 1

        assert len(by_guest) == 4
        attempts = connection.execute(
            text(
                """
                SELECT id, request_id, arrival_mode, requested_declared_companions,
                       primary_admitted_this_scan, companions_admitted_this_scan,
                       declared_companions_after, companions_admitted_after, outcome
                  FROM gate_scan_attempts
                """
            )
        ).mappings()
        by_attempt = {row["id"]: row for row in attempts}
        for name, expected in (("zero", 0), ("one", 1), ("two", 2)):
            row = by_attempt[admitted_attempt_ids[name]]
            assert row["request_id"] == row["id"]
            assert row["arrival_mode"] == "PRIMARY"
            assert row["requested_declared_companions"] is None
            assert row["primary_admitted_this_scan"] is True
            assert row["companions_admitted_this_scan"] == expected
            assert row["declared_companions_after"] == expected
            assert row["companions_admitted_after"] == expected
        for row in by_attempt.values():
            if row["outcome"] != "ADMITTED":
                assert row["request_id"] == row["id"]
                assert row["arrival_mode"] == "PRIMARY"
                assert row["primary_admitted_this_scan"] is False
                assert row["companions_admitted_this_scan"] is None
                assert row["declared_companions_after"] is None
                assert row["companions_admitted_after"] is None


def _assert_constraints_and_primary_index(engine, seeded: dict[str, object]) -> None:
    guest_ids = seeded["guest_ids"]
    attendance_ids = seeded["attendance_ids"]
    assert isinstance(guest_ids, dict)
    assert isinstance(attendance_ids, dict)

    with engine.begin() as connection:
        entitlement_id = connection.execute(
            text(
                """
                SELECT id FROM guest_companion_entitlements
                 WHERE event_id = :event_id AND guest_id = :guest_id
                """
            ),
            {"event_id": seeded["event_id"], "guest_id": guest_ids["one"]},
        ).scalar_one()
        for declared, admitted in ((0, 0), (1, 0), (1, 1), (2, 0), (2, 1), (2, 2)):
            savepoint = connection.begin_nested()
            connection.execute(
                text(
                    """
                    UPDATE guest_companion_entitlements
                       SET declared_companions = :declared,
                           companions_admitted = :admitted
                     WHERE id = :id
                    """
                ),
                {"id": entitlement_id, "declared": declared, "admitted": admitted},
            )
            savepoint.rollback()

        for declared, admitted in ((-1, 0), (1, -1), (1, 2), (3, 0)):
            _expect_integrity_error(
                connection,
                """
                UPDATE guest_companion_entitlements
                   SET declared_companions = :declared,
                       companions_admitted = :admitted
                 WHERE id = :id
                """,
                {"id": entitlement_id, "declared": declared, "admitted": admitted},
            )

        _expect_integrity_error(
            connection,
            """
            UPDATE guest_companion_entitlements
               SET primary_attendance_id = NULL, establishing_correction_id = NULL
             WHERE id = :id
            """,
            {"id": entitlement_id},
        )
        _expect_integrity_error(
            connection,
            """
            UPDATE guest_companion_entitlements
               SET establishing_correction_id = :correction_id
             WHERE id = :id
            """,
            {"id": entitlement_id, "correction_id": seeded["manual_correction_id"]},
        )
        _expect_integrity_error(
            connection,
            """
            INSERT INTO guest_companion_entitlements (
                id, event_id, guest_id, establishing_correction_id,
                declared_companions, companions_admitted
            ) VALUES (
                :id, :event_id, :guest_id, :correction_id, 1, 1
            )
            """,
            {
                "id": uuid4(),
                "event_id": seeded["event_id"],
                "guest_id": guest_ids["none"],
                "correction_id": seeded["manual_correction_id"],
            },
        )
        _expect_integrity_error(
            connection,
            """
            INSERT INTO guest_companion_entitlements (
                id, event_id, guest_id, primary_attendance_id,
                declared_companions, companions_admitted
            ) VALUES (
                :id, :event_id, :guest_id, :attendance_id, 1, 1
            )
            """,
            {
                "id": uuid4(),
                "event_id": seeded["event_id"],
                "guest_id": guest_ids["one"],
                "attendance_id": attendance_ids["one"],
            },
        )
        _expect_integrity_error(
            connection,
            """
            UPDATE guest_companion_entitlements
               SET event_id = :other_event_id
             WHERE id = :id
            """,
            {"id": entitlement_id, "other_event_id": seeded["other_event_id"]},
        )

        admitted_attempt_id = seeded["admitted_attempt_ids"]["one"]
        _expect_integrity_error(
            connection,
            """
            INSERT INTO gate_scan_attempts (
                id, event_id, request_id, arrival_mode, credential_fingerprint,
                guest_id, qr_credential_id, attendance_id, outcome,
                requested_companions, scanned_by
            )
            SELECT
                :id, event_id, request_id, 'PRIMARY', :fingerprint,
                guest_id, qr_credential_id, attendance_id, 'ALREADY_CHECKED_IN',
                0, scanned_by
              FROM gate_scan_attempts WHERE id = :attempt_id
            """,
            {
                "id": uuid4(),
                "fingerprint": f"{401:064x}",
                "attempt_id": admitted_attempt_id,
            },
        )
        _expect_integrity_error(
            connection,
            """
            INSERT INTO gate_scan_attempts (
                id, event_id, request_id, arrival_mode, credential_fingerprint,
                guest_id, qr_credential_id, attendance_id, outcome,
                requested_companions, declared_companions_after,
                companions_admitted_after, scanned_by
            )
            SELECT
                :id, event_id, :request_id, 'PRIMARY', :fingerprint,
                guest_id, qr_credential_id, attendance_id, 'ALREADY_CHECKED_IN',
                0, 1, 2, scanned_by
              FROM gate_scan_attempts WHERE id = :attempt_id
            """,
            {
                "id": uuid4(),
                "request_id": uuid4(),
                "fingerprint": f"{402:064x}",
                "attempt_id": admitted_attempt_id,
            },
        )

        savepoint = connection.begin_nested()
        connection.execute(
            text(
                """
                INSERT INTO gate_scan_attempts (
                    id, event_id, request_id, arrival_mode, credential_fingerprint,
                    guest_id, qr_credential_id, attendance_id, outcome,
                    requested_companions, primary_admitted_this_scan,
                    companions_admitted_this_scan, declared_companions_after,
                    companions_admitted_after, scanned_by
                )
                SELECT
                    :id, event_id, :request_id, 'COMPANION', :fingerprint,
                    guest_id, qr_credential_id, attendance_id, 'ADMITTED',
                    1, false, 1, 2, 2, scanned_by
                  FROM gate_scan_attempts WHERE id = :attempt_id
                """
            ),
            {
                "id": uuid4(),
                "request_id": uuid4(),
                "fingerprint": f"{403:064x}",
                "attempt_id": admitted_attempt_id,
            },
        )
        savepoint.rollback()


def _assert_orm_entitlement_creation(engine, seeded: dict[str, object]) -> None:
    guest_ids = seeded["guest_ids"]
    assert isinstance(guest_ids, dict)
    correction_id = uuid4()
    with Session(engine, expire_on_commit=False) as session:
        session.execute(
            text(
                """
                INSERT INTO guest_attendance_corrections (
                    id, event_id, guest_id, revision_number, previous_correction_id,
                    decision, reason_code, reason_note, companions_admitted,
                    gate_attendance_record_id, previous_effective_status,
                    resulting_effective_status, resulting_effective_source,
                    corrected_by_user_id
                ) VALUES (
                    :id, :event_id, :guest_id, 1, NULL,
                    'SET_ATTENDED', 'MISSED_GATE_SCAN', NULL, 0,
                    NULL, 'NOT_ATTENDED', 'ATTENDED', 'MANUAL', :administrator_id
                )
                """
            ),
            {
                "id": correction_id,
                "event_id": seeded["event_id"],
                "guest_id": guest_ids["none"],
                "administrator_id": seeded["administrator_id"],
            },
        )
        entitlement = GuestCompanionEntitlement(
            id=uuid4(),
            event_id=seeded["event_id"],
            guest_id=guest_ids["none"],
            primary_attendance_id=None,
            establishing_correction_id=correction_id,
            declared_companions=0,
        )
        session.add(entitlement)
        session.flush()

        assert entitlement.companions_admitted == 0
        assert entitlement.created_at is not None
        assert entitlement.updated_at is not None
        session.rollback()


def test_c22_migration_lifecycle_and_persistence_contract(
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
        inspector = inspect(engine)
        assert "guest_companion_entitlements" in inspector.get_table_names()
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("guest_companion_entitlements")
        } >= {
            "ck_guest_companion_entitlements_declared_bounded",
            "ck_guest_companion_entitlements_admitted_bounded",
            "ck_guest_companion_entitlements_origin_xor",
        }

        assert disposable_alembic.main(["downgrade", "base"]) == 0
        assert disposable_alembic.main(["upgrade", PREVIOUS_HEAD]) == 0
        seeded = _seed_legacy_evidence(engine)
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0
        _assert_backfill(engine, seeded)
        _assert_constraints_and_primary_index(engine, seeded)
        _assert_orm_entitlement_creation(engine, seeded)

        assert disposable_alembic.main(["downgrade", PREVIOUS_HEAD]) == 0
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (PREVIOUS_HEAD)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM guest_attendance_records")
                ).scalar_one()
                == 3
            )
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        _assert_backfill(engine, seeded)

        guest_ids = seeded["guest_ids"]
        assert isinstance(guest_ids, dict)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE guest_companion_entitlements
                       SET declared_companions = 2, companions_admitted = 1
                     WHERE event_id = :event_id AND guest_id = :guest_id
                    """
                ),
                {"event_id": seeded["event_id"], "guest_id": guest_ids["one"]},
            )
        with pytest.raises(SQLAlchemyError, match="not legacy-representable"):
            disposable_alembic.main(["downgrade", PREVIOUS_HEAD])
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (CURRENT_HEAD)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE guest_companion_entitlements
                       SET declared_companions = 1, companions_admitted = 1
                     WHERE event_id = :event_id AND guest_id = :guest_id
                    """
                ),
                {"event_id": seeded["event_id"], "guest_id": guest_ids["one"]},
            )
    finally:
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
