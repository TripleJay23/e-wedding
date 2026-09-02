# Locked Decision Log

These durable decisions prevent contract drift.

| Decision | Status |
|---|---|
| Backend is complete through reports and the full staging release gate | ACCEPTED |
| Flutter is a separate later phase | LOCKED |
| FastAPI/Python backend with PostgreSQL 17 across environments | LOCKED |
| Git flow is `feature/* -> dev -> staging -> main`; remote operations require explicit authorization | LOCKED |
| MVP has one active persisted `SUPER_ADMIN` | LOCKED |
| Default maximum companions per invitation is 2 | LOCKED |
| Excel mapping is deterministic; formulas are never executed | LOCKED |
| Valid rows may progress while invalid rows remain staged | LOCKED |
| Exact selected-row progression is atomic; imported selections are idempotent | LOCKED |
| Original import evidence is preserved; corrections and revalidation are versioned | LOCKED |
| Wedding Floral V10 is preserved by exact digests, not recreated | LOCKED |
| Renderer behavior is generic and manifest-driven | LOCKED |
| Wedding Floral package/assets/fonts remain private until redistribution rights are confirmed | OPEN PUBLICATION DECISION |
| Preview QR is synthetic; permanent cards use real issued QR images | LOCKED |
| Raw QR value is not stored; PostgreSQL stores only its verifier hash | LOCKED |
| Gate evidence is immutable and attendance corrections are append-only | LOCKED |
| Effective attendance is derived from gate evidence plus the correction chain | LOCKED |
| JSON/CSV/XLSX reports share one privacy-bounded dataset | LOCKED |
| Alembic is one clean linear history through `20260821_0008`; no inherited migration chain | LOCKED |
| Development/staging database identities remain operational guardrails, not branding strings | LOCKED |
| Repository name, public license, and synthetic publication assets require later owner decisions | OPEN PUBLICATION DECISION |
