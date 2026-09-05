# Project Charter

## Purpose

Provide e-Wedding as a secure, deterministic wedding invitation, e-card,
guest-validation, attendance, and reporting platform that supports the full event
journey and a future mobile administrator/gate client.

## Current state

The e-Wedding backend release is finalized. Backend functionality through reports
passed the continuous staging replay and final release gate, and `main` was
promoted by pure fast-forward. At the accepted technical release baseline
`6891e644472e81d5bc3690dc9ebc53fff9511ebf`, `main`, `staging`, and `dev` were
converged. Repository cleanup, legacy-repository retirement, and the final local
rename to `e-wedding` are complete.

A private GitHub repository is established and the three permanent branches have
been pushed. Backend GitHub Actions CI is operational, Dependabot automation is
complete, and GitHub-hosted branch protection/ruleset enforcement is deferred
because of the private-plan limitation; the linear fast-forward procedure remains
mandatory. Excel ingestion is hardened through H2. Flutter remains intentionally
unimplemented and deferred until companion entitlement lifecycle hardening is
separately audited, contracted, and completed.

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

Backend technical release finalization and the authorized private-repository
baseline are complete. The accepted technical release evidence remains associated
with `6891e644472e81d5bc3690dc9ebc53fff9511ebf`; a later documentation-only
governance commit does not imply that the full release suite was rerun on that
newer commit. H2 has a separate dated closeout baseline. Companion entitlement
lifecycle hardening remains the next backend program; it must distinguish
invitation maximum, first-scan declared entitlement, remaining slots, and actual
admission without treating that planned behavior as implemented. Public licensing
or publication, deployment, future remote changes or pushes, and Flutter
implementation remain separately controlled work.
