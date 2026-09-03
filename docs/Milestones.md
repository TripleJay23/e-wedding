# Backend Milestones and Release Status

Backend product milestones are complete through the full staging release gate.
Release finalization remains sequential; a later checkpoint does not authorize an
earlier or unrelated operation. The accepted product display name is `e-Wedding`
and the local repository slug is `e-wedding`.

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

The accepted schema head is `20260821_0008`. The accepted release reference has
537 passed tests, 4 deliberate staging-only skips, and 91.12% branch-aware
coverage.

## Release finalization

| Checkpoint | Outcome |
|---|---|
| F1 | Repository cleanup audit and finalization plan | COMPLETE |
| F2 cleanup execution | Cleanup implementation and documentation reconciliation | COMPLETE |
| F2 cleanup verification/integration | Release-quality verification and integration | COMPLETE |
| F3 | Separately authorized frozen legacy-repository retirement | COMPLETE |
| F4 | Final-name decision and controlled rename plan | COMPLETE |
| F5 | Local filesystem, display-branding, and package-metadata rename to e-Wedding | COMPLETE |
| F5 live verification/integration | Review and integrate the accepted rename without widening scope | COMPLETE |
| F6 | Final backend release verification and main promotion gate | CURRENT — PENDING VERIFICATION |

The public license, public sample strategy, Docker identity or volume migration,
main movement, remote configuration, push, and Flutter work require their own
explicit authorization. Do not infer them from completion of F5.
