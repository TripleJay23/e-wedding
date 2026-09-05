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

- `POST /attendance/scan` validates one decoded opaque credential and records the
  outcome atomically;
- event attendance, guest attendance, and scan-attempt reads are bounded;
- effective attendance is available by event and guest;
- correction creation/history/detail routes are event and guest scoped.

Corrections use optimistic expectations and the closed decisions
`SET_ATTENDED`, `SET_NOT_ATTENDED`, and `RESTORE_GATE_DERIVED`. Sensitive reason
notes are confined to the dedicated audit-detail response.

### Reports

Under `/events/{event_id}/reports`:

- `GET /summary`
- `GET /attendance`
- `GET /export/{output_format}` for `csv` and `xlsx`

JSON, CSV, and XLSX share one bounded calculation dataset. Raw gate evidence and
correction-aware effective attendance remain distinct; exports apply a privacy
allowlist and spreadsheet-injection protection.

## Error and privacy rules

- validation errors remain actionable without echoing submitted values;
- resource and conflict errors are bounded and do not disclose cross-event state;
- SQL, filesystem, configuration, and secret details never enter responses;
- raw QR values/hashes and private artifact keys are never public API fields;
- private artifact and report responses use `Cache-Control: private, no-store`.
