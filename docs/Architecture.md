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
`20260821_0008`; it was built cleanly and does not inherit another repository's
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
bounded XLSX bytes -> package validation -> sheet/header detection
-> deterministic mapping -> structural classification -> lossless normalization
-> row/duplicate validation -> persisted batch and rows -> partial progression
-> correction -> explicit revalidation -> later progression and reconciliation
```

Formulas are inspected as formulas and never executed. Structural rows require
strong contextual evidence; malformed genuine rows remain visible. Original row
snapshots and revision history are retained while current corrected values evolve
under optimistic version checks.

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

## Gate evidence, corrections, and reports

A scan validates event scope, credential/guest lifecycle, duplicate state, and
companion bounds in one transaction. Successful attendance and every meaningful
scan attempt are immutable evidence. Corrections form an append-only optimistic
revision chain; effective state is a deterministic reduction over gate evidence
and the latest correction.

Reports use one read-only bounded dataset for JSON, CSV, and XLSX. Named invitation
holders, companion capacity, admitted companions, and total people are distinct
metrics. Exports omit contact data, notes, table/category values, QR/card data,
correction notes, and actor identities.

## Versioning and privacy

The HTTP base remains `/api/v1`; breaking contract changes require an explicit
architecture decision. Routine logs and public errors exclude secrets, raw QR
values, workbook rows, photo bytes, PII, SQL details, and private paths.
