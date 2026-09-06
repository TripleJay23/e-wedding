# Database Contract

PostgreSQL 17 is authoritative. Alembic maintains one linear migration chain from
`20260814_0001` through the sole head `20260906_0013`.

H2 Excel hardening changed parser/service behavior and tests only; it introduced no
migration, table, column, or breaking API contract.

## Persisted entities

- `users`: singleton administrator identity, Argon2id hash, role, active state,
  and timestamps.
- `events`: wedding identity, schedule/location, lifecycle, companion ceiling,
  creator, and timestamps.
- `event_guest_fields`: ordered enabled/required configuration for the seven
  canonical guest fields.
- `guests`: event-scoped invitation holder data, allowance, lifecycle, and
  timestamps.
- `import_batches`: workbook digest/size/private key, detected structure, mapping,
  field/policy snapshots, aggregate counts, lifecycle, and timestamps.
- `import_batch_rows`: immutable original values/errors, current values/errors,
  identity fingerprints, status, optimistic version, optional guest link, and
  timestamps.
- `import_row_revisions`: append-only correction/revalidation evidence with actor
  and validation outcome.
- `guest_qr_credentials`: event/guest binding, unique verifier hash, protected QR
  image identity, lifecycle, reissue link, actor, and timestamps.
- `template_presets` and `template_preset_versions`: immutable template identity,
  integrity digests, dimensions/counts, verification state, and actor.
- `event_photos`: canonical private image identity, dimensions, primary selection,
  actor, and timestamps.
- `event_card_designs`, `event_card_design_revisions`, and
  `event_card_design_approvals`: event design identity, immutable controls and
  approval/revocation history.
- `generated_guest_cards`: pinned render identities, protected artifact digest/key,
  dimensions, lifecycle, actor, and timestamps.
- `guest_attendance_records`: one immutable successful primary gate admission per
  guest with initial companion count and actor.
- `guest_companion_entitlements`: current declared and effective cumulative
  companion state, established by exactly one primary attendance or correction.
- `gate_scan_attempts`: immutable bounded evidence for each meaningful admission
  command/outcome, request idempotency, transition delta, and post-state; stores
  only a credential fingerprint, never the raw value.
- `guest_attendance_corrections`: append-only optimistic correction chain and
  resulting effective attendance/companion state.

Reports are calculated on demand and have no report-storage table or setting.

## Core invariants

- one singleton `SUPER_ADMIN`; protected authentication requires an active row;
- guest/import/QR/card/attendance data remains event scoped;
- event companion maximum is `0..2`, defaults to 2, and guest allowance cannot
  exceed persisted policy;
- guest allowance is configured invitation capacity, entitlement declaration is
  the locked total, and entitlement admission is current effective cumulative
  consumption; `0 <= admitted <= declared <= allowed`;
- entitlement origin is exclusive: gate origin has a primary attendance reference,
  while correction origin has an establishing correction reference;
- normalized original canonical import snapshots are immutable; current corrected
  values remain separate; corrections and revalidations append revision evidence;
  progression selections lock and validate all selected rows;
- QR verifier hashes are unique and one current lifecycle identity is enforced;
- template and design revision identities are immutable; permanent cards pin all
  accepted render inputs;
- successful attendance is unique per event/guest and gate evidence is immutable;
- correction decisions are closed and append-only with monotonic revisions;
- database constraints and transaction locks protect invariants against direct or
  concurrent bypasses.

## Operational rules

Development, disposable test, staging, and production identities are separate.
Migration commands must verify the connected database and revision before action.
Tests must never fall back to development or staging. Schema history must not be
squashed during release cleanup.

An unestablished invitation has no entitlement row, represented as null declaration,
admission, and remaining values at the API/report boundary. An established zero
declaration persists zero declaration and admission and has zero remaining slots.
Entitlement does not create companion guest rows, QR credentials, cards, or
attendance-holder identities.
