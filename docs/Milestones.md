# Backend Milestones and Release Status

Backend product milestones, Excel hardening through H2, and the C2 companion
entitlement lifecycle are complete. C2 is integrated across the local permanent
branches, canonical staging is accepted at Alembic `20260906_0013`, and final
repository cleanup has passed. The former frozen legacy repository has been
verified absent and no longer required. The current C2 and release-finalization
changes have not yet been published to the private remote, and Flutter has not
started. A later checkpoint does not authorize an earlier or unrelated operation.
The accepted product display name is `e-Wedding` and the repository slug is
`e-wedding`.

| Milestone | Durable outcome | Status |
|---|---|---|
| M0 | Controlled foundation, PostgreSQL plan, and exact private V10 integrity evidence | COMPLETE |
| M1 | FastAPI factory, fail-closed settings, PostgreSQL/Alembic foundation, health and test harness | COMPLETE |
| M2 | Singleton administrator, Argon2id, JWT login and protected identity reload | COMPLETE |
| M3 | Events, lifecycle, seven guest fields, manual guests, and companion policy default 2 | COMPLETE |
| M4 | Safe staged XLSX workflow, partial progression, correction/revalidation, duplicate handling, and history | COMPLETE |
| M5 | Opaque QR issuance, verifier hash-at-rest, protected image, and lifecycle | COMPLETE |
| M6 | Exact Wedding Floral V10, canonical photos, design revisions/approval, and permanent cards | COMPLETE |
| M7 | Gate validation, bounded outcomes, immutable scans/attendance, and companion admission | COMPLETE |
| M8 | Append-only corrections and deterministic effective-state reduction | COMPLETE |
| M9 | Shared privacy-safe JSON, CSV, and XLSX reports | COMPLETE |
| M10 | Continuous S1-S13 staging journey and full backend replay/release gate | COMPLETE |

The accepted C2 schema head is `20260906_0013`. The earlier technical release baseline is
`6891e644472e81d5bc3690dc9ebc53fff9511ebf`; its accepted release verification has
537 passed tests, 4 deliberate staging-only skips, and 91.12% branch-aware
coverage. Later documentation-only governance commits do not represent a rerun of
that release suite.

## Release finalization

| Checkpoint | Outcome |
|---|---|
| F1 | Repository cleanup audit and finalization plan | COMPLETE |
| F2 cleanup execution | Cleanup implementation and documentation reconciliation | COMPLETE |
| F2 cleanup verification/integration | Release-quality verification and integration | COMPLETE |
| F3 | Legacy-repository retirement established; the predecessor is no longer required | COMPLETE |
| F4 | Final-name decision and controlled rename plan | COMPLETE |
| F5 | Local filesystem, display-branding, and package-metadata rename to e-Wedding | COMPLETE |
| F5 live verification/integration | Review and integrate the accepted rename without widening scope | COMPLETE |
| F6 | Final backend release verified and main promotion approved | PASS |
| F7A | Main promotion by pure fast-forward and permanent-branch convergence | PASS |
| GitHub private repository baseline | Private `e-wedding` repository configured | COMPLETE |
| Earlier private baseline publication | Pre-C2 `main`, `staging`, and `dev` pushed | COMPLETE |
| G1 | GitHub Actions CI architecture and repository readiness | COMPLETE |
| G2 | GitHub Actions CI implementation | COMPLETE |
| G3 | Live GitHub Actions verification | PASS |
| G3C | Hosted branch-protection/ruleset enforcement | DEFERRED — PRIVATE PLAN |
| G4 | Review-first Dependabot automation for uv and GitHub Actions | COMPLETE |
| G4 accepted updates | Ruff 0.16.5, setup-uv 10.0.1, pytest 9.1.1; uv CLI retained at 0.11.14 | COMPLETE |
| C2 local integration | Accepted C2 release integrated through `dev`, `staging`, and `main` | PASS |
| RF1 | Final repository cleanup and hygiene | PASS |
| RF2 | Frozen predecessor verified absent and no longer required | PASS |
| Current release publication | C2 and release-finalization changes not yet pushed | PENDING |
| Flutter | Administrator and gate client implementation | NOT STARTED |

Backend CI runs on pull requests targeting and pushes to `dev`, `staging`, and
`main`, plus `workflow_dispatch`. G3C is not enabled: procedural review and linear
fast-forward promotion remain authoritative because the private GitHub plan blocks
the required hosted enforcement.

## Excel hardening

| Checkpoint | Durable outcome | Status |
|---|---|---|
| H1 | Current-state Excel contract and documentation audit | COMPLETE |
| H2.1 | Synthetic characterization of unsafe ingestion boundaries | COMPLETE |
| H2.2 | Field normalization and persistence-boundary hardening | COMPLETE |
| H2.3 | Structural-row safety and candidate visibility | COMPLETE |
| H2.4 | Visible-sheet and header-selection resilience | COMPLETE |
| H2.5 | Collision-free deterministic alias flexibility | COMPLETE |
| H2.6 | Repeated-header and noisy data-region hardening | COMPLETE |
| H2.7 | Hostile-package security and 5,000-candidate scale acceptance | PASS |
| H2.8 | Documentation, final reconciliation, and release verification | COMPLETE |

H2 introduced no migrations or breaking API changes; its schema head remained
`20260821_0008`. The dated H2.7 baseline is 739 collected, 735 passed, 4
staging-only skips, no failures or warnings, and 92.20% branch-aware coverage.

## Companion entitlement lifecycle

| Checkpoint | Durable outcome | Status |
|---|---|---|
| C1 | Current-state audit and backend/Flutter contract | COMPLETE |
| C2.1 | Strict characterization of pre-C2 gaps | COMPLETE |
| C2.2–C2.2C | Entitlement, attempt outcome/reference, and correction-origin persistence | COMPLETE |
| C2.3 | Transactional PRIMARY/COMPANION state machine and idempotency | COMPLETE |
| C2.4 | Read-only inspect and explicit admit scanner API | COMPLETE |
| C2.5–C2.5A | Append-only companion correction persistence and behavior | COMPLETE |
| C2.6 | Raw/effective report metrics and export privacy | COMPLETE |
| C2.7 | Integrated synthetic event-day PostgreSQL acceptance | PASS |
| C2.8 | Documentation and release closeout | COMPLETE |

C2.7 accepted 932 collected tests, 928 passed, 4 staging-only skips, no failures,
XFAIL, XPASS, or warnings, and 92.34% branch-aware coverage. The implementation
distinguishes invitation capacity, locked declaration, effective and raw admitted
companions, remaining declared slots, and released versus uncommitted allowance.
The accepted C2 history is integrated across the local permanent branches.
Publication of that release remains separately controlled. Flutter remains not
started.

The public license, public sample strategy, Docker identity or volume migration,
deployment, future remote changes or pushes, and Flutter work require their own
explicit authorization.
