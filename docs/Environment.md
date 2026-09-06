# e-Wedding Environment and Operations

The e-Wedding display and repository names do not replace the retained database,
JWT, CLI, Compose, container, network, or volume identities documented below.

## Environment separation

| Environment | Required identity | Storage |
|---|---|---|
| Development | `APP_ENV=development`, local `wedding_ecard_dev` | private local root |
| Disposable integration | `APP_ENV=test`, unique local `wedding_ecard_disposable_*` | unique temporary root |
| Staging | `APP_ENV=staging`, local `wedding_ecard_staging`, user `wedding_staging_app` | persistent absolute private root |
| Production | `APP_ENV=production`, separately approved identity | separately provisioned private storage |

Commit only `.env.example`, `backend/.env.example`, and
`.env.staging.example`. Real `.env`, `backend/.env`, and `.env.staging` files
contain secrets and remain ignored.

## Development

Copy `.env.example` to `.env`, replace every placeholder, and keep
`POSTGRES_DEV_PASSWORD` synchronized with the password in `DATABASE_URL`.

```text
docker compose up -d postgres
uv sync --project backend --all-extras --locked
uv run --project backend --env-file .env wedding-alembic current
uv run --project backend --env-file .env wedding-alembic upgrade head
uv run --project backend --env-file .env wedding-alembic check
uv run --project backend --env-file .env wedding-bootstrap-admin
uv run --project backend --env-file .env uvicorn app.main:create_app --factory
```

Remove `ADMIN_BOOTSTRAP_PASSWORD` after the singleton account is provisioned.

## Disposable PostgreSQL tests

Disposable commands require `APP_ENV=test`, an explicit local PostgreSQL URL,
and a unique database name beginning with `wedding_ecard_disposable_`. They verify
the connected identity and refuse development, staging, production, remote, or
ambiguous targets. Each acceptance run creates and removes only its own synthetic
database and storage roots.

## Canonical local staging

The current operational staging contract remains:

```text
APP_ENV=staging
host=127.0.0.1
database=wedding_ecard_staging
user=wedding_staging_app
Compose project=wedding-ecard-backend-staging
volume=wedding-ecard-m10-staging-v2-postgres-data
```

The milestone-bearing volume name is retained until a separately approved,
backup-backed naming migration. The older
`wedding-ecard-staging-postgres-data` volume is unverified and must not be mounted,
renamed, mutated, or deleted as part of ordinary operations.

Copy `.env.staging.example` to the ignored `.env.staging`, replace all
placeholders, and set every private storage root to an absolute path beneath
`PRIVATE_STORAGE_ROOT`.

```text
docker compose --env-file .env.staging -f compose.staging.yaml up -d postgres
uv run --project backend --env-file .env.staging wedding-staging-alembic current
uv run --project backend --env-file .env.staging wedding-staging-alembic upgrade head
uv run --project backend --env-file .env.staging wedding-staging-alembic check
uv run --project backend --env-file .env.staging wedding-staging-bootstrap-admin
uv run --project backend --env-file .env.staging wedding-staging-rotate-admin-password
uv run --project backend --env-file .env.staging uvicorn app.main:create_app --factory
```

Staging schema mutation requires the exact public acknowledgement
`STAGING_MIGRATION_AUTHORIZATION=APPLY_ACCEPTED_SCHEMA_TO_CANONICAL_STAGING`, a
backup and writer check, PostgreSQL 17, the canonical local identity, and the sole
repository Alembic head. Remove the acknowledgement after use.

Bootstrap creates the staging administrator once. Rotation temporarily requires
the expected persisted UUID and a new environment-only password; remove both
rotation variables after success.

Normal shutdown preserves state:

```text
docker compose --env-file .env.staging -f compose.staging.yaml stop postgres
```

`down -v` is a destructive reset and requires separate explicit authorization,
exact project/volume verification, and a confirmed recovery position.

## Migration safety sequence

Before every migration action: sanitize the configured target, connect, read the
actual database/user identity, verify the environment allowlist, inspect current
and target revisions, confirm the sole head, and only then execute the requested
operation. The current repository head is `20260906_0013`.
