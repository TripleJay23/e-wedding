# e-Wedding Repository Setup

The local repository slug is `e-wedding`. Its private GitHub repository is
configured, and `main`, `staging`, and `dev` have been pushed. Future remote
changes, pushes, and any public publication remain separately controlled.

## Prerequisites

- Git
- Python 3.12 or 3.13
- `uv`
- Docker with Compose
- local PostgreSQL 17 through the tracked Compose configuration

## Branch model

```text
feature/* -> dev -> staging -> main
```

New work starts from accepted `dev`. The user controls future remote changes,
pushes, and publication; local setup must not invent or modify remotes.

## Tracked foundation

The repository retains root documentation and policy files, environment examples,
Compose definitions, `pytest.ini`, the `backend/app` package, eight linear Alembic
migrations, all tests, `backend/pyproject.toml`, and `backend/uv.lock`.

Create local environment files only by copying the examples. Keep all real values
ignored. Development setup and commands are documented in `README.md` and
`docs/Environment.md`.

## Private local state

These paths or equivalents remain untracked:

```text
.env
.env.staging
backend/.env
backend/storage/private/
backend/backups/
```

Do not place real guest data, photos, template sources/fonts, QR artifacts,
generated cards, report exports, dumps, or backups in tracked sample directories.
Only separately approved synthetic or appropriately licensed public examples may
be committed under `assets/`.
