# e-Wedding Backend

FastAPI application, PostgreSQL migrations, and automated tests for the e-Wedding
invitation, guest-validation, attendance, and reporting platform.

Run commands from the repository root:

```text
uv sync --project backend --all-extras --locked
uv run --project backend --env-file .env wedding-alembic current
uv run --project backend --env-file .env wedding-alembic upgrade head
uv run --project backend --env-file .env wedding-alembic check
uv run --project backend --env-file .env wedding-bootstrap-admin
uv run --project backend --env-file .env uvicorn app.main:create_app --factory
uv run --project backend pytest
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
uv run --project backend python -m compileall -q backend/app backend/tests backend/alembic
```

The application includes authentication, events and guests, complete staged Excel
imports, QR lifecycle, template/photo/design/card workflows, gate attendance,
append-only corrections, and JSON/CSV/XLSX reports.

Use `wedding-alembic` only for guarded development migration operations. Use
`wedding-disposable-alembic` with `APP_ENV=test` and an explicitly named local
`wedding_ecard_disposable_*` database for disposable integration work. Staging
uses the dedicated `wedding-staging-*` operators described in
`../docs/Environment.md`; ordinary development and test commands refuse it.

`wedding-bootstrap-admin` creates the development singleton once and never
overwrites it. Remove its environment-only password after successful provisioning.
Private workbook, photo, template, QR, card, and report artifacts stay outside Git.

Repository-wide engineering controls are in `../AGENTS.md`.
