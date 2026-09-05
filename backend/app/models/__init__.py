"""SQLAlchemy domain models."""

from app.models.administrator import Administrator, AdministratorRole
from app.models.attendance import (
    GateArrivalMode,
    GateScanAttempt,
    GateScanOutcome,
    GuestAttendanceRecord,
    GuestCompanionEntitlement,
)
from app.models.attendance_corrections import (
    AttendanceCorrectionDecision,
    AttendanceCorrectionReason,
    EffectiveAttendanceSource,
    EffectiveAttendanceStatus,
    GuestAttendanceCorrection,
)
from app.models.cards import (
    DesignApprovalStatus,
    EventCardDesign,
    EventCardDesignApproval,
    EventCardDesignRevision,
    EventPhoto,
    GeneratedCardStatus,
    GeneratedGuestCard,
    PhotoFit,
    TemplatePreset,
    TemplatePresetVersion,
    TemplateVersionStatus,
)
from app.models.event import Event, EventGuestField, EventStatus, GuestFieldKey
from app.models.guest import Guest, GuestStatus
from app.models.imports import (
    ImportBatch,
    ImportBatchRow,
    ImportBatchStatus,
    ImportRevisionKind,
    ImportRowRevision,
    ImportRowStatus,
)
from app.models.qr import GuestQrCredential, QrCredentialStatus

__all__ = [
    "Administrator",
    "AdministratorRole",
    "AttendanceCorrectionDecision",
    "AttendanceCorrectionReason",
    "DesignApprovalStatus",
    "EffectiveAttendanceSource",
    "EffectiveAttendanceStatus",
    "Event",
    "EventCardDesign",
    "EventCardDesignApproval",
    "EventCardDesignRevision",
    "EventGuestField",
    "EventPhoto",
    "EventStatus",
    "GateArrivalMode",
    "GateScanAttempt",
    "GateScanOutcome",
    "GeneratedCardStatus",
    "GeneratedGuestCard",
    "Guest",
    "GuestAttendanceCorrection",
    "GuestAttendanceRecord",
    "GuestCompanionEntitlement",
    "GuestFieldKey",
    "GuestQrCredential",
    "GuestStatus",
    "ImportBatch",
    "ImportBatchRow",
    "ImportBatchStatus",
    "ImportRevisionKind",
    "ImportRowRevision",
    "ImportRowStatus",
    "PhotoFit",
    "QrCredentialStatus",
    "TemplatePreset",
    "TemplatePresetVersion",
    "TemplateVersionStatus",
]
