# Project Charter

## Purpose

Provide e-Wedding as a secure, deterministic wedding invitation, e-card,
guest-validation, attendance, and reporting platform that supports the full event
journey and a future mobile administrator/gate client.

## Current state

Backend functionality through reports has passed the continuous staging replay and
release gate. Repository cleanup, legacy-repository retirement, and the final local
rename to `e-wedding` are complete. Main promotion, publication, and Flutter
implementation are separate later checkpoints.

## Primary user

The MVP has one authenticated active `SUPER_ADMIN` with full administrative
capability. Additional roles are outside the accepted backend scope unless
explicitly reintroduced.

## Accepted product journey

```text
administrator authentication -> event and guest policy -> staged Excel import
-> partial progression -> row correction/revalidation -> guest reconciliation
-> secure QR issuance -> exact approved design/photo -> permanent cards
-> gate validation and companion admission -> append-only corrections
-> reconciled JSON/CSV/XLSX reports
```

## Locked product decisions

- FastAPI/Python backend and PostgreSQL 17 across environments;
- branch flow `feature/* -> dev -> staging -> main` with explicit authorization
  for remote operations;
- default maximum companions per invitation is exactly 2;
- Excel mapping and classification are deterministic, formulas are never executed,
  valid rows can progress independently, and invalid rows remain correctable;
- Wedding Floral V10 is preserved by exact integrity anchors through a generic
  manifest-driven renderer;
- preview QR is synthetic while permanent cards use protected images of real,
  event/guest-bound opaque credentials whose raw values are not stored;
- gate evidence is immutable, corrections are append-only, and reports preserve
  raw-versus-effective attendance semantics;
- private guest, workbook, photo, template, QR, card, report, secret, and database
  material remains outside Git.

## Non-goals without explicit approval

- payments, billing, public RSVP, messaging automation, or social automation;
- multi-tenant SaaS roles/billing;
- AI guest analytics, fuzzy import decisions, or AI photo cropping;
- unconstrained drag-and-drop design tooling;
- advanced offline synchronization;
- repository publication, production deployment, or Flutter implementation as an
  implied consequence of backend completion.

## Release completion boundary

Release preparation is complete only after documentation and repository cleanup
pass, protected/private boundaries are reverified, the final name and license are
decided, accepted staging is promoted to `main` under an explicit checkpoint, and
the owner authorizes any GitHub remote or push. Flutter remains subsequent work.
