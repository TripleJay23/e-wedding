# Backend

FastAPI backend for the wedding e-card and guest validation system.

Implementation must follow the root `AGENTS.md` and `docs/` milestone plan.

Do not start Flutter/mobile work from this directory.

## Foundation commands

From the repository root:

```text
uv sync --project backend --all-extras --locked
uv run --project backend --env-file .env wedding-alembic current
uv run --project backend --env-file .env wedding-alembic upgrade head
uv run --project backend --env-file .env wedding-bootstrap-admin
uv run --project backend pytest
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
```

Run the API through its factory:

```text
uv run --project backend --env-file .env uvicorn app.main:create_app --factory
```

The Alembic environment intentionally refuses direct, unverified connections.
Use `wedding-alembic` for development and read-only staging checks. Use
`python -m app.cli.disposable_alembic` only with a guarded disposable PostgreSQL
database and `APP_ENV=test`.

`wedding-bootstrap-admin` is a one-time, development-only command. It reads the
administrator password from `ADMIN_BOOTSTRAP_PASSWORD`, never prints or
overwrites it, and should be run only after the guarded migration reaches a
known authentication-compatible revision (currently M2, M3, or M4). Remove that
password from the ignored local environment after the account is provisioned.
