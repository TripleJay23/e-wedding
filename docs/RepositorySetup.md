# Repository Setup Contract

The implementation agent creates the actual repository locally.

## Required initial branches

```text
main
staging
dev
```

Initial foundation work occurs on `feature/repository-foundation` from `dev` after the clean initial branch graph is established.

## Root files

Required:

- README.md
- AGENTS.md
- LICENSE
- .gitignore
- .gitattributes
- .editorconfig
- .env.example

## Backend foundation files to create during M1

- dependency/project metadata (`pyproject.toml` preferred)
- application package
- tests
- Alembic configuration
- Docker/PostgreSQL local setup where useful
- lint/format/test configuration

Do not invent implementation code during M0 recovery.

## Private local folders

Create only when needed and keep ignored:

```text
backend/storage/private/
backend/backups/
backend/.env
```
