# Staging Acceptance — Jordan & Jessica Wedding

## Purpose

Keep the entire backend acceptance campaign coherent and visible.

The same wedding scenario continues across checkpoints.

## Report header

Every staging report begins with:

```text
Scenario: Jordan & Jessica Wedding
Checkpoint: <name>
Previous accepted checkpoint: <name>
Branch/HEAD: <staging commit>
Database revision: <revision>
Existing event identity/details: <read from persisted state, do not invent>
Input explicitly supplied for this checkpoint: <yes/no + digest where applicable>
```

## Journey

### S1 Authentication and event continuity

Prove admin authentication and read the persisted Jordan & Jessica event.

### S2 Event policy and guest fields

Verify configured fields and default/event companion maximum. Default is 2 unless the persisted event intentionally overrides it.

### S3 Excel upload/preview

Use the workbook explicitly supplied by the user.

Prove structure, mapping, row classification, validation, duplicate analysis, preview.

### S4 Partial confirmation

Import valid rows only. Reconcile exact created count and no invalid leakage.

### S5 Invalid-row correction

Correct representative gaps/errors through the backend correction workflow; revalidate and import newly eligible rows.

### S6 Re-import/duplicates

Prove safe repeated upload/confirmation behavior.

### S7 QR issuance

Issue real secure credentials for confirmed guests.

### S8 Event card design

Recover/use exact approved Wedding Floral design and selected couple photo; approve design revision.

### S9 Permanent cards

Generate personalized cards for real staging guests using real QR credentials.

### S10 Gate validation

Test valid, duplicate, wrong event, inactive/unknown token and companion admission.

### S11 Attendance corrections

Apply and reconcile correction decisions.

### S12 Reports

Verify JSON/CSV/XLSX counts reconcile with the same event.

### S13 Full replay / release gate

Run final complete backend acceptance, privacy/security checks, and data reconciliation.

## Failure rule

A failed checkpoint stops progress. Fix it through feature → dev → staging, then repeat the same checkpoint with the same relevant input.
