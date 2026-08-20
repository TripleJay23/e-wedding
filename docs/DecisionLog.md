# Locked Decision Log

These decisions prevent repeated rediscovery and drift.

| Decision | Status |
|---|---|
| Backend first; Flutter only after backend acceptance | LOCKED |
| FastAPI / Python backend | LOCKED |
| PostgreSQL retained across dev/test/staging | LOCKED |
| Git flow `feature/* → dev → staging → main` | LOCKED |
| Agents commit locally; no push without explicit user authorization | LOCKED |
| Main staging story is Jordan & Jessica wedding | LOCKED |
| Default max companions per invitation = 2 | LOCKED |
| Valid Excel rows may be imported while invalid rows remain staged | LOCKED |
| Invalid staged Excel rows require in-app/backend correction + revalidation | LOCKED |
| Formulas are never evaluated during Excel import | LOCKED |
| Exact approved Wedding Floral V10 design must be preserved | LOCKED |
| Generic manifest-driven renderer; no template-specific rendering code | LOCKED |
| Preview QR synthetic/non-resolving | LOCKED |
| Permanent cards use real secure guest QR credential | LOCKED |
| Raw QR token never stored; only hash at rest | LOCKED |
| M5 stores the protected QR image; M6 consumes it without raw-token recovery | LOCKED |
| Docs fully reconciled after staging acceptance, before main | LOCKED |
| Old/frozen repository is evidence; new backend code is rebuilt cleanly | LOCKED |
