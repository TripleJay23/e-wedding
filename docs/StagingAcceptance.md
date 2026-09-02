# Staging Acceptance — Jordan & Jessica Wedding

## Accepted release journey

The backend completed one continuous authenticated staging scenario through the
full replay and release gate. Its purpose is to remain a coherent regression
runbook, not to expose private event or guest evidence.

```text
S1  authentication and event continuity
S2  event policy and guest fields
S3  explicitly supplied workbook upload and preview
S4  partial confirmation
S5  staged-row correction, revalidation, and later progression
S6  re-import and duplicate safety
S7  real QR issuance
S8  exact card design and canonical photo
S9  permanent cards with real QR images
S10 gate validation and companion admission
S11 append-only attendance corrections
S12 reconciled JSON, CSV, and XLSX reports
S13 complete replay, privacy/security checks, and backend release gate
```

## Replay header

Every future staging replay reports:

```text
Scenario: Jordan & Jessica Wedding
Checkpoint: <name>
Previous accepted checkpoint: <name>
Branch/HEAD: <staging commit>
Database revision: <revision>
Existing event identity/details: <read from persisted state; do not invent>
Input explicitly supplied: <yes/no plus privacy-safe digest where applicable>
Expected behavior: <bounded contract>
Observed behavior: <privacy-safe evidence>
Outcome: PASS or BLOCKED
Next exact checkpoint: <name>
```

## Continuity and failure rules

- read existing persisted state before any authorized mutation;
- never replace the scenario with disconnected synthetic events;
- never reuse a private workbook unless the checkpoint explicitly supplies it;
- report only aggregate, PII-free evidence;
- keep blocked batches and immutable gate evidence intact;
- stop at the first confirmed source defect and repair it through a focused
  `feature/* -> dev -> staging` path before replaying the same checkpoint;
- preserve the canonical database, private storage, container, and volume unless
  a separately authorized recovery/reset checkpoint says otherwise.
