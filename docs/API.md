# API Contract Plan

Base path:

```text
/api/v1
```

The exact OpenAPI contract is implemented and frozen milestone by milestone.

## Groups

```text
auth
events
guest-fields
guests
imports
templates
event-photos
card-designs
qr-codes
cards
attendance
reports
health
```

## Core planned operations

### Auth

- login
- current administrator

### Events

- create/list/get/update as permitted
- event policy including maximum companions

### Guest fields

- get/replace event guest-field configuration

### Excel imports

Required capabilities:

- download template where useful
- upload workbook
- get batch/preview
- list batch rows/errors
- confirm currently eligible rows
- correct an invalid staged row
- revalidate corrected row/batch state
- confirm newly eligible rows without duplicating previously imported guests
- list import history

The correction endpoints are required before Excel milestone completion.

`POST /api/v1/events/{event_id}/imports/{batch_id}/progress` preserves batch-wide
progression when called without a body. An optional body containing a non-empty,
unique `staged_row_ids` list selects an exact subset by persisted staged-row UUID.
All selectors are validated against the requested batch before mutation. Selected
`VALID` rows progress, already `IMPORTED` rows are idempotent no-ops, and other row
states reject the complete selection. Unselected rows remain staged and unchanged.

### Guests

- list/get/create/update according to policy

### QR

- issue
- retrieve safe credential metadata
- deactivate
- activate where lifecycle permits
- regenerate/reissue
- bounded event credential metadata list

M5 HTTP responses never return the raw credential, stored token hash, protected
filesystem/storage key, or unnecessary guest PII.

### Templates and card design

- catalogue/list/get
- event photo upload/select
- event design create/replace/revisions
- preview
- approve/revoke approval

### Generated cards

- generate one
- generate eligible/all
- list
- retrieve protected content
- bulk export where accepted

Permanent generation requires approved design and real QR credential.

### Attendance

- validate/scan
- list attendance
- apply correction
- read effective state

### Reports

- JSON summary
- CSV export
- XLSX export

## Error rules

- bounded domain errors
- no SQL/storage/internal paths
- no secrets
- no raw QR hashes/tokens; the issuance/render boundary is internal to the M5 service
- validation errors remain actionable for the administrator
