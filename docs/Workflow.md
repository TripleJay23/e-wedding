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
