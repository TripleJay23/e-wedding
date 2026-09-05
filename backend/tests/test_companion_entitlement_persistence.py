"""C2.2 model-level companion entitlement persistence contracts."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.models.attendance import (
    GateArrivalMode,
    GateScanAttempt,
    GuestAttendanceRecord,
    GuestCompanionEntitlement,
)


def test_entitlement_has_only_the_approved_current_state_fields() -> None:
    assert set(GuestCompanionEntitlement.__table__.columns.keys()) == {
        "id",
        "event_id",
        "guest_id",
        "primary_attendance_id",
        "establishing_correction_id",
        "declared_companions",
        "companions_admitted",
        "created_at",
        "updated_at",
    }


def test_entitlement_has_unique_invitation_and_row_local_bounds() -> None:
    table = GuestCompanionEntitlement.__table__
    unique_columns = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert frozenset({"event_id", "guest_id"}) in unique_columns
    assert (
        "declared_companions BETWEEN 0 AND 2"
        in checks["ck_guest_companion_entitlements_declared_bounded"]
    )
    assert "companions_admitted >= 0" in checks["ck_guest_companion_entitlements_admitted_bounded"]
    assert (
        "companions_admitted <= declared_companions"
        in checks["ck_guest_companion_entitlements_admitted_bounded"]
    )
    origin_check = checks["ck_guest_companion_entitlements_origin_xor"]
    assert "primary_attendance_id IS NOT NULL" in origin_check
    assert "establishing_correction_id IS NOT NULL" in origin_check


def test_entitlement_foreign_keys_enforce_event_guest_and_origin_ownership() -> None:
    constraints = {
        constraint.name: tuple(element.target_fullname for element in constraint.elements)
        for constraint in GuestCompanionEntitlement.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert constraints["fk_guest_companion_entitlements_guest_event"] == (
        "guests.id",
        "guests.event_id",
    )
    assert constraints["fk_guest_companion_entitlements_attendance_event_guest"] == (
        "guest_attendance_records.id",
        "guest_attendance_records.event_id",
        "guest_attendance_records.guest_id",
    )
    assert constraints["fk_guest_companion_entitlements_correction_event_guest"] == (
        "guest_attendance_corrections.id",
        "guest_attendance_corrections.event_id",
        "guest_attendance_corrections.guest_id",
    )


def test_scan_attempt_has_idempotency_arrival_and_transition_foundation() -> None:
    table = GateScanAttempt.__table__
    unique_columns = {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert frozenset({"event_id", "request_id"}) in unique_columns
    assert str(table.c.request_id.server_default.arg) == "gen_random_uuid()"
    assert str(table.c.arrival_mode.server_default.arg) == "'PRIMARY'"
    assert set(GateArrivalMode) == {GateArrivalMode.PRIMARY, GateArrivalMode.COMPANION}
    assert (
        "requested_declared_companions BETWEEN 0 AND 2"
        in checks["ck_gate_scan_attempts_requested_declaration_bounded"]
    )
    assert (
        "companions_admitted_this_scan BETWEEN 0 AND 2"
        in checks["ck_gate_scan_attempts_companion_delta_bounded"]
    )
    post_state = checks["ck_gate_scan_attempts_post_entitlement_state"]
    assert "companions_admitted_after <= declared_companions_after" in post_state
    assert "declared_companions_after <= 2" in post_state


def test_success_attempt_uniqueness_is_limited_to_primary_arrival() -> None:
    indexes = {
        index.name: index for index in GateScanAttempt.__table__.indexes if isinstance(index, Index)
    }
    primary_index = indexes["uq_gate_scan_attempts_one_primary_admitted_attendance"]

    assert primary_index.unique is True
    assert tuple(column.name for column in primary_index.columns) == ("attendance_id",)
    assert str(primary_index.dialect_options["postgresql"]["where"]) == (
        "outcome = 'ADMITTED' AND arrival_mode = 'PRIMARY'"
    )
    assert "uq_gate_scan_attempts_one_admitted_attendance" not in indexes


def test_legacy_primary_attendance_companion_evidence_is_preserved() -> None:
    assert "companions_admitted" in GuestAttendanceRecord.__table__.columns
    assert "declared_companions" not in GuestAttendanceRecord.__table__.columns
