# Database Contract

PostgreSQL is authoritative.

This document lists the core domain shape. Exact columns/constraints are finalized milestone by milestone and reconciled after staging acceptance.

## Core entities

### users

- id
- username/email identity
- password_hash
- role
- is_active
- created_at / updated_at

MVP authenticating role: `SUPER_ADMIN`.

### events

- id
- event_name
- bride_name
- groom_name
- event_date/time
- venue/address/description as supported
- status
- `max_companions_per_invitation` — non-negative integer, **default 2**
- created_by
- timestamps

### event_guest_fields

Canonical initial fields:

```text
full_name
phone_number
email
invitation_type
allowed_companions
table_number
category
```

Each event stores enabled/required rules.

### guests

- id
- event_id
- full_name required
- phone_number optional
- email optional
- invitation_type required
- allowed_companions non-negative and not above event policy
- table_number optional
- category optional
- status
- timestamps

### import_batches

Stores immutable upload/validation context:

- event
- workbook digest/metadata (privacy-safe)
- detected sheet/header metadata
- mapping snapshot
- guest-field snapshot
- event companion-policy snapshot
- status
- timestamps

### import_batch_rows

Stores normalized staged guest candidates:

- batch + source row number
- canonical normalized fields
- validation state
- structured error codes
- duplicate state
- correction/version state
- eligibility state

Do not store executed formula results.

### import_row_corrections / revision history

Required for in-app correction workflow:

- row reference
- changed canonical values
- actor
- revision/configuration version
- validation result
- timestamp

Original imported row snapshot remains auditable.

### guest_qr_credentials

- id
- event_id
- guest_id
- token_hash
- protected QR image storage key and image digest
- active / inactive / superseded state
- issuance/reissue metadata and lifecycle timestamps
- timestamps

Raw token is not stored. The storage key and token hash are internal and are not
part of the HTTP response contract.

### template_presets / template_preset_versions

Versioned immutable template catalogue and canonical package identity.

### event_photos

Canonicalized metadata-free event photo assets and primary selection.

### event_card_designs / revisions

Event-level design state, immutable revisions, explicit template version, name policies, and editable controls.

### event_card_design_approvals

Pins the exact approved current design revision for permanent generation.

### generated_guest_cards

- event
- guest
- approved design revision
- QR credential identity
- render-input identity/version
- storage key
- SHA-256
- generated_at
- status

### guest_attendance_records

Immutable gate-derived attendance evidence, including admitted companions.

### guest_attendance_corrections

Append-only authorized corrections used to compute effective attendance.

### audit_logs

Security-sensitive administrative actions where required.

## Critical constraints

- one guest belongs to one event
- guest companions >= 0 and <= event max
- event max companions >= 0, default 2
- one active QR credential per guest where lifecycle requires
- QR token hash unique
- one active template version per preset
- template versions immutable after verification/activation
- event design revisions immutable
- generated card identity unique under accepted idempotency key
- attendance duplicate prevention enforced transactionally
- correction decisions closed to supported enum values
