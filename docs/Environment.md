# Environment Plan

## Environments

### Development

- `APP_ENV=development`
- PostgreSQL database `wedding_ecard_dev`
- private local storage

### Automated/disposable integration test

- `APP_ENV=test`
- unique disposable PostgreSQL database name
- separate private temporary storage
- hard refusal of staging/production database identity

### Staging

- `APP_ENV=staging`
- PostgreSQL `wedding_ecard_staging`
- persistent private staging storage
- continuous Jordan & Jessica acceptance scenario

### Production later

Configured only during deployment milestone.

## Environment files

Commit only examples:

```text
.env.example
backend/.env.example
.env.staging.example
```

Never commit:

```text
.env
backend/.env
.env.staging
```

## Canonical local staging workflow

Local M10 staging uses these explicit tracked contracts:

```text
compose.staging.yaml
.env.staging.example
```

Copy `.env.staging.example` to the ignored `.env.staging`, replace every
`CHANGE_ME` value, keep the database password synchronized between
`POSTGRES_STAGING_PASSWORD` and `DATABASE_URL`, and use absolute private-storage
paths. The real file is never committed.

The canonical local identity is:

```text
APP_ENV=staging
host=127.0.0.1
database=wedding_ecard_staging
user=wedding_staging_app
Compose project=wedding-ecard-backend-staging
volume=wedding-ecard-m10-staging-v2-postgres-data
```

The pre-existing `wedding-ecard-staging-postgres-data` volume is unverified
legacy state. The V2 staging workflow never mounts, renames, mutates, or deletes it.

Prepare and start the PostgreSQL 17 service:

```text
Copy-Item .env.staging.example .env.staging
docker compose --env-file .env.staging -f compose.staging.yaml up -d postgres
```

Verify the sanitized target and initialize or upgrade only through the dedicated
authorization-gated command:

```text
uv run --project backend --env-file .env.staging wedding-staging-alembic current
# After confirming an empty DB or fresh backup and no active writers, set:
# STAGING_MIGRATION_AUTHORIZATION=APPLY_ACCEPTED_SCHEMA_TO_CANONICAL_STAGING
uv run --project backend --env-file .env.staging wedding-staging-alembic upgrade head
uv run --project backend --env-file .env.staging wedding-staging-alembic check
```

Ordinary `wedding-alembic` continues to refuse staging mutation. The dedicated
command requires exact staging environment/database/user/local-host identity,
PostgreSQL 17, the public operator acknowledgement in `.env.staging`, and the
repository's sole Alembic head.

Reset or remove the acknowledgement after the migration so each later staging
schema change requires a fresh backup/writer check and deliberate authorization.

Provision the singleton staging administrator exactly once:

```text
uv run --project backend --env-file .env.staging wedding-staging-bootstrap-admin
```

This staging-only command requires the database to be at the repository's sole
head and refuses to overwrite an existing administrator. Passwords, hashes, and
JWT secrets are never printed.

Rotate the password of that existing administrator only through the separate
staging operator. Temporarily set `STAGING_ADMIN_ROTATION_EXPECTED_UUID` to the
persisted singleton UUID and `STAGING_ADMIN_ROTATION_PASSWORD` to a new secret in
the ignored `.env.staging`, then run:

```text
uv run --project backend --env-file .env.staging wedding-staging-rotate-admin-password
```

The command verifies the canonical local staging identity, PostgreSQL 17, the
repository's sole Alembic head, the locked singleton UUID, role, and active state.
It refuses a same-password replacement and changes only the Argon2id password hash
plus the normal update timestamp. Remove both rotation variables after success.
The bootstrap command remains create-once and never overwrites the administrator.

Start FastAPI with the same explicit staging file:

```text
uv run --project backend --env-file .env.staging uvicorn app.main:create_app --factory
```

`GET /api/v1/health` proves process health; `wedding-staging-alembic current`
proves database connectivity and sanitized identity. Normal shutdown preserves
the database:

```text
docker compose --env-file .env.staging -f compose.staging.yaml stop postgres
```

Never use `down -v` during S1-S13. It deletes the canonical persistent staging
volume and is a deliberate destructive reset, not a normal start/stop command.
Outside an active campaign, an explicitly approved reset must first confirm the
exact Compose project/volume and recovery position; only then may an operator run
`docker compose --env-file .env.staging -f compose.staging.yaml down -v`.

## Migration command guard

Before Alembic:

1. print sanitized configured target;
2. connect;
3. query actual DB identity;
4. verify environment/database allow-list;
5. verify current revision;
6. run requested migration.

Staging mutation additionally requires the dedicated staging command and its
explicit operator acknowledgement. Development and disposable migration behavior
is unchanged.
