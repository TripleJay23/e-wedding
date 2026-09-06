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

Repository-safe GitHub CI uses only synthetic resources. Ignored Wedding Floral
package/font tests are a separate protected local release layer. Disposable
PostgreSQL integration tests use an explicit guarded test URL; staging-only
operator tests remain skipped outside their separately authorized environment.
Excel has dedicated hostile-package/type tests and a synthetic 5,000-candidate
scale acceptance. Generic GitHub CI does not run canonical staging acceptance.

## Database safety

Integration acceptance uses a fresh local PostgreSQL 17 database named
`wedding_ecard_disposable_*` with `APP_ENV=test` and isolated private storage.
The guard must refuse development, `wedding_ecard_staging`, production, remote,
and missing or ambiguous targets. Canonical staging is never a test fallback.

## Release quality gate

The dated H2.7 reference on 2026-09-05 is 739 collected, 735 passed, 4 deliberate
staging-only skips, no failures, xfails, xpasses, or warnings, and 92.20%
branch-aware coverage. It is a checkpoint baseline, not an eternal suite-size
promise. The H2.8 documentation-only closeout rerun preserved the same inventory,
outcomes, and coverage. Release-impacting changes require:

- focused and complete backend tests with at least 90% branch-aware coverage;
- Ruff lint and format checks;
- application/test/migration compilation and configured static checks;
- locked dependency sync and `uv lock --check`;
- wheel and sdist builds with archive inspection;
- Alembic upgrade/current/heads/check and one-revision downgrade/re-upgrade;
- Compose parse/configuration validation;
- private-file, secret-pattern, PII, and documentation-reference audits.

## C2 companion lifecycle evidence

The C2.7 integrated PostgreSQL 17 acceptance used 160 synthetic guests, 66
established entitlements, 77 admitted attempts, and 10 corrections. Its bounded
statement observations were 4 for inspection, 9 for PRIMARY admission, 8 for
COMPANION admission, 4 for exact replay, 7 for correction, 1 for aggregate report,
and 1 for guest report. These local measurements are engineering observations,
not production SLAs or permanent architecture requirements.

The complete C2.7 repository-safe suite collected 932 tests: 928 passed and 4
staging-only tests were skipped, with zero failures, XFAIL, XPASS, or warnings and
92.34% branch coverage. C2.8 repeats the integrated acceptance, migration checks,
complete suite, quality gates, privacy audits, and archive hygiene before creating
its documentation-only closeout commit.

## H2 scale evidence

H2.7 accepted the 5,000-candidate product ceiling. Synthetic parser samples were
approximately 0.027–0.032 seconds for 100 rows, 0.171–0.209 seconds for 1,000, and
0.700–0.772 seconds for 5,000, with about 17.72 MiB traced Python allocation at
5,000. The PostgreSQL scenario staged 4,900 valid and 100 invalid rows, applied one
representative correction, and finished with 4,901 imported plus 99 intentionally
invalid rows—no missing, extra, or duplicated candidates.

Measured upload/staging was about 1.804 seconds, 4,898-row progression 27.806
seconds, duplicate replay 2.106 seconds, and the test body 34.42 seconds. These
measurements classify the current implementation as **acceptable for 5,000**;
they are not deployment SLAs. Bounded `iter_rows` traversal avoids repeated indexed
row access and quadratic openpyxl maximum-column recomputation.

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
