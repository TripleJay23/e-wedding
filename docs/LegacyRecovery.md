# Legacy/Frozen Project Recovery Procedure

## Purpose

The frozen previous project contains valuable evidence: validated requirements, exact card assets/packages, migration history, tests, and resolved edge cases. The rebuild must recover that knowledge without inheriting drift or copying the old backend wholesale.

## Recovery principle

```text
Frozen project = evidence and artifact source
New repository = clean implementation
```

## Read-only audit targets

The implementation agent that can access the frozen folder should inspect, without modifying it:

1. root documentation and historical planning files;
2. Git branches/log/history to understand accepted decisions;
3. backend models/services/tests for contract evidence;
4. Alembic history for schema concepts and important constraints;
5. Excel import tests for discovered real-world edge cases;
6. QR security tests/contracts;
7. template catalogue and rendering tests;
8. exact Wedding Floral template packages/assets/fonts;
9. event-photo framing controls;
10. attendance/correction/report semantics;
11. environment and staging safety tooling.

Do not read real guest row contents, private wedding photos, or secrets merely for discovery.

## Classification output

Create a `LegacyRecoveryReport.md` with four buckets:

### REUSE EXACTLY

Examples:

- exact approved template package and its logical files
- exact private asset/font files where rights permit private use
- immutable hashes/digests
- accepted visual geometry/manifests

### REIMPLEMENT FROM CONTRACT

Examples:

- authentication behavior
- Excel import semantics
- QR lifecycle
- attendance rules
- API behavior

Use old source/tests as evidence, but implement cleanly in the new architecture.

### IGNORE

Examples:

- abandoned experiments
- duplicate or contradictory docs
- one-off debugging harnesses
- obsolete branches
- accidental staging state
- features outside current milestone

### USER DECISION REQUIRED

Anything contradictory or not safely inferable.

## High-value facts already known

Use these as search anchors in the frozen folder:

- approved template key: `wedding_floral`
- approved active design target: Version 10
- V10 raw package SHA-256: `b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782`
- approved default preview SHA-256: `1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8`
- V10 canvas: `1240 × 1748`
- preview QR payload: `PREVIEW-QR-DO-NOT-SCAN`
- V10 package contains `manifest.json`, six assets, two fonts
- photo target geometry: 536 × 536 at x=352, y=166
- generic photo controls: COVER/CONTAIN, focus x/y, zoom

Search by digest first rather than guessing filenames.

## Do not copy blindly

Do not copy:

- old `backend/app` wholesale
- old migrations wholesale
- old `.env`
- old databases
- staging backups
- private spreadsheets
- real guest data
- generated cards
- real QR tokens

## Completion gate

No backend implementation begins until the recovery report has:

- confirmed the exact card package or identified why it cannot be recovered;
- listed stable contracts worth carrying forward;
- identified contradictory legacy decisions;
- confirmed no private data was copied into the new repo.
