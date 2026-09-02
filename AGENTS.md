# AGENTS.md — Engineering Control Rules

## 1. Mission and scope

Maintain a secure, deterministic, production-ready wedding e-card and guest
validation backend. Preserve accepted behavior and keep every task within its
explicit checkpoint. Flutter is a separate later phase.

## 2. Agent neutrality

These rules apply to any implementation agent. Do not make repository guidance
depend on one vendor-specific tool unless the user explicitly requests it.

## 3. Sources of truth

Read before every task:

1. `docs/ProjectCharter.md`
2. `docs/Architecture.md`
3. `docs/Workflow.md`
4. `docs/Milestones.md`
5. the feature-specific document, if one exists

When documents conflict, stop and report the conflict rather than silently
choosing an interpretation. Current source, migrations, tests, and accepted
runtime evidence determine implemented behavior.

## 4. Anti-drift rule

Every implementation task identifies the current checkpoint, exact acceptance
criterion, accepted baseline, expected files, required tests, stop condition, and
next checkpoint. Do not add unrelated features, refactors, deployment work, UI
work, or convenient extras. A failed release or staging check authorizes only a
focused diagnosis and repair followed by the same check.

## 5. Git workflow

```text
feature/* -> dev -> staging -> main
```

- create focused feature branches from accepted `dev`;
- use one task and one reviewable local commit per branch where practical;
- never push, fetch, pull, alter remotes, force-push, amend accepted commits, or
  rewrite accepted history without explicit authorization;
- integrate accepted changes into `dev`, promote controlled candidates to
  `staging`, and advance `main` only after the applicable release gate passes.

Commit format: `type(scope): concise description`.

## 6. Architecture and database boundaries

PostgreSQL is the only relational database platform. Development, disposable
tests, staging, and production use separate identities. Never allow disposable
commands to fall back to development, staging, or production.

Before migration work, verify the environment, configured and connected host,
database/user identity, current revision, sole repository head, and intended
target. Routes stay thin; services own business transactions; repositories own
persistence; parsers and renderers remain deterministic and bounded.

## 7. Private data and publication

Never commit or publish:

- real `.env` files, credentials, tokens, database dumps, or backups;
- real guest workbooks, exports, PII, or private event evidence;
- wedding photographs, QR images/credentials, or generated cards;
- private template packages/assets/fonts without confirmed redistribution rights;
- test evidence containing private row values.

Use synthetic fixtures and aggregate, privacy-safe reports.

## 8. Excel import invariant

Excel ingestion is a first-class staged workflow. It must use configured canonical
fields, tolerate reordered columns, map only deterministic aliases, ignore only
proven structural rows, never execute formulas, preserve malformed guest rows,
support partial progression, keep invalid rows correctable/revalidatable, detect
duplicates, and preserve history. Selected staged-row IDs must be non-empty,
unique, batch-owned persisted UUIDs and must be preflighted atomically.

The system default maximum companions per invitation is **2** and spreadsheet
contents never redefine it.

## 9. Card and QR invariants

Wedding Floral V10 is identified by its approved immutable digests; do not redraw
or approximate it. The renderer remains generic and manifest-driven, with no
template-key rendering branch. Private package/assets/fonts remain outside Git
unless publication rights are confirmed.

QR codes contain only high-entropy opaque credentials. Store only the secure
verifier hash in PostgreSQL, bind credentials to event and guest, support explicit
lifecycle transitions, and keep raw values inside the issuance/render boundary.
Preview QR data is synthetic and non-resolving; permanent cards use the protected
image of a real issued credential.

## 10. Attendance and reporting invariants

Gate scans create immutable evidence. Corrections are append-only decisions and
effective state is derived deterministically from gate evidence plus the correction
chain. Reports use the shared bounded dataset, distinguish named guests from
companions and total people, and apply strict privacy and spreadsheet-injection
controls across JSON, CSV, and XLSX.

## 11. Testing and documentation

Feature work requires focused tests, impacted regressions, the complete backend
suite, Ruff, formatting, compile/static checks, locked dependency verification,
build/archive hygiene, migration checks, and privacy/secret audits in proportion
to risk. PostgreSQL feature acceptance uses a fresh guarded disposable database.

Update documentation when behavior or an operational contract changes. Preserve
the accepted S1-S13 scenario as regression evidence; use synthetic fixtures for
narrow technical tests.

## 12. Required task report

Report the checkpoint, branch and HEAD, changes, migrations, tests and results,
privacy/security checks, Git status, commit hash, push status, blockers, and exact
next checkpoint. Stop at the stated checkpoint.
