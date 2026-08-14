# Project Charter

## Purpose

Build a secure, professional, mobile-first wedding e-card and guest validation system whose backend is complete and proven before Flutter implementation begins.

The system manages the full event journey from guest import to gate entry and reporting.

## Primary user

The MVP has one authenticated administrator account with full administrative capability. Additional user roles are deferred unless explicitly reintroduced later.

## Core backend outcome

A completed backend must support this end-to-end flow:

```text
Admin authentication
→ Wedding event setup
→ Guest field/policy configuration
→ Excel guest import
→ Validation and preview
→ Import valid rows
→ Correct invalid staged rows and revalidate
→ Guest management
→ Secure QR issuance
→ Exact approved wedding-card design finalization
→ Permanent personalized card generation
→ Card download/export
→ QR gate validation
→ Attendance + companion admission
→ Attendance corrections
→ Event reports and exports
```

## Locked product decisions

- Backend: FastAPI / Python.
- Database: PostgreSQL across development, disposable integration tests, staging, and production later.
- Mobile: Flutter after backend completion.
- Git: `feature/* → dev → staging → main`.
- Agents commit locally only unless the user explicitly authorizes remote operations.
- System default maximum companions per invitation: **2**.
- Excel validation errors do not necessarily block valid rows; valid rows can be imported while invalid rows remain for correction.
- Invalid staged import rows must have an administrator correction/revalidation workflow before Excel Import is declared complete.
- The exact approved Wedding Floral card design must be preserved rather than recreated.
- Preview QR codes are synthetic and non-resolving; permanent cards use real secure guest QR credentials.
- Documentation reconciliation happens after successful full staging acceptance, before promotion to `main`.

## Non-goals for backend MVP

Do not implement without explicit approval:

- payments or billing
- SMS/WhatsApp automation
- public RSVP portal
- multi-tenant SaaS billing
- AI guest analytics
- unconstrained drag-and-drop card designer
- social media automation
- advanced offline synchronization

## Definition of backend done

The backend is done only when:

1. every backend milestone is implemented;
2. focused and full regression tests pass;
3. the continuous Jordan & Jessica staging scenario passes end to end;
4. Excel import handles incomplete data as designed;
5. real QR credentials are used on permanent generated cards;
6. gate/attendance/report flows reconcile correctly;
7. security/privacy checks pass;
8. documentation is reconciled to actual tested behavior;
9. `staging` is accepted and promoted to `main` locally;
10. final `main` verification passes.
