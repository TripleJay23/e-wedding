# Architecture

This is the implemented backend architecture for the e-Wedding platform.

## System shape

```text
Future Flutter client
        | HTTPS / JSON / multipart
        v
FastAPI application
        |
        +-- PostgreSQL 17
        +-- private file storage
        +-- deterministic Excel, QR, image, and report components
```

The backend is authoritative for administrator identity, event policy, guests,
import validation, QR lifecycle, card designs and artifacts, gate evidence,
attendance corrections, and reports.

## Application boundaries

`backend/app` separates:

- `api/routes` and `schemas`: authenticated HTTP contracts and safe responses;
- `services`: transaction ownership and domain rules;
- `repositories`: bounded PostgreSQL queries and locking;
- `models`: SQLAlchemy persistence contracts and database-backed invariants;
- `imports`, `qr`, `cards`, and `reports`: deterministic specialized components;
- `cli`: guarded development, disposable, and staging operators;
- `core` and `db`: fail-closed settings, security, identity, and runtime safety.

Routes remain thin. Services own mutations and transaction decisions. Repositories
keep event scoping and query bounds explicit. Parsers and renderers do not make
network calls or invoke AI/LLM services.

## PostgreSQL and migrations

PostgreSQL is the sole relational platform. The protected identities
`wedding_ecard_dev`, unique `wedding_ecard_disposable_*` databases, and
`wedding_ecard_staging` remain isolated. Guarded operators verify configured and
connected identity before migrations. Alembic has one linear history through
`20260906_0013`; it was built cleanly and does not inherit another repository's
migration chain.

## Private storage

Server-controlled roots contain import evidence, verified templates, canonical
event photos, QR images, and generated cards. Paths are derived from internal UUIDs
or digests, validated against their configured root, and never returned by HTTP
metadata schemas. Staging and production require absolute child roots inside the
configured private root.

## Authentication

The MVP persists one active `SUPER_ADMIN`. Passwords use Argon2id. Short-lived
HS256 access tokens require strict subject, issuer, audience, issued-at, and expiry
claims; protected requests reload the persisted active administrator.

## Excel import

```text
bounded XLSX envelope/package validation
-> visible-sheet and bounded header-candidate discovery
-> deterministic semantic header mapping and candidate viability selection
-> row-by-row structural classification -> field-specific normalization
-> policy validation and duplicate analysis -> persisted batch/staged evidence
-> bounded preview -> correction/revalidation -> partial or exact progression
-> reconciliation
```

Formulas are inspected as formulas and never executed. Structural rows require
strong contextual evidence; malformed and uncertain post-header rows remain
visible. The database retains an immutable normalized original canonical snapshot,
current corrected canonical values, source-row association, and revision history.
The protected original XLSX—not staged canonical JSON—preserves complete source
evidence such as display formatting, outer whitespace, original email casing, and
formula/error cell characteristics. Selection and parsing make no network or AI
calls.

## Templates and permanent cards

Template packages are immutable versions verified by package, logical, manifest,
and preview digests. The renderer consumes a validated generic manifest and has no
template-key conditional rendering path. Event photos are canonicalized and
metadata-free. Design revisions and approvals are append-only identities.

Permanent generated cards pin the event, guest, design revision, approval,
template version, photo controls, active QR credential, renderer contract, render
input digest, storage reference, and artifact digest. Identical current inputs are
idempotent.

## QR lifecycle

Issuance creates a high-entropy opaque value, renders it to protected QR bytes,
stores only the lowercase SHA-256 verifier and protected image metadata, and then
discards the raw value. Credentials are event/guest bound and support active,
inactive, and superseded states. Scanning hashes the submitted value; management
APIs expose metadata only.

## Companion entitlement and attendance

The configured invitation maximum and the event ceiling bound what may be declared;
the product maximum is currently 2. `Guest.allowed_companions` is configured
capacity, `GuestCompanionEntitlement.declared_companions` is the locked total
entitlement, and `GuestCompanionEntitlement.companions_admitted` is the mutable
effective cumulative consumption. Remaining declared slots are `declared -
admitted`. Allowed, declared, and admitted are deliberately different concepts;
allowed capacity is not expected attendance.

```text
configured invitation policy
-> PRIMARY establishes and locks declaration, admitting 0..declared companions
-> later COMPANION arrivals consume remaining declared slots
-> append-only corrections may change current effective entitlement within evidence
-> one bounded reporting projection reconciles raw and effective state
```

PRIMARY must establish entitlement before COMPANION admission. A declaration of
zero is valid, exhausts companion entitlement, and leaves the QR valid. Repeated
PRIMARY admission cannot alter the declaration. One primary invitation owns the
lifecycle; companions do not receive `Guest` rows, individual QR credentials,
individual cards, or fabricated attendance-holder identities.

`GuestAttendanceRecord` is immutable primary gate evidence. `GateScanAttempt` is
immutable admission-attempt and transition evidence.
`GuestCompanionEntitlement` is mutable current entitlement state, and
`GuestAttendanceCorrection` is the append-only administrative audit chain. A gate
origin has `primary_attendance_id` and no establishing correction; a correction
origin has `establishing_correction_id` and no primary attendance ID. Both are
legitimate. Current-state correction never rewrites historical gate evidence.

Admission locks the guest and existing entitlement in a consistent order. The
first PRIMARY serializes declaration establishment, the last companion slot cannot
oversubscribe, correction and live admission serialize coherently, and identical
request replay consumes capacity once. Primary attendance, entitlement, and attempt
commit atomically; companion entitlement and its attempt also commit atomically.
These are PostgreSQL transaction and row-lock guarantees, not distributed locks.

## Corrections and evidence ceiling

Normal scanning cannot alter an established declaration. Administrative
`SET_COMPANION_STATE` may change effective declaration and admitted counts only
within `0 <= admitted <= declared <= allowed`. Previous values are captured from
the locked entitlement on the server. Effective admitted companions may be reduced
below immutable evidence and later restored, but never above the evidenced ceiling.
For gate-origin state, that ceiling is the sum of successful immutable `ADMITTED`
companion deltas. For correction-origin state, it is the establishing
`SET_ATTENDED` companion evidence plus later successful companion-scan deltas;
primary attendance is not double-counted.

`SET_ATTENDED` can establish correction-origin attendance and entitlement using
its deterministic legacy companion count. `SET_NOT_ATTENDED` makes the primary
effectively absent but retains entitlement, so later companion admission remains
blocked. `RESTORE_GATE_DERIVED` restores valid gate state when raw gate attendance
exists; without gate attendance it restores the pre-gate absent state and does not
invent gate evidence.

## Reports

Reports use one read-only bounded dataset for JSON, CSV, and XLSX. They keep
configured companion capacity, declared companions, effective admitted companions,
raw admitted companions, remaining declared slots, released allowance, uncommitted
allowance, and effective total people distinct. Released allowance is `allowed -
declared` only after entitlement exists; an unestablished invitation contributes
allowed capacity to uncommitted allowance instead. `remaining_people_capacity`
remains a population-capacity metric, not remaining declared companion slots.

Aggregate identities are de-identified. Authenticated administrator guest-level
reports may expose limited operational identity (guest ID, name, invitation type,
table, and category) with attendance state. They exclude contact data, credential
material, correction reason/actor data, internal entitlement/provenance IDs, and
private paths.

Reconciliation preserves:

- declared = effective admitted + remaining declared slots;
- allowed capacity = declared + released + uncommitted;
- effective total people = effective primary attendance + effective companions.

Raw companion admissions can differ from effective admitted companions after a
correction.

## Versioning and privacy

The HTTP base remains `/api/v1`; breaking contract changes require an explicit
architecture decision. Routine logs and public errors exclude secrets, raw QR
values, workbook rows, photo bytes, PII, SQL details, and private paths.
