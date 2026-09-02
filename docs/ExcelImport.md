# Excel Import Product Contract

This is a critical backend milestone.

## User goal

A wedding organizer should be able to upload a realistic Excel guest list, understand what the system detected, import valid guests, and correct incomplete/invalid rows without manually rebuilding the entire workbook.

## Canonical initial fields

```text
full_name
phone_number
email
invitation_type
allowed_companions
table_number
category
```

Event configuration determines enabled/required fields.

`full_name` and `invitation_type` are expected required defaults unless the final product decision changes explicitly.

## Event companion policy

Default maximum companions per invitation: **2**.

Spreadsheet values do not redefine this policy.

## Workflow

```text
Upload workbook
→ detect relevant visible sheet
→ detect header row
→ deterministic header mapping
→ classify structural/non-data rows
→ normalize values losslessly
→ validate all candidate guest rows
→ analyze duplicates
→ persist batch + row evidence
→ preview
→ confirm valid/eligible rows only
→ keep invalid rows staged
→ admin edits invalid staged values
→ revalidate corrected rows
→ confirm newly eligible rows
→ reconcile all persisted guests
```

## Structural behavior

Must handle:

- title rows
- merged presentation headings
- blank spacer rows
- reordered columns
- safe configured aliases
- extra undefined columns according to explicit contract
- repeated headers where confidently structural
- footer/summary rows with strong structural evidence
- gaps/blank required values inside genuine guest rows

Never hide a malformed guest simply because it occurs at the bottom of the sheet.

## Formula behavior

Never evaluate formulas.

A formula in a genuine guest row is invalid.

A formula-containing footer can be ignored only when the entire row is independently proven structural.

## Validation behavior

At minimum:

- required field missing
- invalid invitation type
- invalid companion count
- companion limit exceeded
- malformed phone/email according to accepted rules
- duplicate within upload
- duplicate against current event
- unsupported formula/value type

## Partial import behavior

Validation errors do not make the entire workbook unusable.

Example:

```text
50 candidates
43 valid
7 invalid
```

After administrator review, the system may import the 43 valid rows while retaining the 7 invalid staged rows for correction.

Progression uses the existing batch endpoint. With no request body, it imports all
currently eligible `VALID` rows. A request may instead provide a non-empty, unique
`staged_row_ids` list of persisted staged-row UUIDs to import exactly that subset.
Every selected row must belong to the requested batch and be either currently
`VALID` or already `IMPORTED`; invalid selection is rejected atomically. Already
imported selected rows are idempotent no-ops, and unselected rows remain unchanged.
An explicit empty selection never means "import all."

## Staged-row correction

Required capability:

- admin retrieves invalid row and errors
- admin edits one or more canonical staged values
- optimistic version check prevents stale overwrite
- original imported snapshot remains auditable
- corrected state is revalidated under the same or explicitly refreshed policy snapshot
- once eligible, row can be confirmed/imported
- previously imported rows are never duplicated

The mobile app will later expose this as an edit/fix flow in the Import Preview screen.

## Acceptance workbook behavior

The real user-supplied workbook with gaps must be explicitly supplied for the staging checkpoint. Never silently reuse an older cached workbook.
