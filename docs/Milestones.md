# Backend Milestones

Milestones are sequential gates. Later implementation may be informed by the frozen legacy project, but a later milestone must not distract from an unresolved earlier acceptance gate.

## M0 — Legacy recovery and clean repository foundation

Deliver:

- fresh repository
- root documentation
- `.gitignore`
- `.env.example`
- license
- backend package/tooling skeleton
- PostgreSQL dev/test/staging environment plan
- legacy recovery report
- exact approved card package located and fingerprinted

Gate: no implementation starts until the user reviews the recovery report and locked decisions.

## M1 — FastAPI / PostgreSQL foundation

Deliver:

- application factory
- configuration
- DB session lifecycle
- SQLAlchemy base
- Alembic baseline
- health endpoint
- test harness
- disposable PostgreSQL safety guard

## M2 — Administrator authentication

Deliver:

- user persistence
- secure password hashing
- admin bootstrap
- login
- `/auth/me`
- bearer authorization
- inactive-user rejection

## M3 — Event and guest policy foundation

Deliver:

- events
- event status
- configured guest fields
- default canonical fields
- `max_companions_per_invitation` default **2**
- manual guest foundation

## M4 — Excel Import COMPLETE

This milestone is not complete at upload/preview alone.

Deliver:

1. safe XLSX upload
2. sheet/header detection
3. configured-field mapping
4. title/banner/footer/blank classification
5. formula non-execution
6. normalization
7. row validation
8. duplicate detection
9. persisted batches/rows
10. preview
11. partial confirmation of valid rows
12. exact guest reconciliation
13. invalid staged-row correction API/service
14. revalidation after correction
15. later confirmation of newly eligible corrected rows
16. duplicate/re-import behavior
17. import history
18. real user-supplied workbook staging acceptance, including gaps/incomplete values

Gate: Jordan & Jessica Excel journey passes end to end.

## M5 — Secure QR credentials

Deliver:

- secure opaque token issuance
- hash-at-rest
- event/guest binding
- active/inactive/reissue
- QR image generation
- decode verification
- no PII

Gate: real imported staging guests receive credentials and lifecycle tests pass.

## M6 — Exact card design and permanent generation

Deliver:

- recover exact approved Wedding Floral template package
- template registration/versioning
- event photo validation/storage
- event card design revisions
- photo fit/focus/zoom controls
- design approval
- generated-card persistence
- permanent card generation using real guest QR
- deterministic PNG
- bulk generation/export
- idempotency

Gate: Jordan & Jessica staging guests receive exact approved personalized cards with real decodable QR credentials.

## M7 — Gate validation and attendance

Deliver:

- scan endpoint/service
- valid scan
- wrong event
- unknown token
- inactive token
- duplicate scan
- companion admission bounded by guest/event policy
- immutable gate evidence
- attendance records

## M8 — Attendance corrections

Deliver:

- authorized correction decisions
- effective-state reducer
- audit history
- deterministic resolution

## M9 — Reports and exports

Deliver:

- event summary
- guest attendance roster
- named-holder counts
- companion capacity
- admitted companion counts
- total people counts
- JSON
- CSV
- XLSX
- privacy-safe exports

Gate: reports reconcile exactly with Jordan & Jessica gate/attendance scenario.

## M10 — Full staging acceptance

Run the complete continuous scenario from authentication through reports with no skipped product checkpoint.

No new features unless a staging defect requires them.

## M11 — Documentation reconciliation and release gate

Update:

- Architecture
- Database
- API
- Security
- Workflow
- Milestones
- Design/card docs
- README
- environment/runbooks

Then run:

- complete test suite
- migration audit
- dependency/security review
- private-file Git audit
- route/OpenAPI review
- staging DB backup/recovery verification

## M12 — Promote to main and GitHub handoff

- fast-forward accepted staging state to `main`
- run final `main` verification
- no private artifacts tracked
- user explicitly authorizes and performs/controls GitHub push

After M12, begin Flutter planning/implementation.
