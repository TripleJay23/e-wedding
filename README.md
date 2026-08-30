# Wedding E-Card Backend — Clean Rebuild Foundation

This repository is a clean backend-first rebuild of the wedding e-card and guest validation system.

The rebuild exists to finish the backend with a controlled milestone workflow, preserve the exact approved wedding-card design, eliminate implementation drift, and reach a tested `main` branch before Flutter work begins.

## Core product flow

```text
Admin login
  → Create/configure wedding event
  → Configure guest fields and event policies
  → Upload Excel guest workbook
  → Detect structure / map fields / validate rows
  → Preview valid + invalid rows
  → Import valid rows
  → Correct invalid staged rows and revalidate
  → Issue secure guest QR credentials
  → Finalize the event card design
  → Generate permanent personalized cards with real QR codes
  → Scan at gate
  → Record attendance and companions
  → Correct attendance when authorized
  → Produce reports and exports
```

## Technology

- Backend: FastAPI / Python
- Database: PostgreSQL for development, test, staging, and production-like verification
- ORM/migrations: SQLAlchemy 2 / Alembic
- Excel: openpyxl or equivalent safe XLSX processing
- QR: secure random opaque credentials; hashes stored at rest
- Card rendering: deterministic server-side image renderer driven by validated template manifests
- Mobile: Flutter, only after backend acceptance is complete

## Git workflow

```text
feature/* → dev → staging → main
```

- Feature branches are small and milestone-scoped.
- `dev` is the integration branch.
- `staging` is the full end-to-end acceptance branch.
- `main` is the tested release branch.
- Agents commit locally only unless the user explicitly authorizes remote operations.

## Start here

1. Read `AGENTS.md`.
2. Read `docs/ProjectCharter.md`.
3. Read `docs/Architecture.md`, `docs/Workflow.md`, and `docs/Milestones.md`.
4. If rebuilding from the frozen legacy project, follow `MASTER_BOOTSTRAP_PROMPT.md` and `docs/LegacyRecovery.md`.
5. Do not implement a milestone until its predecessor is accepted.

## Important rule

The frozen legacy project is evidence, not the new codebase. Reuse exact approved private template assets/packages and stable contracts where appropriate, but do not blindly copy legacy backend source or migrations.

## Foundation development

Prerequisites:

- Python 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose for the local PostgreSQL service

Local setup:

1. Copy `.env.example` to the ignored `.env` file and replace every `CHANGE_ME` value.
2. Start PostgreSQL with `docker compose up -d postgres`.
3. Run `uv sync --project backend --all-extras --locked`.
4. Run the guarded baseline migration with
   `uv run --project backend --env-file .env wedding-alembic upgrade head`.
5. Start the application factory with
   `uv run --project backend --env-file .env uvicorn app.main:create_app --factory`.

The current M6 route surface includes health and authentication plus protected
event CRUD/lifecycle, event guest-field configuration, manual guest foundation,
bounded Excel import workflow endpoints, and metadata-only guest QR credential
issuance/lifecycle endpoints, exact Wedding Floral V10 design/photo workflows, and
private permanent-card generation/download/export. Gate validation, attendance,
and reporting features remain intentionally absent.

After applying the M2 migration, provision the one local development
administrator with `ADMIN_BOOTSTRAP_USERNAME` and `ADMIN_BOOTSTRAP_PASSWORD`
set only in the ignored environment file:

```text
uv run --project backend --env-file .env wedding-bootstrap-admin
```

The command is development-only, refuses to overwrite an existing account,
and requires a known authentication-compatible schema revision (currently M2,
M3, M4, M5, or M6). Remove the bootstrap password from the local environment after
provisioning succeeds.

Foundation verification:

```text
uv run --project backend pytest
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
uv run --project backend python -m compileall -q backend/app backend/tests backend/alembic
```

## Local staging runtime

M10 uses a separate persistent PostgreSQL 17 runtime. Copy
`.env.staging.example` to the ignored `.env.staging`, replace all placeholders,
and follow [docs/Environment.md](docs/Environment.md). The bounded sequence is:

```text
docker compose --env-file .env.staging -f compose.staging.yaml up -d postgres
uv run --project backend --env-file .env.staging wedding-staging-alembic current
uv run --project backend --env-file .env.staging wedding-staging-alembic upgrade head
uv run --project backend --env-file .env.staging wedding-staging-alembic check
uv run --project backend --env-file .env.staging wedding-staging-bootstrap-admin
uv run --project backend --env-file .env.staging uvicorn app.main:create_app --factory
```

The staging migration and administrator commands fail closed on any development,
test, production, remote, wrongly named, or wrongly credentialed database target.
Normal Compose `stop` preserves the M10 database; never use `down -v` during the
continuous S1-S13 campaign.

Disposable migration commands require `APP_ENV=test`, an explicit local
PostgreSQL URL, and a uniquely named database beginning with
`wedding_ecard_disposable_`. Use `python -m app.cli.disposable_alembic`; never
point a test command at development, staging, or production.
