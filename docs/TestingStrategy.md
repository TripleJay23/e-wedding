# Testing Strategy

## Test pyramid

### Unit

Pure parsers, validators, reducers, renderers, security helpers.

### Service/repository

PostgreSQL-backed business rules and transactions.

### API

Authentication, authorization, HTTP contracts, privacy headers, concurrency.

### Migration

Upgrade/downgrade where supported, fresh install, PostgreSQL DDL, Alembic check.

### End-to-end staging

Continuous Jordan & Jessica scenario.

## Database testing

Use disposable PostgreSQL databases with hard guards.

A disposable command must refuse:

- `wedding_ecard_staging`
- production database names
- non-local target unless explicitly allowed
- missing `APP_ENV=test`

## Regression gate

Every feature commit requires:

- focused tests
- impacted regressions
- lint/format
- compile/type checks where configured
- complete backend suite before acceptance

## Privacy

Committed fixtures must be synthetic.

Real Excel files and wedding photos are staging/private evidence only.

## Staging acceptance

Staging is not just a test suite. It proves the real product journey.

A PASS report must include continuity, expected vs observed behavior, and the next exact checkpoint.
