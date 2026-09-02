# Security Contract

## Authentication and secrets

- one persisted active `SUPER_ADMIN` for the MVP;
- Argon2id password hashes and strict shared password policy;
- short-lived JWTs with subject, issuer, audience, issued-at, and expiry checks;
- active administrator reloaded from PostgreSQL on protected requests;
- secrets accepted only from explicitly selected environment configuration;
- passwords, hashes, database URLs, JWT secrets, and submitted values excluded
  from routine output, logs, and configuration errors.

Bootstrap creates an administrator once and never overwrites it. The staging
rotation operator requires the expected persisted UUID, locks the row, rejects a
same-password replacement, and changes only the hash and normal update timestamp.

## PostgreSQL isolation

Development, disposable test, staging, and production identities are separate.
Guarded migration operators verify environment, local/allowed host, configured and
connected database/user identity, PostgreSQL version where required, current
revision, sole head, and explicit authorization before mutation. Disposable tools
refuse development, staging, production, remote, and ambiguously named targets.

## Private data and artifacts

Guest names, contact data, workbook rows, attendance, event details, photos,
template sources, QR images, generated cards, and database contents are private.
Workbook evidence, canonical photos, verified templates, QR PNGs, and permanent
cards live under server-controlled private roots and are accessed only through
authenticated, event-scoped services. Storage keys are internal and traversal is
rejected. Uploaded photos are decoded, bounded, canonicalized, and stripped of
metadata before persistence.

## Excel security

The importer validates actual XLSX ZIP structure, bounds compressed and expanded
content, rejects unsafe relationships/features and path tricks, loads formulas as
formulas with `data_only=False`, and never evaluates them. Ambiguous mapping fails
closed. Malformed genuine rows remain visible to the administrator; errors and
logs do not disclose row values.

## QR security

Credentials are cryptographically random, opaque, high entropy, and contain no
PII. The raw value exists only inside issuance/rendering or a presented scan
request. PostgreSQL stores only a normalized lowercase SHA-256 verifier; the QR
image is protected private data. Management responses never expose the raw value,
hash, image key, or image content. Event/guest binding, active/inactive/superseded
states, reissue semantics, wrong-event rejection, and duplicate admission are
enforced.

## Attendance and correction integrity

Successful gate attendance and meaningful scan attempts are immutable evidence.
Companion admission is rechecked against current guest/event policy in the scan
transaction. Corrections never rewrite gate truth: they are append-only,
administrator-attributed revisions using optimistic expectations and closed
decisions. Sensitive reason notes are returned only by the dedicated audit-detail
surface.

## Report privacy

JSON, CSV, and XLSX share one read-only bounded dataset. Exports use an explicit
identifier/state/count allowlist and exclude names, email, phone, notes,
table/category, QR/card details, correction notes, and actor identities. CSV/XLSX
cells are neutralized against formula injection. Private responses use
non-cacheable headers.

## Git and publication boundary

Never commit or publish real `.env` files, secrets, guest workbooks/exports,
photos, databases/dumps/backups, QR artifacts, generated cards, or private template
packages/assets/fonts. Committed fixtures are synthetic. Wedding Floral V10 is
technically verified but remains private unless redistribution and font rights are
explicitly confirmed. Repository history and build archives must be audited before
publication.
