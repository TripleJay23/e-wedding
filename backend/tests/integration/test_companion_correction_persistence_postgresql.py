"""PostgreSQL 17 acceptance for C2.5A companion-correction persistence."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.cli import disposable_alembic
from app.core.settings import Settings

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
PREVIOUS_HEAD = "20260906_0012"
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
    parsed = make_url(DISPOSABLE_URL)
    assert parsed.database is not None
    assert parsed.database.startswith("wedding_ecard_disposable_")
    assert parsed.database != "wedding_ecard_staging"
    assert parsed.port != 55432

    private_root = tmp_path / "private"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv("IMPORT_EVIDENCE_STORAGE_ROOT", str(private_root / "imports"))
    monkeypatch.setenv("QR_IMAGE_STORAGE_ROOT", str(private_root / "qr"))
    return Settings()


def seed_scope(connection: Connection) -> tuple[UUID, UUID]:
    administrator_id = uuid4()
    event_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO users (id, singleton_key, username, password_hash, role, is_active)
            VALUES (:id, 1, 'synthetic-c25a-admin', '$argon2id$synthetic',
                    'SUPER_ADMIN', true)
            """
        ),
        {"id": administrator_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO events (
                id, event_name, bride_name, groom_name, event_datetime,
                venue, address, description, status,
                max_companions_per_invitation, created_by
            ) VALUES (
                :id, 'Synthetic C2.5A Event', 'Synthetic Bride', 'Synthetic Groom',
                :event_datetime, 'Synthetic Venue', NULL, NULL, 'ACTIVE', 2, :administrator_id
            )
            """
        ),
        {
            "id": event_id,
            "event_datetime": datetime(2027, 9, 6, 12, 0, tzinfo=UTC),
            "administrator_id": administrator_id,
        },
    )
    return administrator_id, event_id


def add_guest(connection: Connection, event_id: UUID, ordinal: int) -> UUID:
    guest_id = uuid4()
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
            "full_name": f"Synthetic C2.5A Guest {ordinal}",
        },
    )
    return guest_id


def add_gate_origin(
    connection: Connection,
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
    ordinal: int,
    declared: int,
    admitted: int,
) -> UUID:
    credential_id = uuid4()
    attendance_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO guest_qr_credentials (
                id, event_id, guest_id, token_hash, image_storage_key,
                image_sha256, status, replaces_credential_id, issued_by,
                deactivated_at, superseded_at
            ) VALUES (
                :id, :event_id, :guest_id, :token_hash, :storage_key,
                :image_sha256, 'ACTIVE', NULL, :administrator_id, NULL, NULL
            )
            """
        ),
        {
            "id": credential_id,
            "event_id": event_id,
            "guest_id": guest_id,
            "token_hash": f"{ordinal:064x}",
            "storage_key": f"{event_id.hex}/{guest_id.hex}/{credential_id.hex}.png",
            "image_sha256": f"{ordinal + 1000:064x}",
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
                :id, :event_id, :guest_id, :credential_id, :admitted, :administrator_id
            )
            """
        ),
        {
            "id": attendance_id,
            "event_id": event_id,
            "guest_id": guest_id,
            "credential_id": credential_id,
            "admitted": admitted,
            "administrator_id": administrator_id,
        },
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
                :id, :event_id, :fingerprint, :guest_id,
                :credential_id, :attendance_id, 'ADMITTED',
                :admitted, :request_id, 'PRIMARY',
                :declared, true, :admitted, :declared,
                :admitted, :administrator_id
            )
            """
        ),
        {
            "id": uuid4(),
            "event_id": event_id,
            "fingerprint": f"{ordinal + 2000:064x}",
            "guest_id": guest_id,
            "credential_id": credential_id,
            "attendance_id": attendance_id,
            "admitted": admitted,
            "request_id": uuid4(),
            "declared": declared,
            "administrator_id": administrator_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO guest_companion_entitlements (
                id, event_id, guest_id, primary_attendance_id,
                establishing_correction_id, declared_companions, companions_admitted
            ) VALUES (
                :id, :event_id, :guest_id, :attendance_id, NULL, :declared, :admitted
            )
            """
        ),
        {
            "id": uuid4(),
            "event_id": event_id,
            "guest_id": guest_id,
            "attendance_id": attendance_id,
            "declared": declared,
            "admitted": admitted,
        },
    )
    return attendance_id


def insert_correction(
    connection: Connection,
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
    revision: int,
    previous_correction_id: UUID | None,
    decision: str,
    legacy_companions: int | None,
    previous_status: str,
    resulting_status: str,
    resulting_source: str,
    gate_attendance_id: UUID | None = None,
    previous_declared: int | None = None,
    previous_admitted: int | None = None,
    resulting_declared: int | None = None,
    resulting_admitted: int | None = None,
) -> UUID:
    correction_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO guest_attendance_corrections (
                id, event_id, guest_id, revision_number, previous_correction_id,
                decision, reason_code, reason_note, companions_admitted,
                previous_declared_companions, previous_companions_admitted,
                resulting_declared_companions, resulting_companions_admitted,
                gate_attendance_record_id, previous_effective_status,
                resulting_effective_status, resulting_effective_source,
                corrected_by_user_id
            ) VALUES (
                :id, :event_id, :guest_id, :revision, :previous_correction_id,
                :decision, 'OPERATOR_ERROR', NULL, :legacy_companions,
                :previous_declared, :previous_admitted,
                :resulting_declared, :resulting_admitted,
                :gate_attendance_id, :previous_status,
                :resulting_status, :resulting_source, :administrator_id
            )
            """
        ),
        {
            "id": correction_id,
            "event_id": event_id,
            "guest_id": guest_id,
            "revision": revision,
            "previous_correction_id": previous_correction_id,
            "decision": decision,
            "legacy_companions": legacy_companions,
            "previous_declared": previous_declared,
            "previous_admitted": previous_admitted,
            "resulting_declared": resulting_declared,
            "resulting_admitted": resulting_admitted,
            "gate_attendance_id": gate_attendance_id,
            "previous_status": previous_status,
            "resulting_status": resulting_status,
            "resulting_source": resulting_source,
            "administrator_id": administrator_id,
        },
    )
    return correction_id


def insert_legacy_chain(
    connection: Connection,
    *,
    event_id: UUID,
    guest_id: UUID,
    administrator_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    attended = insert_correction(
        connection,
        event_id=event_id,
        guest_id=guest_id,
        administrator_id=administrator_id,
        revision=1,
        previous_correction_id=None,
        decision="SET_ATTENDED",
        legacy_companions=1,
        previous_status="NOT_ATTENDED",
        resulting_status="ATTENDED",
        resulting_source="MANUAL",
    )
    not_attended = insert_correction(
        connection,
        event_id=event_id,
        guest_id=guest_id,
        administrator_id=administrator_id,
        revision=2,
        previous_correction_id=attended,
        decision="SET_NOT_ATTENDED",
        legacy_companions=None,
        previous_status="ATTENDED",
        resulting_status="NOT_ATTENDED",
        resulting_source="MANUAL",
    )
    restored = insert_correction(
        connection,
        event_id=event_id,
        guest_id=guest_id,
        administrator_id=administrator_id,
        revision=3,
        previous_correction_id=not_attended,
        decision="RESTORE_GATE_DERIVED",
        legacy_companions=None,
        previous_status="NOT_ATTENDED",
        resulting_status="NOT_ATTENDED",
        resulting_source="NONE",
    )
    return attended, not_attended, restored


VALID_TRANSITIONS = (
    (0, 0, 1, 0),
    (0, 0, 2, 0),
    (0, 0, 2, 1),
    (0, 0, 2, 2),
    (1, 0, 0, 0),
    (1, 1, 1, 0),
    (2, 0, 1, 0),
    (2, 1, 1, 1),
    (2, 2, 2, 1),
    (2, 2, 1, 1),
    (2, 1, 2, 2),
)

INVALID_TRANSITIONS = (
    (None, 0, 1, 0),
    (0, None, 1, 0),
    (0, 0, None, 0),
    (0, 0, 1, None),
    (-1, 0, 1, 0),
    (1, -1, 1, 0),
    (1, 2, 1, 0),
    (3, 0, 1, 0),
    (1, 0, -1, 0),
    (1, 0, 1, -1),
    (1, 0, 1, 2),
    (1, 0, 3, 0),
)


def test_c25a_companion_correction_persistence_lifecycle(
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
            database, version, port, revision = connection.execute(
                text(
                    "SELECT current_database(), current_setting('server_version_num'), "
                    "inet_server_port(), (SELECT version_num FROM alembic_version)"
                )
            ).one()
            assert database.startswith("wedding_ecard_disposable_")
            assert version.startswith("17")
            assert port != 55432
            assert revision == CURRENT_HEAD

        with engine.begin() as connection:
            administrator_id, event_id = seed_scope(connection)
            legacy_guest_id = add_guest(connection, event_id, 1)
            insert_legacy_chain(
                connection,
                event_id=event_id,
                guest_id=legacy_guest_id,
                administrator_id=administrator_id,
            )

        assert disposable_alembic.main(["downgrade", PREVIOUS_HEAD]) == 0
        assert disposable_alembic.main(["upgrade", "head"]) == 0
        assert disposable_alembic.main(["check"]) == 0
        with engine.connect() as connection:
            legacy_snapshots = connection.execute(
                text(
                    """
                    SELECT previous_declared_companions, previous_companions_admitted,
                           resulting_declared_companions, resulting_companions_admitted
                      FROM guest_attendance_corrections
                     WHERE guest_id = :guest_id
                     ORDER BY revision_number
                    """
                ),
                {"guest_id": legacy_guest_id},
            ).all()
            assert legacy_snapshots == [(None, None, None, None)] * 3

        accepted_ids: list[UUID] = []
        with engine.begin() as connection:
            for ordinal, transition in enumerate(VALID_TRANSITIONS, start=10):
                guest_id = add_guest(connection, event_id, ordinal)
                accepted_ids.append(
                    insert_correction(
                        connection,
                        event_id=event_id,
                        guest_id=guest_id,
                        administrator_id=administrator_id,
                        revision=1,
                        previous_correction_id=None,
                        decision="SET_COMPANION_STATE",
                        legacy_companions=None,
                        previous_status="NOT_ATTENDED",
                        resulting_status="NOT_ATTENDED",
                        resulting_source="NONE",
                        previous_declared=transition[0],
                        previous_admitted=transition[1],
                        resulting_declared=transition[2],
                        resulting_admitted=transition[3],
                    )
                )

            correction_guest_id = add_guest(connection, event_id, 100)
            establishing_id = insert_correction(
                connection,
                event_id=event_id,
                guest_id=correction_guest_id,
                administrator_id=administrator_id,
                revision=1,
                previous_correction_id=None,
                decision="SET_ATTENDED",
                legacy_companions=0,
                previous_status="NOT_ATTENDED",
                resulting_status="ATTENDED",
                resulting_source="MANUAL",
            )
            connection.execute(
                text(
                    """
                    INSERT INTO guest_companion_entitlements (
                        id, event_id, guest_id, primary_attendance_id,
                        establishing_correction_id, declared_companions, companions_admitted
                    ) VALUES (:id, :event_id, :guest_id, NULL, :correction_id, 0, 0)
                    """
                ),
                {
                    "id": uuid4(),
                    "event_id": event_id,
                    "guest_id": correction_guest_id,
                    "correction_id": establishing_id,
                },
            )
            correction_origin_id = insert_correction(
                connection,
                event_id=event_id,
                guest_id=correction_guest_id,
                administrator_id=administrator_id,
                revision=2,
                previous_correction_id=establishing_id,
                decision="SET_COMPANION_STATE",
                legacy_companions=None,
                previous_status="ATTENDED",
                resulting_status="ATTENDED",
                resulting_source="MANUAL",
                previous_declared=0,
                previous_admitted=0,
                resulting_declared=1,
                resulting_admitted=0,
            )

            gate_guest_id = add_guest(connection, event_id, 101)
            gate_attendance_id = add_gate_origin(
                connection,
                event_id=event_id,
                guest_id=gate_guest_id,
                administrator_id=administrator_id,
                ordinal=101,
                declared=2,
                admitted=1,
            )
            insert_correction(
                connection,
                event_id=event_id,
                guest_id=gate_guest_id,
                administrator_id=administrator_id,
                revision=1,
                previous_correction_id=None,
                decision="SET_COMPANION_STATE",
                legacy_companions=None,
                previous_status="ATTENDED",
                resulting_status="ATTENDED",
                resulting_source="GATE",
                gate_attendance_id=gate_attendance_id,
                previous_declared=2,
                previous_admitted=1,
                resulting_declared=1,
                resulting_admitted=1,
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE guest_attendance_corrections "
                    "SET resulting_declared_companions = 2 WHERE id = :id"
                ),
                {"id": correction_origin_id},
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("DELETE FROM guest_attendance_corrections WHERE id = :id"),
                {"id": correction_origin_id},
            )

        for ordinal, transition in enumerate(INVALID_TRANSITIONS, start=200):
            with engine.begin() as connection:
                guest_id = add_guest(connection, event_id, ordinal)
            with pytest.raises(IntegrityError), engine.begin() as connection:
                insert_correction(
                    connection,
                    event_id=event_id,
                    guest_id=guest_id,
                    administrator_id=administrator_id,
                    revision=1,
                    previous_correction_id=None,
                    decision="SET_COMPANION_STATE",
                    legacy_companions=None,
                    previous_status="NOT_ATTENDED",
                    resulting_status="NOT_ATTENDED",
                    resulting_source="NONE",
                    previous_declared=transition[0],
                    previous_admitted=transition[1],
                    resulting_declared=transition[2],
                    resulting_admitted=transition[3],
                )

        with engine.begin() as connection:
            invalid_legacy_guest = add_guest(connection, event_id, 300)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            insert_correction(
                connection,
                event_id=event_id,
                guest_id=invalid_legacy_guest,
                administrator_id=administrator_id,
                revision=1,
                previous_correction_id=None,
                decision="SET_COMPANION_STATE",
                legacy_companions=1,
                previous_status="NOT_ATTENDED",
                resulting_status="NOT_ATTENDED",
                resulting_source="NONE",
                previous_declared=1,
                previous_admitted=0,
                resulting_declared=1,
                resulting_admitted=1,
            )

        with engine.begin() as connection:
            leaked_legacy_guest = add_guest(connection, event_id, 301)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            insert_correction(
                connection,
                event_id=event_id,
                guest_id=leaked_legacy_guest,
                administrator_id=administrator_id,
                revision=1,
                previous_correction_id=None,
                decision="SET_ATTENDED",
                legacy_companions=1,
                previous_status="NOT_ATTENDED",
                resulting_status="ATTENDED",
                resulting_source="MANUAL",
                previous_declared=0,
                previous_admitted=0,
                resulting_declared=1,
                resulting_admitted=1,
            )

        assert len(accepted_ids) == len(VALID_TRANSITIONS)
        with pytest.raises(SQLAlchemyError, match="not 0012-representable"):
            disposable_alembic.main(["downgrade", PREVIOUS_HEAD])
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == CURRENT_HEAD
            )
    finally:
        if inspect(engine).has_table("users"):
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE TABLE users CASCADE"))
        engine.dispose()
        assert disposable_alembic.main(["downgrade", "base"]) == 0
