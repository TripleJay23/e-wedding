# API Contract

The implemented FastAPI surface is rooted at `/api/v1`. Except for health and
login, routes require the active persisted `SUPER_ADMIN` bearer identity.

## Route groups

### Health and authentication

- `GET /health`
- `POST /auth/login`
- `GET /auth/me`

### Events, guest fields, and guests

- create, list, get, and patch `/events` resources;
- get and replace `/events/{event_id}/guest-fields`;
- create, list, get, and patch `/events/{event_id}/guests` resources.

Lists are bounded and event scoped. Event lifecycle and the companion ceiling are
validated in the service and PostgreSQL layers.

### Excel imports

Under `/events/{event_id}/imports` the API supports upload, history, batch detail,
reconciliation, row listing, row correction, row and batch revalidation, and batch
progression.

Upload accepts only a bounded `.xlsx` envelope. Import history and staged-row
lists are paged; list page size is at most 100, so a 5,000-candidate workbook never
returns all staged rows in one response. Batch detail contains bounded aggregate,
mapping, policy, and reconciliation evidence rather than an embedded full row set.

`POST /events/{event_id}/imports/{batch_id}/progress` keeps its original batch-wide
behavior when called without a body. An optional non-empty, unique
`staged_row_ids` list selects exact persisted staged-row UUIDs. The complete
selection is preflighted before mutation: `VALID` rows progress, `IMPORTED` rows
are idempotent no-ops, invalid/foreign/stale selections reject atomically, and
unselected rows remain unchanged.

Correcting an unlinked `VALID`, `INVALID`, or `DUPLICATE` row requires its expected
version and moves it to pending revalidation. Imported or guest-linked rows cannot
be corrected through the staging surface. The normalized original canonical
snapshot, source row, current corrected values, and revision evidence remain
preserved. Full cell-level source evidence remains in the protected original
workbook.

### QR credentials

The API issues, reads, deactivates, activates, and regenerates a guest credential,
and lists bounded event credential metadata. Responses never expose the raw bearer
credential, verifier hash, private storage key, image bytes, or unnecessary PII.

### Templates, photos, designs, and cards

The API supports:

- registration and lookup of verified Wedding Floral V10 metadata;
- authenticated approved-preview access;
- event-photo upload, list, selection, and protected content access;
- design revision create/read/list, preview, approval, and revocation;
- single and bounded bulk permanent-card generation;
- generated-card list/metadata, protected PNG download, and selected ZIP export.

Permanent generation requires the approved current design, event photo, eligible
guest, and active real QR credential. Protected content responses use private,
non-cacheable semantics.

### Gate attendance and corrections

- `POST /attendance/inspect` reads current scanner state without reserving capacity,
  creating attendance/entitlement, or recording a `GateScanAttempt`;
- `POST /attendance/admit` revalidates and applies an explicit, transactional,
  client-idempotent `PRIMARY` or `COMPANION` command;
- legacy `POST /attendance/scan` remains supported for compatibility and is marked
  deprecated in OpenAPI; new clients use inspect followed by admit;
- event attendance, guest attendance, and scan-attempt reads are bounded;
- effective attendance is available by event and guest;
- correction creation/history/detail routes are event and guest scoped.

Inspection accepts only `event_id` and the decoded opaque `credential`. It returns
minimal scanner context, `inspection_state`, `permitted_actions`, primary state,
and allowed/declared/admitted/remaining companion values. Its closed states are
`READY_FOR_PRIMARY`, `READY_FOR_COMPANION`, `ENTITLEMENT_EXHAUSTED`,
`ADMISSION_LOCKED`, `EVENT_NOT_ACTIVE`, `UNKNOWN_CREDENTIAL`, `WRONG_EVENT`,
`INACTIVE_CREDENTIAL`, `SUPERSEDED_CREDENTIAL`, and `INELIGIBLE_GUEST`.

Admission additionally requires a caller-generated UUID `request_id`,
`arrival_mode`, and `companions_entering_now`. `PRIMARY` requires
`declared_companions` and may admit zero through that declaration immediately;
`COMPANION` forbids the declaration field and requires at least one companion.
The server re-reads authoritative state, so inspection neither authorizes nor
guarantees a later mutation. Normal business outcomes, including denials, are HTTP
200. Reusing a request ID with the same normalized command returns the authoritative
result without duplicate mutation; reuse with a different credential, mode,
declaration, or entering count returns HTTP 409 `IDEMPOTENCY_KEY_REUSED`. Legacy
`/scan` generates a new request ID for every call and therefore does not offer
client-visible retry idempotency.

Credential and guest lifecycle checks are privacy-safe. After successful
credential validation, admission requires `Event.status == ACTIVE`; `DRAFT`,
`COMPLETED`, and `CANCELLED` produce `EVENT_NOT_ACTIVE`. Inspection likewise does
not advertise normal admission actions for a non-active event.

Before entitlement exists, `declared_companions`, `companions_admitted`, and
`remaining_companion_slots` are all `null`. An established zero declaration returns
numeric zero for all three. Admission responses preserve the distinction where the
fields apply.

Corrections use optimistic expectations and the closed decisions `SET_ATTENDED`,
`SET_NOT_ATTENDED`, `RESTORE_GATE_DERIVED`, and `SET_COMPANION_STATE`.
`SET_COMPANION_STATE` requires declaration and admitted counts and preserves
`0 <= admitted <= declared <= allowed` plus the immutable-evidence ceiling.
Sensitive reason notes are confined to the dedicated audit-detail response; the
server derives prior companion state rather than trusting it from the client.

### Reports

Under `/events/{event_id}/reports`:

- `GET /summary`
- `GET /attendance`
- `GET /export/{output_format}` for `csv` and `xlsx`

JSON, CSV, and XLSX share one bounded calculation dataset. Raw gate evidence and
correction-aware effective attendance remain distinct; exports apply a privacy
allowlist and spreadsheet-injection protection. The aggregate summary is
de-identified. Authenticated administrator guest-level JSON may include `guest_id`,
`full_name`, `invitation_type`, `table_number`, and `category`; CSV/XLSX use the
corresponding operational roster fields. Report outputs exclude phone, email, QR
credential/token/hash data, correction reasons/notes/actors, internal entitlement
or provenance IDs, and private paths.

Guest-level report and export rows preserve `null` entitlement values separately
from established numeric zero. Aggregate metrics keep configured capacity,
declaration, effective and raw admissions, remaining declared slots, released and
uncommitted allowance, and total people separate. `remaining_people_capacity` is
overall population capacity and is not remaining declared companion entitlement.

### Future Flutter gate handoff

The future Flutter gate client scans or accepts a credential, calls inspect,
displays server-authoritative state, asks the operator to confirm PRIMARY or
COMPANION, collects declaration/entering counts only when permitted, generates a
request ID, calls admit, and displays the authoritative result. Flutter does not
own entitlement arithmetic, remaining-slot truth, concurrency or idempotency
mutation decisions, primary-first enforcement, or event-active enforcement.

## Error and privacy rules

- validation errors remain actionable without echoing submitted values;
- resource and conflict errors are bounded and do not disclose cross-event state;
- SQL, filesystem, configuration, and secret details never enter responses;
- raw QR values/hashes and private artifact keys are never public API fields;
- private artifact and report responses use `Cache-Control: private, no-store`.
