"""Validated, bounded M4 Excel import API contracts."""

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.models.event import GuestFieldKey
from app.models.imports import ImportBatchStatus, ImportRowStatus

CorrectionText = Annotated[str, Field(max_length=320)]


class ImportValidationErrorResponse(BaseModel):
    """One actionable field error without the rejected raw value."""

    field: GuestFieldKey | None
    code: str
    message: str


class ImportReconciliationResponse(BaseModel):
    """Exact current partition of all discovered guest rows."""

    actual_guest_rows_discovered: int
    ignored_non_data_rows: int
    currently_valid_staged_rows: int
    currently_invalid_staged_rows: int
    duplicates: int
    pending_revalidation: int
    rows_already_imported: int
    newly_importable_rows: int
    remaining_unresolved_rows: int
    guests_created_from_batch: int
    overall_batch_state: ImportBatchStatus


class ImportBatchSummaryResponse(BaseModel):
    """Privacy-minimal import history item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    uploaded_by: UUID
    workbook_sha256: str
    workbook_byte_size: int
    detected_sheet_name: str
    header_row_number: int
    candidate_row_count: int
    ignored_row_count: int
    status: ImportBatchStatus
    created_at: datetime
    updated_at: datetime


class ImportBatchDetailResponse(ImportBatchSummaryResponse):
    """Detected structure, immutable policy snapshot, and reconciliation."""

    column_mapping_snapshot: dict[str, int]
    structure_snapshot: dict[str, Any]
    guest_field_snapshot: list[dict[str, Any]]
    max_companions_snapshot: int
    reconciliation: ImportReconciliationResponse


class ImportBatchListResponse(BaseModel):
    """Bounded deterministic event import history page."""

    items: list[ImportBatchSummaryResponse]
    limit: int
    offset: int


class ImportRowResponse(BaseModel):
    """Authorized staged-row values, errors, provenance, and current state."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    source_row_number: int
    original_values: dict[str, str | int | None]
    original_validation_errors: list[ImportValidationErrorResponse]
    current_values: dict[str, str | int | None]
    validation_errors: list[ImportValidationErrorResponse]
    status: ImportRowStatus
    row_version: int
    guest_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ImportRowListResponse(BaseModel):
    """Bounded deterministic staged-row page."""

    items: list[ImportRowResponse]
    limit: int
    offset: int


class ImportCorrectionValues(BaseModel):
    """Only seven canonical staged guest fields are correctable."""

    model_config = ConfigDict(extra="forbid")

    full_name: CorrectionText | None = None
    phone_number: CorrectionText | None = None
    email: CorrectionText | None = None
    invitation_type: CorrectionText | None = None
    allowed_companions: StrictInt | None = None
    table_number: CorrectionText | None = None
    category: CorrectionText | None = None

    @field_validator(
        "full_name",
        "phone_number",
        "email",
        "invitation_type",
        "table_number",
        "category",
    )
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("correction text contains control characters")
        return value

    @model_validator(mode="after")
    def require_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("at least one canonical guest field must be supplied")
        if "allowed_companions" in self.model_fields_set and self.allowed_companions is None:
            raise ValueError("allowed_companions may not be null")
        return self


class ImportRowCorrectionRequest(BaseModel):
    """Optimistically versioned in-system correction request."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    values: ImportCorrectionValues


class ImportProgressResponse(BaseModel):
    """Outcome of one idempotent currently-valid progression operation."""

    imported_rows: int
    invalidated_rows: int
    duplicate_rows: int
    reconciliation: ImportReconciliationResponse


class ImportRevalidationResponse(BaseModel):
    """Rows explicitly revalidated by a batch operation."""

    items: list[ImportRowResponse]
    reconciliation: ImportReconciliationResponse
