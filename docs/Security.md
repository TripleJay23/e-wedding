# Security Plan

## Authentication

- Argon2id password hashes
- strong secret from environment
- short-lived JWT access token
- issuer/audience checks
- persisted active-user reload
- protected admin routes require `SUPER_ADMIN`

## Secrets

Never commit `.env` or real secret values.

Rotate credentials if accidental exposure occurs.

The staging administrator is provisioned once. Existing-account password rotation
uses the dedicated staging-only operator with an expected UUID guard, row lock, and
the shared Argon2id password policy; bootstrap never overwrites the account.

## Excel uploads

- allow only supported XLSX package type
- bounded byte size
- inspect actual ZIP/package structure
- protect against decompression bombs/path tricks
- never execute formulas/macros
- do not trust file extension alone
- keep uploaded bytes private
- expose row data only to authorized admin

## Guest data

PII is private:

- names
- emails
- phone numbers
- notes
- attendance
- wedding/event information where private

Use privacy-safe logs and test reports.

## QR

- cryptographically secure opaque tokens
- no PII in payload
- hash-at-rest
- constant-time/safe comparison where relevant
- lifecycle active/inactive/reissue
- render the credential into a protected private QR image before discarding it
- metadata-only management API; never expose the raw token, hash, or storage key
- duplicate attendance protection

## File storage

Private storage only.

Prevent path traversal.

Use content-addressed or deterministic verified storage where appropriate.

Strip image metadata before canonical event-photo storage.

## Template assets

Private source assets/fonts remain ignored unless licensing/redistribution is explicitly confirmed.

Do not make the repo public until asset/font rights are verified.

## Generated cards

Generated cards are private event artifacts and must not be publicly guessable.

## Staging safety

Before staging migrations:

- verify DB identity from connected server
- fresh backup
- active-writer check
- current/target Alembic revision
- controlled migration
- post-migration verification

Disposable DB helper must refuse staging/production names.

## Git audit

Before each accepted commit/release:

- inspect tracked files
- ensure no `.env`
- no private spreadsheets/photos
- no dumps/backups
- no generated cards
- no template private package unless explicitly approved
