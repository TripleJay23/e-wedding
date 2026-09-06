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

The upload envelope accepts only `.xlsx` with the configured XLSX/octet-stream
media types and at most 10 MiB. Package inspection rejects malformed ZIP/XLSX,
more than 2,000 entries, more than 50 MiB total expanded content, any entry over
20 MiB, or compression ratio over 250:1. Duplicate, encrypted, parent-traversal,
absolute, empty-component, and Windows drive-prefixed member names are rejected.

XLSM envelopes, VBA parts, ActiveX, embedded objects, custom UI, and external
workbook parts are rejected where represented by the implemented forbidden package
parts/prefixes. Ordinary external hyperlink relationships may remain only as inert
metadata: the importer uses `keep_links=False`, makes no network calls, and never
dereferences them. Other non-active custom parts are not globally allowlisted;
they remain inert and subject to the same entry/expansion bounds. This contract
does not claim explicit rejection of every possible relationship type.

Formulas load as formulas with `data_only=False` and are never evaluated. A
formula in a guest row is invalid unless the whole row is independently proven to
be a supported structural aggregate. Excel error-typed cells receive deterministic
field-associated invalidation, while literal normal text `"#N/A"` is not
automatically treated as an Excel error. Unsupported types remain invalid.

Only visible sheets participate in bounded table discovery. Genuine ambiguity
fails closed with `AMBIGUOUS_GUEST_TABLE`; malformed and uncertain post-header
rows remain visible to the administrator. Candidate count is capped at 5,000,
worksheet dimensions at 10,000 rows by 100 columns, and header/probe scans at 100
rows each. Errors and logs do not disclose row values.

Successful imports keep the original workbook in protected evidence storage.
Known persistence failures after a write clean up that artifact. A process crash
between evidence write and database commit may leave a recoverable orphan; this is
accepted H2 operational recovery debt and does not imply corrupted committed data.

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
Inspection is read-only and creates no reservation or attempt evidence. Admission
revalidates credential, event, guest, attendance, correction, and entitlement state
transactionally; non-active events cannot admit. A caller-generated request ID
makes exact admission-command retries authoritative and consume capacity once,
while conflicting reuse fails with `IDEMPOTENCY_KEY_REUSED`. The deprecated legacy
scan route creates a fresh request ID and must not be treated as retry-idempotent.

Companion admission is rechecked against current guest/event policy and locked
entitlement in the admission transaction. Corrections never rewrite gate truth:
they are append-only, administrator-attributed revisions using optimistic
expectations and closed decisions. Companion-state correction captures previous
state server-side and cannot raise effective admissions above immutable physical
evidence. Sensitive reason notes are returned only by the dedicated audit-detail
surface and remain absent from reports.

## Report privacy

JSON, CSV, and XLSX share one read-only bounded dataset. Aggregate reports are
de-identified and contain no guest identity, contact, credential, or
correction-sensitive details. Authenticated administrator guest-level reports and
exports may include the operational fields guest ID, full name, invitation type,
table number, and category together with attendance/companion state.

All report forms exclude phone number, email, raw QR/token material, verifier
hashes, correction reason/note, correction actor identity, internal entitlement or
provenance IDs, and filesystem/internal paths. CSV/XLSX cells are neutralized
against formula injection. Private responses use non-cacheable headers. The
guest-level operational identity allowance supersedes the former blanket claim
that name, table, and category were excluded from every export.

## Git and publication boundary

Never commit or publish real `.env` files, secrets, guest workbooks/exports,
photos, databases/dumps/backups, QR artifacts, generated cards, or private template
packages/assets/fonts. Committed fixtures are synthetic. Wedding Floral V10 is
technically verified but remains private unless redistribution and font rights are
explicitly confirmed. Repository history and build archives must be audited before
publication.
