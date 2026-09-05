# Engineering Workflow

## Change lifecycle

```text
define checkpoint -> verify accepted baseline -> feature branch from dev
-> focused implementation -> focused and full verification -> local commit
-> review -> fast-forward dev -> controlled staging promotion and acceptance
-> controlled main promotion
```

Each task states its acceptance criterion, expected files, required tests, stop
condition, and next checkpoint. Scope does not expand because adjacent work is
convenient.

## Branch responsibilities

- `feature/*`: one focused task from current accepted `dev`;
- `dev`: integration of reviewed feature commits;
- `staging`: controlled release candidate and continuous acceptance environment;
- `main`: only fully accepted release state.

Use fast-forward integration where required. Never push, fetch, pull, change
remotes, force-push, rebase accepted history, or amend an accepted commit without
explicit authorization.

## GitHub quality gate

The operational `Backend CI` workflow runs the `Backend Quality Gate` for pull
requests targeting and pushes to `dev`, `staging`, and `main`, and by manual
`workflow_dispatch`. It uses synthetic private storage and disposable PostgreSQL
17; canonical staging and private Wedding Floral assets are deliberately absent.
The gate covers privacy/history audits, the locked environment, Ruff, formatting,
compile, Compose parsing, Alembic lifecycle, repository-safe tests with at least
90% branch coverage, and wheel/sdist archive hygiene.

G3C hosted branch-protection/ruleset enforcement is deferred because the private
GitHub plan does not provide the required control. It must not be described as
enabled. Until separately authorized, review and linear fast-forward promotion are
procedurally enforced.

## Dependency automation

Dependabot version updates target `dev`, entering the established promotion flow
from `dev` to `staging` to `main`. Weekly review-first updates cover the backend's
native `uv` ecosystem and GitHub Actions; actions remain pinned to immutable commit
SHAs. Every dependency pull request requires the Backend Quality Gate and human
review; Dependabot auto-merge is not enabled.

The separately pinned uv CLI version remains a manually reviewed maintenance
item. Major dependency acceptance compares collection, skip/xfail/warning
inventory, plugins and fixtures, branch coverage, Windows behavior, disposable
PostgreSQL behavior, and private-API usage before promotion. Automated updates for
the verified PostgreSQL container digest remain outside current Dependabot scope.

## Failure workflow

At the first confirmed defect, stop the checkpoint, distinguish product failure
from harness/environment failure, and report evidence. Product repair occurs on a
narrow feature branch, returns through `dev` and `staging`, and reruns the same
failed checkpoint with the same relevant input. Do not start unrelated work.

## PostgreSQL workflow

PostgreSQL is required throughout. Before migration or integration work, verify
sanitized configured and connected identity, environment, host, database/user,
current revision, sole head, intended target, and protected-storage boundary.
Disposable tests use only unique local `wedding_ecard_disposable_*` databases.

## Staging continuity

The accepted Jordan & Jessica wedding scenario remains the primary end-to-end
regression story:

```text
authentication and event -> policy and fields -> workbook preview
-> partial progression -> correction/revalidation -> duplicate replay
-> QR issuance -> design/photo -> permanent cards -> gate admission
-> corrections -> reports -> full replay
```

Real checkpoint inputs are used only when explicitly supplied. Synthetic fixtures
support narrow technical tests but do not replace staging continuity.

## Documentation and release workflow

Documentation changes accompany accepted contract or operational changes and must
match current source, migrations, tests, and runtime evidence. Release preparation
also verifies archive hygiene, publication exclusions, licensing position, and
Git state. Repository rename, remote configuration, publication, main movement,
and Flutter work each require their own explicit checkpoint.

After H2 closeout, the next backend checkpoint is **C1 — Companion Entitlement
Lifecycle Current-State Audit & Contract**. It precedes Flutter and does not imply
that first-scan companion declaration locking already exists.
