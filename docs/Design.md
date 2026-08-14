# Backend Product/UX Contract

Flutter is deferred, but backend APIs must support a simple future admin journey.

## Canonical journey

```text
Login
→ Dashboard
→ Wedding event
→ Guest/event policy
→ Card design/photo setup
→ Excel import
→ Import preview
→ Fix invalid rows
→ Confirm guests
→ Generate QR/cards
→ Generated cards
→ Scanner
→ Attendance
→ Reports
```

## Excel preview UX requirements

Backend must provide enough structured data for Flutter to show:

- total candidate rows
- valid rows
- rows needing correction
- exact field errors per row
- duplicate state
- ignored/unmapped columns
- mapping decisions
- whether a row is importable
- current correction revision

The administrator should be able to edit a staged invalid row without editing the original Excel file.

## Card designer UX requirements

Constrained design, not free-form drag/drop.

Admin can:

- select approved preset
- upload/select couple photo
- adjust crop/focus/zoom within allowed controls
- edit permitted event text/name presentation
- preview
- explicitly approve/finalize

Coordinates/layout remain template-controlled.

## Event-day UX requirements

Gate scan result must be immediate and clear:

- valid guest
- already checked in
- invalid QR
- inactive QR
- wrong event
- companion allowance/admission

Backend error contracts must be safe and actionable for this UI.
