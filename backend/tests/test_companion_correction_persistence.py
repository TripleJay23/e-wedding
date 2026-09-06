"""C2.5A structured companion-correction persistence metadata tests."""

from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, SmallInteger

from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionPersistenceError,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
    _validate_correction_insert,
)
from app.schemas.attendance_corrections import AttendanceCorrectionRequest

SNAPSHOT_FIELDS = (
    "previous_declared_companions",
    "previous_companions_admitted",
    "resulting_declared_companions",
    "resulting_companions_admitted",
)


def make_correction(
    decision: AttendanceCorrectionDecision,
    *,
    companions_admitted: int | None = None,
    previous: tuple[int, int] | None = None,
    resulting: tuple[int, int] | None = None,
) -> GuestAttendanceCorrection:
    return GuestAttendanceCorrection(
        id=uuid4(),
        event_id=uuid4(),
        guest_id=uuid4(),
        revision_number=1,
        previous_correction_id=None,
        decision=decision,
        reason_code=AttendanceCorrectionReason.OPERATOR_ERROR,
        reason_note=None,
        companions_admitted=companions_admitted,
        previous_declared_companions=None if previous is None else previous[0],
        previous_companions_admitted=None if previous is None else previous[1],
        resulting_declared_companions=None if resulting is None else resulting[0],
        resulting_companions_admitted=None if resulting is None else resulting[1],
        gate_attendance_record_id=None,
        previous_effective_status=EffectiveAttendanceStatus.ATTENDED,
        resulting_effective_status=EffectiveAttendanceStatus.ATTENDED,
        resulting_effective_source=EffectiveAttendanceSource.MANUAL,
        corrected_by_user_id=uuid4(),
    )


def test_companion_decision_and_typed_snapshot_columns_are_metadata() -> None:
    assert AttendanceCorrectionDecision.SET_COMPANION_STATE.value == "SET_COMPANION_STATE"
    table = GuestAttendanceCorrection.__table__
    for field in SNAPSHOT_FIELDS:
        assert field in table.columns
        assert isinstance(table.c[field].type, SmallInteger)
        assert table.c[field].nullable is True
    assert "companions_admitted" in table.columns


def test_shared_public_enum_expands_without_exposing_snapshot_request_fields() -> None:
    schema = AttendanceCorrectionRequest.model_json_schema()
    assert "SET_COMPANION_STATE" in str(schema)
    assert not set(SNAPSHOT_FIELDS) & AttendanceCorrectionRequest.model_fields.keys()


def test_model_metadata_contains_snapshot_shape_and_bounds() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in GuestAttendanceCorrection.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    shape = checks["ck_guest_attendance_corrections_companion_snapshot_shape"]
    assert "decision = 'SET_COMPANION_STATE'" in shape
    assert all(field in shape for field in SNAPSHOT_FIELDS)

    previous = checks["ck_guest_attendance_corrections_previous_companion_state"]
    assert "previous_companions_admitted <= previous_declared_companions" in previous
    assert "previous_declared_companions <= 2" in previous

    resulting = checks["ck_guest_attendance_corrections_resulting_companion_state"]
    assert "resulting_companions_admitted <= resulting_declared_companions" in resulting
    assert "resulting_declared_companions <= 2" in resulting


@pytest.mark.parametrize(
    ("previous", "resulting"),
    [
        ((0, 0), (1, 0)),
        ((0, 0), (2, 0)),
        ((0, 0), (2, 1)),
        ((0, 0), (2, 2)),
        ((1, 0), (0, 0)),
        ((1, 1), (1, 0)),
        ((2, 0), (1, 0)),
        ((2, 1), (1, 1)),
        ((2, 2), (2, 1)),
        ((2, 2), (1, 1)),
        ((2, 1), (2, 2)),
    ],
)
def test_valid_companion_snapshot_matrix_is_representable(previous, resulting) -> None:
    correction = make_correction(
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        previous=previous,
        resulting=resulting,
    )
    _validate_correction_insert(None, None, correction)


@pytest.mark.parametrize(
    ("previous", "resulting"),
    [
        (None, (1, 0)),
        ((1, 0), None),
        ((-1, 0), (1, 0)),
        ((1, -1), (1, 0)),
        ((1, 2), (1, 0)),
        ((3, 0), (1, 0)),
        ((1, 0), (-1, 0)),
        ((1, 0), (1, -1)),
        ((1, 0), (1, 2)),
        ((1, 0), (3, 0)),
    ],
)
def test_incomplete_or_unbounded_companion_snapshots_are_rejected(previous, resulting) -> None:
    correction = make_correction(
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        previous=previous,
        resulting=resulting,
    )
    with pytest.raises(AttendanceCorrectionPersistenceError):
        _validate_correction_insert(None, None, correction)


def test_companion_decision_rejects_legacy_count() -> None:
    correction = make_correction(
        AttendanceCorrectionDecision.SET_COMPANION_STATE,
        companions_admitted=1,
        previous=(1, 0),
        resulting=(1, 1),
    )
    with pytest.raises(AttendanceCorrectionPersistenceError):
        _validate_correction_insert(None, None, correction)


@pytest.mark.parametrize(
    ("decision", "legacy_count"),
    [
        (AttendanceCorrectionDecision.SET_ATTENDED, 1),
        (AttendanceCorrectionDecision.SET_NOT_ATTENDED, None),
        (AttendanceCorrectionDecision.RESTORE_GATE_DERIVED, None),
    ],
)
def test_legacy_decisions_preserve_count_semantics_and_exclude_snapshots(
    decision,
    legacy_count,
) -> None:
    legacy = make_correction(decision, companions_admitted=legacy_count)
    _validate_correction_insert(None, None, legacy)

    leaked = make_correction(
        decision,
        companions_admitted=legacy_count,
        previous=(1, 0),
        resulting=(1, 0),
    )
    with pytest.raises(AttendanceCorrectionPersistenceError):
        _validate_correction_insert(None, None, leaked)
