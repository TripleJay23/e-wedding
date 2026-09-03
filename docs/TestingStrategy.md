# Testing Strategy

## Test topology

- unit tests cover parsers, normalizers, validators, reducers, security helpers,
  QR primitives, renderers, and export encoders;
- API tests cover authentication, authorization, validation, privacy headers,
  bounded routes, and safe errors;
- service/repository tests cover transactions, query shape, event scoping,
  optimistic concurrency, idempotency, and rollback;
- PostgreSQL integration tests cover real constraints, migrations, concurrency,
  storage boundaries, and complete feature behavior;
- staging acceptance preserves one continuous product journey through reports.

The repository contains 52 test modules plus `conftest.py`: 41 regular modules and
11 opt-in PostgreSQL integration modules.

## Database safety

Integration acceptance uses a fresh local PostgreSQL 17 database named
`wedding_ecard_disposable_*` with `APP_ENV=test` and isolated private storage.
The guard must refuse development, `wedding_ecard_staging`, production, remote,
and missing or ambiguous targets. Canonical staging is never a test fallback.

## Release quality gate

The accepted reference is 537 passed tests, 4 deliberate staging-only skips, and
91.12% branch-aware coverage. Release-impacting changes require:

- focused and complete backend tests with at least 90% branch-aware coverage;
- Ruff lint and format checks;
- application/test/migration compilation and configured static checks;
- locked dependency sync and `uv lock --check`;
- wheel and sdist builds with archive inspection;
- Alembic upgrade/current/heads/check and one-revision downgrade/re-upgrade;
- Compose parse/configuration validation;
- private-file, secret-pattern, PII, and documentation-reference audits.

## Privacy and fixtures

Committed fixtures contain only synthetic data. Real guest workbooks, photos,
template packages/fonts, QR images, generated cards, exports, database contents,
and evidence remain ignored. Test reports use aggregate results and never disclose
private rows or secrets.

## Staging regression

Staging is not replaced by unit or disposable integration tests. A staging PASS
must identify the existing event, continue the same accepted scenario, compare
expected and observed behavior, preserve protected storage/database state, and
name the exact next checkpoint.
