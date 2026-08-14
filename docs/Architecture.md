# Architecture

## 1. System shape

```text
Flutter mobile app (later)
        │ HTTPS / JSON / multipart
        ▼
FastAPI backend
        │
        ├── PostgreSQL
        ├── private file storage
        └── deterministic rendering / Excel / QR services
```

The backend is the authoritative source for identity, event policies, guest state, import validation, QR credentials, card design selection, generated-card records, attendance, corrections, and reports.

## 2. Backend modules

Recommended bounded modules:

```text
auth
events
guest_fields
guests
imports
templates
card_designs
photos
qr
cards
attendance
reports
audit
storage
health
```

Routes must remain thin. Business logic belongs in services. Persistence belongs in repositories. Renderers/parsers should be deterministic pure components wherever practical.

## 3. Database

PostgreSQL is the single relational database platform for this rebuild.

Separate databases:

- `wedding_ecard_dev`
- disposable test databases with explicit unique names
- `wedding_ecard_staging`
- production database later

Never let test configuration fall back to staging.

Use SQLAlchemy 2 and Alembic.

## 4. Storage

Private local storage during backend development and staging.

Logical roots:

```text
private/templates/
private/event-photos/
private/generated-cards/
private/reports/
private/import-evidence/
```

Production may later use S3-compatible object storage behind the same storage interfaces.

## 5. Authentication

MVP:

- persisted administrator account
- Argon2id password hashing
- short-lived JWT access token
- strict issuer/audience validation
- server reloads persisted active user on protected requests
- `SUPER_ADMIN` authorization for administrative workflows

Do not expose future inactive roles in the mobile MVP.

## 6. Excel import architecture

The import pipeline is staged and immutable enough for auditability:

```text
upload bytes
→ safe XLSX package validation
→ sheet/header detection
→ deterministic field mapping
→ structural row classification
→ lossless normalization
→ row validation
→ duplicate analysis
→ ImportBatch + ImportBatchRow persistence
→ preview
→ confirmation of eligible rows
→ correction/revalidation of invalid staged rows
→ later confirmation of newly eligible rows
```

Important principles:

- no formula execution
- no fuzzy silent guessing
- malformed guest rows stay visible
- presentation/footer rows are ignored only with strong structural evidence
- batch snapshots preserve the event guest-field and policy context used for validation
- valid rows may proceed even when other rows are invalid
- corrections never mutate the original uploaded workbook

## 7. Companion policy

System default:

```text
max_companions_per_invitation = 2
```

Each guest has `allowed_companions` within `0..event.max_companions_per_invitation`.

Event-specific changes are explicit administrator policy changes, never inferred from spreadsheet data.

## 8. Template/card architecture

Templates are immutable versioned packages.

```text
TemplatePreset
→ TemplatePresetVersion
→ EventCardDesign
→ EventCardDesignRevision
→ EventCardDesignApproval
→ GeneratedGuestCard
```

The renderer reads a validated manifest and generic layer definitions.

Supported layer concepts include:

- text
- static asset
- photo
- QR
- shape
- line

Template-specific asset names, coordinates, colors, fonts, or composition logic must not be hardcoded in renderer branches.

## 9. QR architecture

For each guest/event credential:

1. generate cryptographically secure opaque token;
2. return raw token only at issuance/render boundary;
3. store secure hash at rest;
4. bind to event and guest;
5. support active/inactive/reissue lifecycle;
6. scanning hashes submitted token and looks up the credential safely.

QR content contains no guest PII.

## 10. Permanent generated-card architecture

A permanent generated card must pin:

- event
- guest
- approved design revision
- template version
- canonical event photo selection/revision inputs
- real QR credential identity
- deterministic rendered artifact digest
- storage reference
- generation timestamp

Generation must be idempotent under an explicit version/input identity contract.

## 11. Attendance architecture

Gate flow:

```text
scan token
→ credential validation
→ event match
→ guest eligibility
→ duplicate attendance check
→ companion-admission validation
→ immutable gate evidence
→ effective attendance state
```

Corrections are explicit append-only decisions; reports derive effective state from gate evidence plus corrections.

## 12. Reports

JSON/CSV/XLSX reports derive from authoritative event/guest/attendance state.

Keep named invitation-holder counts distinct from companion counts and total people counts.

## 13. API versioning

Base prefix:

```text
/api/v1
```

No breaking route/schema change without an explicit architecture decision.

## 14. Observability and privacy

- safe structured logs
- correlation/request IDs where useful
- no secrets, raw QR credentials, spreadsheet row contents, photo bytes, or guest PII in routine logs
- bounded public error messages

## 15. Backend completion boundary

Do not start Flutter implementation until full staging acceptance and `main` promotion are complete.
