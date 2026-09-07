# Project Charter

## Purpose

Provide e-Wedding as a secure, deterministic wedding invitation, e-card,
guest-validation, attendance, and reporting platform that supports the full event
journey and a future mobile administrator/gate client.

## Current state

The e-Wedding backend release is finalized through C2.8. The companion entitlement
lifecycle is accepted and integrated across local `dev`, `staging`, and `main`,
including event-day scanning, append-only corrections, and reconciled reports.
Canonical staging was forward-migrated to Alembic `20260906_0013` with its data and
private-resource boundaries preserved. Final repository cleanup is accepted, and
the former frozen legacy repository has been verified absent and no longer
required.

A private GitHub repository is established, and its earlier backend baseline has
been published. The current C2 and release-finalization changes have not yet been
published. Backend GitHub Actions CI is operational, Dependabot automation is
complete, and GitHub-hosted branch protection/ruleset enforcement is deferred
because of the private-plan limitation; the linear fast-forward procedure remains
mandatory. Excel ingestion is hardened through H2. Flutter remains intentionally
unimplemented.

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
- one invitation holder owns a companion entitlement lifecycle: invitation maximum,
  locked declaration, cumulative admitted companions, and remaining declared slots
  are distinct; companions do not receive guest identities, credentials, or cards;
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
baseline are complete. The earlier release evidence remains associated with its
dated technical baseline; later governance-only changes do not imply an unrecorded
rerun. H2 and C2 retain their separate acceptance evidence. C2 is integrated into
the local permanent branches, while the accepted repository-cleanup change remains
on the release-finalization line pending separate promotion. Publication of the
current release, deployment, future remote changes or pushes, public licensing,
and Flutter implementation remain separately controlled work.
