# Backend Milestones and Release Status

Backend product milestones are complete through the full staging release gate.
Release finalization remains sequential; a later checkpoint does not authorize an
earlier or unrelated operation.

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
535 passed tests, 4 deliberate staging-only skips, and 91.12% branch-aware
coverage.

## Release finalization

| Checkpoint | Outcome |
|---|---|
| F1 | Repository cleanup audit and finalization plan | COMPLETE |
| F2 | Cleanup implementation, documentation reconciliation, and release-quality verification | IN PROGRESS |
| F2 live verification/integration | Review and integrate the accepted cleanup without widening scope | NEXT AFTER F2 PASS |
| F3 | Separately authorized legacy repository retirement | NOT STARTED |
| F4 | Approved final naming, publication boundary, main promotion, and GitHub handoff | NOT STARTED |

The repository name, public license, public sample strategy, Docker volume naming
migration, main movement, remote configuration, push, and Flutter work require
their own explicit authorization. Do not infer them from completion of F2.
