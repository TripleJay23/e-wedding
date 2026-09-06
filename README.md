# e-Wedding

e-Wedding is a secure wedding invitation, e-card, guest validation, attendance,
and reporting platform. Its accepted backend uses FastAPI and PostgreSQL.

## Release status

The FastAPI/PostgreSQL backend is implemented and release-tested through reports.
Its staged Excel ingestion subsystem is hardened through H2, including hostile
package checks and 5,000-candidate acceptance. The private GitHub repository has
operational backend CI and review-first Dependabot updates. Flutter has not been
implemented; companion entitlement lifecycle hardening remains a separately
authorized backend checkpoint before Flutter begins.

## Capabilities

- singleton administrator authentication with Argon2id and strict JWT validation;
- wedding-event, guest-field, guest, and companion-policy management;
- bounded XLSX inspection, deterministic header mapping, staged validation,
  partial progression, correction, revalidation, duplicate handling, and history;
- opaque high-entropy QR credentials with hash-at-rest and protected QR images;
- exact, manifest-driven Wedding Floral V10 design verification, event-photo
  controls, approval, permanent card generation, protected downloads, and export;
- gate validation with wrong-event, unknown, inactive, superseded, duplicate, and
  companion-limit outcomes;
- immutable gate evidence with append-only attendance corrections;
- shared, privacy-bounded JSON, CSV, and XLSX attendance reporting.

## Architecture

```text
Future Flutter client
        |
        v
FastAPI /api/v1
        |
        +-- PostgreSQL 17
        +-- private workbook/photo/QR/card storage
        +-- deterministic XLSX, QR, image, and report services
```

Routes are thin, transaction and domain rules live in services, persistence is
isolated in repositories, and parsers/renderers are deterministic components.
PostgreSQL is required for development, disposable integration tests, staging,
and production.

## Repository structure

```text
backend/app/                 application, routes, services, repositories, models
backend/alembic/             linear PostgreSQL migrations through 20260906_0013
backend/tests/               unit/API/service and PostgreSQL integration tests
docs/                        authoritative engineering and product contracts
compose.yaml                 local development PostgreSQL 17
compose.staging.yaml         isolated persistent staging PostgreSQL 17
```

## Development setup

Prerequisites: Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/), Docker, and
Docker Compose.

1. Copy `.env.example` to the ignored `.env` file and replace every `CHANGE_ME`
   value.
2. Start PostgreSQL:

   ```text
   docker compose up -d postgres
   ```

3. Install the locked development environment:

   ```text
   uv sync --project backend --all-extras --locked
   ```

4. Apply the guarded migrations and provision the development administrator:

   ```text
   uv run --project backend --env-file .env wedding-alembic upgrade head
   uv run --project backend --env-file .env wedding-bootstrap-admin
   ```

5. Remove `ADMIN_BOOTSTRAP_PASSWORD` from the local environment after successful
   provisioning, then run the API:

   ```text
   uv run --project backend --env-file .env uvicorn app.main:create_app --factory
   ```

The guarded commands verify environment, host, database identity, and migration
state. Disposable tests require an explicitly configured local database whose name
starts with `wedding_ecard_disposable_`; they refuse development, staging, and
production targets.

## Quality gate

The dated H2.7 acceptance baseline is 739 collected, 735 passed, 4 deliberate
staging-only skips, no failures or warnings, and 92.20% branch-aware coverage.
The repository also requires Ruff, format, compile, locked dependency,
build/archive, Alembic lifecycle, Compose, privacy, secret, and documentation
checks. See `docs/TestingStrategy.md`.

## Security and privacy

Never commit or publish real environment files, credentials, guest workbooks or
PII, wedding photographs, database contents, QR artifacts, generated cards, report
exports, or private template sources. Protected artifacts are served only through
authenticated, event-scoped APIs. Raw QR credentials are never returned by
management APIs, stored in PostgreSQL, or written to routine logs.

## Private template provisioning

Wedding Floral V10 is technically verified by immutable package, logical,
manifest, and approved-preview digests. Its package, assets, and fonts remain in
ignored private storage and must not be published unless redistribution rights are
separately confirmed. A deployment must provision the verified private package
before template registration and card-generation workflows can be used.

## Flutter relationship

The backend contract is designed for a future Flutter administrator and gate
client. Flutter implementation remains a separate, later phase and must consume
the accepted `/api/v1` contract without weakening backend security or privacy.

## Documentation and license

Start with `AGENTS.md`, then see `docs/Architecture.md`, `docs/API.md`,
`docs/Environment.md`, `docs/Security.md`, and `docs/Workflow.md`.

This repository remains private and proprietary. It does not claim an open-source
license; public publication readiness and licensing remain separate owner
decisions recorded in `LICENSE`.
