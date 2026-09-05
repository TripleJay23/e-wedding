# Excel Import Product Contract

This is the implemented H2 closeout contract for deterministic staged XLSX
ingestion. It introduces no future companion-declaration fields.

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

Event configuration determines enabled/required fields. `full_name` and
`invitation_type` are the current required defaults.

## Event companion policy

`allowed_companions` is the invitation maximum. The event policy is authoritative;
its current default and system maximum are **2**. Spreadsheet values do not
redefine that policy, and the parser does not own the policy ceiling.

After a header maps a column to `allowed_companions`, non-negative decimal text
with an optional all-zero fractional part is normalized exactly to an integer.
Surrounding whitespace is ignored. Signs, scientific notation, locale separators,
fractional values, prose, booleans, and formulas remain invalid; the normalized
integer is still checked against the event maximum.

Examples: blank becomes `0`; numeric `0`, `1`, or `2`, text `"0"`, `"1"`, or
`"2"`, and exact zero-fraction text `"2.0"` or `"2.00"` become integers. `2.5`,
`-1`, `+1`, `1e0`, `two`, `1,0`, and prose are invalid. Text `"3"` normalizes to
integer `3` and then fails the current event maximum.

`table_number` is an identifier, not a count. Values such as `20`, `02`, `A4`,
and `VIP-2` do not contribute to companion calculations.

## Workflow

```text
Upload workbook
→ validate bounded XLSX envelope and package
→ discover visible-sheet/header candidates
→ deterministic semantic header mapping and bounded viability selection
→ classify each post-header row
→ normalize fields by their own rules
→ validate all candidate guest rows
→ analyze duplicates
→ persist batch + row evidence
→ bounded preview
→ progress all or exact selected eligible rows
→ keep invalid rows staged
→ admin edits invalid staged values
→ revalidate corrected rows
→ progress newly eligible rows
→ reconcile all persisted guests
```

## Sheet and header selection

Only `.xlsx` workbooks are supported. Only visible worksheets compete. Header
discovery examines at most the first 100 rows of each visible worksheet. Every
eligible header must map all configured required fields, map at least two
canonical fields, and contain no duplicate canonical mapping.

Selection then probes at most 100 rows after each eligible header. It ranks
affirmative guest-table evidence before mapped-header count: plausible candidate
rows and actual mapped-field occupancy count even when field validation will fail.
Blank, presentation, footer, and established repeated-header rows do not create
guest viability. Independent plausible tables that cannot be selected safely fail
closed with `AMBIGUOUS_GUEST_TABLE`; hidden sheets never affect that decision.

Header aliases remain a small explicit registry. `Phone No` is accepted for
`phone_number` (including punctuation and case variants handled by normal header
normalization). Generic `Type` remains unsupported because it can refer to
invitation type, category, or organizer-defined data without deterministic
header evidence.

Aliases are explicit, normalized, collision-free, and deterministic. There is no
fuzzy matching, semantic embedding, network/AI interpretation, or value-driven
field inference. Repeated internal headers can remain inside the one selected
guest region when deterministic evidence proves their mapping; the selected sheet
is parsed once after discovery.

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

The governing bias is: strong structural evidence is structural; strong guest
evidence is a candidate; weak or uncertain post-header evidence remains a visible
candidate. Never hide a malformed guest simply because it occurs at the bottom of
the sheet.
Post-header traversal continues through the bounded worksheet even after blanks,
presentation separators, repeated headers, or proven footers. A deterministic
repeated header may reorder the selected canonical fields or omit optional fields;
its mapping applies to the following section, while partial header-like rows remain
guest candidates.

Formula-backed footers are classified row by row rather than by starting a
one-way footer region. Each footer requires exactly one supported `SUM`, `COUNT`,
or `COUNTA` formula over preceding mapped guest rows plus one text label outside
the active guest columns. Multiple such rows can be structural, but intervening
malformed guests remain candidates and later guests are still traversed. Unmapped
notes without equally strong structural evidence remain visible invalid candidates.

## Formula behavior

Never evaluate formulas.

A formula in a genuine guest row is invalid.

A formula-containing footer can be ignored only when the entire row is independently proven structural.

Excel error-typed cells are not accepted as ordinary guest text and produce
deterministic field-associated invalidation. Literal text such as `"#N/A"` is not
automatically an Excel error cell. Unsupported cell types are invalid rather than
coerced into misleading canonical values.

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

Shared validation also enforces persistence-compatible text bounds: phone numbers
are at most 32 characters and email addresses at most 320. Values that cannot fit
the `Guest` contract cannot reach import `VALID` state.

## Evidence and storage

For every staged candidate the database preserves the source-row relationship, an
immutable normalized original canonical snapshot, current corrected canonical
values, and append-only correction/revalidation history. Canonical JSON is not a
byte-for-byte cell archive: display formatting, outer whitespace, original email
casing, formula source text as a canonical value, and Excel error type as ordinary
text are not necessarily preserved there.

The protected original XLSX is the full source evidence for successful imports.
Known storage/persistence failures clean up the newly written evidence. A process
crash between the evidence write and database commit can leave a recoverable orphan
artifact; H2 accepts this as operational recovery debt, not a data-integrity
failure, and does not add garbage collection.

## Bounded surfaces

Current defaults are a 10 MiB upload, 5,000 candidate rows, a 10,000-row by
100-column worksheet, a 100-row header scan, and a 100-row viability probe. Staged
row and history API pages are limited to 100; batch detail is aggregate and bounded
rather than a 5,000-row response.

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

- admin retrieves an unlinked preview row and its current validation state
- admin edits one or more canonical staged values while the row is `VALID`, `INVALID`, or
  `DUPLICATE`
- optimistic version check prevents stale overwrite
- original imported snapshot remains auditable
- every effective correction becomes non-progressable until explicit revalidation completes
- corrected state is revalidated under the same or explicitly refreshed policy snapshot
- once eligible, row can be confirmed/imported
- imported or guest-linked staged rows cannot be corrected through the import staging surface
- previously imported rows are never duplicated

The mobile app will later expose this as an edit/fix flow in the Import Preview screen.

## Acceptance workbook behavior

The real user-supplied workbook with gaps must be explicitly supplied for the staging checkpoint. Never silently reuse an older cached workbook.
