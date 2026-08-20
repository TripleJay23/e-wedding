# Legacy Recovery Report

Status: **M0 targeted audit complete - awaiting user review**  
Audit date: 2026-08-14  
New project: `C:\Users\jtrip\Desktop\Projects\e-card-system-v2`  
Frozen evidence source: `C:\Users\jtrip\Desktop\Projects\e-card-system-FROZEN`

## 1. Scope and control baseline

This report is the only deliverable from the initial recovery phase required by
`MASTER_BOOTSTRAP_PROMPT.md`. No backend feature, migration, database, Git branch,
commit, remote, or Flutter work was created.

The following control documents were read before the audit:

- `AGENTS.md`
- `README.md`
- `docs/ProjectCharter.md`
- `docs/Architecture.md`
- `docs/Workflow.md`
- `docs/Milestones.md`
- `docs/LegacyRecovery.md`
- `docs/CardDesignReference.md`
- `docs/ExcelImport.md`
- `docs/Security.md`

Relevant repository-foundation guidance in `docs/RepositorySetup.md` and
`docs/Environment.md` was also reviewed for the required foundation assessment.

Current checkpoint: **M0 - targeted legacy recovery and clean-foundation assessment**.  
Exact acceptance criterion: locate and independently fingerprint the approved
Wedding Floral Version 10 package, recover only stable legacy contracts, classify
the findings, confirm that no private material was copied, and stop for review.  
Previous accepted baseline: the supplied bootstrap/control pack; the new folder is
not yet a Git repository and therefore has no branch or HEAD.  
Expected file change: this report only.  
Stop condition: this report exists and the audit evidence is verified.  
Next checkpoint: user review and resolution of the decisions in section 7 before
repository initialization or implementation.

### Frozen checkout identity

- Branch observed read-only: `staging`
- HEAD: `ca9c8ddaef41eefcba41a019c90390bfaa9fcbb0`
- HEAD subject: `fix(imports): classify trailing workbook footers safely`
- Parent: `dd3c87d70493663bd82f73018cd0283b112eee00`
- Git history and refs were read with a command-local safe-directory override.
- No frozen Git configuration, index, ref, file, database, or storage object was
  changed. Tracked worktree cleanliness was not used as artifact evidence because
  this strict read-only audit deliberately avoided a Git status/index refresh.

### Privacy boundary

The audit did not open or copy any `.env`, database, backup, guest workbook, guest
export, real wedding photograph, generated guest card, raw real QR credential, or
staging data. The approved synthetic default preview was the only rendered image
inspected. Archive verification read ZIP entries in memory without extraction.

## 2. Decision summary

| Area | Proven legacy behavior | New rebuild decision | Evidence |
|---|---|---|---|
| Wedding Floral V10 | Exact candidate and canonical archives are byte-identical; package, manifest, logical-file, and preview fingerprints match | Reuse the exact private V10 bytes only after the private-storage and rights decisions are approved; never redraw | Frozen candidate/canonical paths and section 3 inventory |
| Template engine | Strict Manifest V1, closed placeholders/capabilities, generic layers, deterministic rendering, bounded package validation | Reimplement the generic validator/renderer cleanly; preserve the V10 manifest and geometry exactly | `backend/app/template_engine/`; template tests; V10 `manifest.json` |
| Excel import | Deterministic mapping, safe XLSX inspection, structural classification, formula non-execution, immutable snapshots, partial confirmation, duplicate and policy rechecks | Reimplement from contract and add the required correction/revalidation layer that legacy lacks | `backend/app/imports/`; import services/models/tests |
| QR credentials | Opaque payload, no PII, hash at rest, scoped lifecycle, safe resolution, decodable QR | Reimplement the security properties; do not choose the legacy deterministic derivation scheme until section 7 is resolved | QR model/service/security tests |
| Permanent cards | Approved revision and all render inputs are pinned; output is deterministic, private, idempotent, and contains a real decodable QR | Reimplement from contract after M5, using the exact V10 package and the approved QR recovery boundary | generated-card services/models/tests |
| Gate and attendance | Validation is non-mutating; admission records actual companions once; raw evidence is immutable; duplicates do not add companions | Reimplement these domain semantics at M7 | gate/attendance services and tests |
| Corrections and reports | Corrections are append-only; effective state is reduced from raw evidence plus corrections; invitation capacity, raw gate counts, and effective people remain separate | Reimplement at M8/M9; retain identifier-only, spreadsheet-safe export rules | correction/report services and privacy/headcount tests |
| Schema/migrations | Legacy migrations encode useful constraints and several careful nullable/backfill/constraint transitions | Learn from constraints and failure modes only; start one clean PostgreSQL Alembic history | legacy revisions `0001` through `0021`; disposable/readiness guards |
| Repository foundation | Root control pack and placeholder backend directories exist; no private artifacts were found in the new tree | Keep M0 limited to review; initialize Git/branches only at the next approved checkpoint | section 8 |

## 3. REUSE EXACTLY

### 3.1 Approved Wedding Floral Version 10 artifact

Safe read-only source paths:

```text
Candidate archive:
C:\Users\jtrip\Desktop\Projects\e-card-system-FROZEN\backend\storage\private\candidates\wedding_floral\v10\output\wedding_floral_v10_candidate.template-package.zip

Canonical archive:
C:\Users\jtrip\Desktop\Projects\e-card-system-FROZEN\backend\storage\private\template_packages\verified\75446293b53acf292b8900e61042239c01ad80b6ed66365e30710834ded6eed4.template-package.zip

Logical source tree:
C:\Users\jtrip\Desktop\Projects\e-card-system-FROZEN\backend\storage\private\candidates\wedding_floral\v10\source

Approved synthetic default preview:
C:\Users\jtrip\Desktop\Projects\e-card-system-FROZEN\backend\storage\private\candidates\wedding_floral\v10\output\wedding_floral_v10_default_preview.png
```

Independent verification result: **PASS**.

| Check | Expected | Observed |
|---|---|---|
| Template identity | `wedding_floral`, Version `10` | Exact match in manifest |
| Canvas | `1240 x 1748` | Exact match in manifest and preview |
| Raw archive SHA-256 | `b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782` | Exact for candidate and canonical archives |
| Logical package SHA-256 | `75446293b53acf292b8900e61042239c01ad80b6ed66365e30710834ded6eed4` | Independently reproduced from the canonical logical index |
| Manifest SHA-256 | `ccf84b1cb02716c1d0b5c9b830203d1399675a41aacedd02b2b52ac917bb01be` | Exact match |
| Default preview SHA-256 | `1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8` | Exact match |
| Preview image | `1240 x 1748`, deterministic reference | `1240 x 1748`, RGBA, no PNG metadata |
| Preview QR | `PREVIEW-QR-DO-NOT-SCAN` | Independently decoded as one QR Code with the exact payload |
| Logical entries | 9 unique entries | 9 unique entries: manifest + 6 assets + 2 fonts |
| ZIP safety | No extra/directory/traversal entries | 0 directory entries, 0 duplicate names, 0 absolute/traversal paths |
| Source-to-archive identity | Every logical source file matches | 9/9 size and SHA-256 comparisons match |
| Candidate-to-canonical identity | Unchanged approved bytes | All 598,060 bytes compare equal |

The synthetic preview was visually inspected and matches the documented floral,
photo, QR, heading, script-name, invitation-panel, date, venue, and lower-floral
composition. No real photograph was opened.

### 3.2 Exact logical inventory

| Kind | Package path | Bytes | SHA-256 |
|---|---|---:|---|
| Manifest | `manifest.json` | 13,468 | `ccf84b1cb02716c1d0b5c9b830203d1399675a41aacedd02b2b52ac917bb01be` |
| Raster asset | `assets/bottom_left_floral.png` | 74,231 | `f1f6e07d53bb34147e29e66847ef9ec75b565b4a27f3fed1fe66b128536e319d` |
| Raster asset | `assets/bottom_right_floral.png` | 74,096 | `031fde58dff37b2854df2096367f841c221a38ecce60ff494e5d3e436d520ac7` |
| Raster asset | `assets/photo_floral_frame.png` | 141,786 | `8d13590ccbbe36ae62f0e2108f7f944e0d2e164fb7d32a4f52a39d634d4d139a` |
| Raster asset | `assets/photo_gold_rings.png` | 134,226 | `3e28304cf1f029d3b74a72df6dd4b4f122a24a61cc47b2549e9d7a861c013ea6` |
| SVG asset | `assets/qr_frame.svg` | 204 | `1b96180158dfe49f10532331893f80945a240a444f55d04a87f43597205bc568` |
| Raster asset | `assets/top_right_floral.png` | 108,997 | `fd494cce736a1fef0da9d20154497818c6618a8f234db34d374c0285b1f1c28d` |
| OTF font | `fonts/body_font.otf` | 53,116 | `de1eddf3f49fbc0782a1008a87053207f69174a3860f7068520ee4cd901ef926` |
| TTF font | `fonts/couple_script.ttf` | 73,996 | `9706c0bb3f05f576e5fe13abcad1bb5e27c76b0b0da055c94e2a2cd88c51bd5d` |

The package contains only these logical entries. It contains no real photo,
provenance record, review evidence, QR credential, or generated card.

### 3.3 Exact V10 manifest contract

Preserve the validated manifest bytes and therefore all of these properties:

- schema version `1`, template key `wedding_floral`, template version `10`;
- the declared six asset and two font identities above;
- the complete generic layer order, geometry, colors, text runs, formatters, and
  fitting rules;
- QR frame `x=63, y=70, width=232, height=232` and QR image
  `x=80, y=87, width=198, height=198`;
- `primary_photo` frame `x=352, y=166, width=536, height=536`, elliptical mask,
  default `COVER`, focus `(0.5, 0.5)`, and zoom `1.0`;
- generic controls `PHOTO_CROP_OVERRIDE`, X/Y `PHOTO_FOCUS_OVERRIDE`, and
  `PHOTO_SCALE_OVERRIDE` bounded `1.0..3.0`;
- the exact closed placeholder contract used by this package:
  `event.address`, `event.couple_names`, `event.date`, `event.primary_photo`,
  `event.time`, `event.venue`, `guest.full_name`, and `system.qr_image`.

The approved default preview digest is a regression oracle. Neither the preview
nor the private package is authorized for Git by this report.

## 4. REIMPLEMENT FROM CONTRACT

### 4.1 Excel import

Stable behavior worth carrying forward:

- Seven configured canonical fields: `full_name`, `phone_number`, `email`,
  `invitation_type`, `allowed_companions`, `table_number`, and `category`.
- Default required fields were `full_name` and `invitation_type`; aliases and
  required/enabled state came from a frozen event configuration snapshot.
- Header matching normalized configured canonical keys, labels, and aliases;
  ambiguous matches, duplicate mapped fields, and missing required headers failed
  closed. Unknown and disabled columns were ignored without becoming guest data.
- Visible sheet/header discovery was bounded and deterministic. Equal candidates
  failed closed instead of using workbook order.
- XLSX package validation bounded size/entries/relationships and rejected unsafe
  package features. Workbooks were loaded read-only with `data_only=False` and
  `keep_links=False`; formulas were represented, never evaluated.
- Presentation rows, blanks, repeated headers, and terminal formula summaries were
  ignored only with strong structural proof. Sparse, malformed, or invalid trailing
  guest rows remained visible validation failures.
- Scalar normalization was lossless: unsafe numeric phone/table values,
  non-integral companion counts, formulas, non-finite values, and unsupported cell
  types failed rather than being guessed.
- Batch metadata, field mapping, source row number, normalized data, errors, and
  policy context were persisted as an immutable preview snapshot.
- Valid rows could be confirmed while invalid rows remained visible. Duplicate
  analysis was rerun against current event state, and the event companion policy
  was locked and rechecked immediately before persistence.
- Confirmation was atomic and idempotency/conflict behavior was bounded.

Legacy gap that must be corrected in the rebuild:

`ImportBatchRow` explicitly rejected every update, and confirmation moved the whole
batch to `CONFIRMED`. No import-row correction/revalidation route or service was
present. The clean implementation must preserve an immutable original snapshot
while adding a separately versioned administrator correction state, optimistic
version checking, revalidation, and later confirmation of newly eligible rows as
required by `docs/ExcelImport.md` and M4.

Evidence anchors:

- `backend/app/services/guest_field_defaults.py`
- `backend/app/imports/header_mapping.py`
- `backend/app/imports/parser.py`
- `backend/app/imports/value_normalization.py`
- `backend/app/imports/row_validation.py`
- `backend/app/imports/xlsx_security.py`
- `backend/app/models/import_batch.py`
- `backend/app/models/import_batch_row.py`
- `backend/app/services/import_confirmations.py`
- `backend/tests/test_guest_import_structured_workbooks.py`
- `backend/tests/test_guest_import_value_normalization.py`
- `backend/tests/test_import_batch_service.py`
- `backend/tests/test_import_confirmation_atomicity.py`
- `backend/tests/test_import_confirmation_companion_policy.py`

No real workbook or row content was inspected.

### 4.2 QR security and lifecycle

Carry forward the behavior, not the implementation:

- one event/guest-scoped credential identity with active/revoked and explicit
  rotation/reissue behavior;
- opaque high-entropy payload containing no guest or event PII;
- a normalized 64-character lowercase SHA-256 verifier at rest, never the raw
  bearer value;
- raw payload absent from ORM rows, logs, exceptions, safe views, generated-card
  rows, and gate evidence;
- safe malformed/unknown/revoked/rotated-away resolution;
- independent QR decoding and a minimum safe quiet-zone/error-correction contract;
- permanent QR distinct from the synthetic non-resolving preview QR.

The legacy project contains both an earlier random 32-byte token contract and a
later secret-derived deterministic payload contract. The rebuild must not silently
inherit either scheme; section 7 records the unresolved M5-to-M6 recovery boundary.

Evidence anchors:

- `backend/app/models/guest_qr_credential.py`
- `backend/app/services/qr_credentials.py`
- `backend/app/services/qr_images.py`
- `backend/tests/test_qr_token_security.py`
- `backend/tests/test_qr_credential_service.py`
- `backend/tests/test_qr_credential_security.py`
- `backend/tests/test_qr_payload_derivation.py`
- `backend/tests/test_qr_renderable_credential_service.py`
- `backend/tests/test_qr_image_decoding.py`

### 4.3 Generic rendering, name presentation, approval, and permanent cards

Carry forward:

- a strict, bounded, immutable package validator and a renderer driven only by
  validated manifests, generic layers, closed capabilities, placeholders,
  formatters, declared assets/fonts, and supplied in-memory context;
- no `template_key` conditional rendering branch;
- deterministic compositing and text fitting, metadata-free PNG output, and
  independent QR verification;
- `COVER`/`CONTAIN`, focus X/Y, and zoom as generic photo controls. `CONTAIN` with
  zoom other than `1.0` was rejected by both service validation and renderer tests;
- explicit name-presentation sources and ordered fallbacks. First names, titles,
  nicknames, household names, or shortened couple names were never guessed from
  `full_name`;
- append-only event-design revisions and explicit versioned approval/revocation;
- permanent-card identity pinned to event, guest, design revision, approval
  revision, template version/package, photo identity, name-presentation versions,
  QR credential/version, control values, and renderer contract version;
- deterministic content digest/card identifier, private content-addressed storage,
  idempotent identical issuance, integrity revalidation, and no PII or raw QR in
  storage keys/records.

Evidence anchors:

- `backend/app/template_engine/`
- `backend/app/cards/name_presentation.py`
- `backend/app/services/event_card_designs.py`
- `backend/app/services/event_card_design_approvals.py`
- `backend/app/services/generated_guest_cards.py`
- `backend/tests/test_photo_framing_controls.py`
- `backend/tests/test_template_preview_renderer.py`
- `backend/tests/test_template_preview_text_runs.py`
- `backend/tests/test_name_presentation.py`
- `backend/tests/test_event_card_design_approval_services.py`
- `backend/tests/test_generated_guest_card_identity.py`
- `backend/tests/test_generated_guest_card_issuance.py`
- `backend/tests/test_generated_guest_card_qr.py`
- `backend/tests/test_generated_guest_card_security.py`

### 4.4 Gate, attendance, corrections, and reports

Carry forward:

- credential validation was a non-mutating lookup; admission was a separate
  transaction boundary;
- an admission locked and rechecked the current guest allowance, stored the actual
  `companions_admitted`, and created exactly one immutable attendance record;
- duplicate scans returned the existing admission and never added or replaced
  companion counts;
- raw gate attendance/companion evidence and its original credential, actor, and
  timestamp were immutable;
- corrections were separate append-only revisions with decisions
  `SET_ATTENDED`, `SET_NOT_ATTENDED`, and `RESTORE_GATE_DERIVED`;
- `SET_ATTENDED` alone supplied an explicit effective companion count bounded by
  the current allowance. `SET_NOT_ATTENDED` zeroed effective people without
  changing raw evidence. Restore returned to raw gate-derived facts;
- invitation capacity, raw gate admitted people, and correction-aware effective
  people were distinct report dimensions. Named-holder attendance rate semantics
  were not replaced by total-person counts;
- JSON/CSV/XLSX exports were deterministic and identifier-only: no names, contact
  data, notes, table/category values, QR/card data, correction reasons/notes, or
  actor identities. CSV/XLSX formula-trigger cells were neutralized.

Evidence anchors:

- `backend/app/services/gate_validation.py`
- `backend/app/models/guest_attendance.py`
- `backend/app/models/guest_attendance_correction.py`
- `backend/app/services/attendance_corrections.py`
- `backend/app/services/attendance_reports.py`
- `backend/app/services/attendance_report_exports.py`
- `backend/tests/test_gate_party_admission.py`
- `backend/tests/test_attendance_companion_corrections.py`
- `backend/tests/test_attendance_correction_effective_state.py`
- `backend/tests/test_attendance_report_companion_headcount.py`
- `backend/tests/test_attendance_report_privacy.py`
- `backend/tests/test_attendance_report_spreadsheet_security.py`

### 4.5 Schema and operational safety lessons

Use the legacy schema only as constraint evidence. The new repository starts with
one clean PostgreSQL Alembic history.

Lessons to retain:

- add nullable columns, explicitly backfill, then enforce `NOT NULL` and checks;
- avoid persistent JSON server defaults used only to assist a backfill;
- express lifecycle consistency, event/guest scope, uniqueness, append-only
  history, companion non-negativity, and correction-decision consistency in both
  application validation and PostgreSQL constraints;
- preserve ACTIVE-to-VERIFIED template supersession so pinned historical versions
  remain renderable; retirement is explicit and terminal;
- require explicit `APP_ENV=test`, explicit `DATABASE_URL`, a strongly disposable
  PostgreSQL database name, local host checks, connected-database identity, current
  revision, and target revision before disposable migration commands;
- require sanitized identity, PostgreSQL, one Alembic head, canonical-storage
  integrity, and privacy-safe readiness checks for staging.

Legacy SQLite batch/offline helpers are historical compatibility evidence, not a
database-platform decision for this PostgreSQL-only rebuild.

Evidence anchors:

- `backend/migrations/versions/20260802_0012_safe_template_supersession.py`
- `backend/migrations/versions/20260802_0018_import_validation_failure.py`
- `backend/migrations/versions/20260812_0019_event_companion_policy.py`
- `backend/migrations/versions/20260812_0020_gate_party_admission.py`
- `backend/migrations/versions/20260812_0021_attendance_companion_corrections.py`
- `backend/app/cli/disposable_alembic.py`
- `backend/app/cli/staging_readiness.py`

## 5. IGNORE

- The legacy `backend/app` tree as implementation material. It is evidence only;
  do not copy it wholesale.
- All 21 legacy Alembic revisions as a migration chain. Rebuild the schema from a
  clean PostgreSQL baseline when the appropriate milestones begin.
- Legacy `.env` files, databases, backups, caches, storage roots, spreadsheets,
  guest/export data, photographs, generated cards, raw QR values, and staging state.
- Legacy private packages for Versions 1-9 and any abandoned candidate/evidence
  artifacts not required to preserve the exact approved V10 package.
- Legacy documentation claims contradicted by later accepted code/tests, including
  stale "not implemented" statements and the earlier role expansion.
- Legacy SQLite-as-a-supported-disposable-platform behavior. The rebuild is
  PostgreSQL throughout.
- The legacy event model default/backfill of `0` companions. The new control
  documents lock the system default to **2**; spreadsheet data must not alter it.
- The legacy whole-batch terminal confirmation plus immutable preview-row design as
  the final M4 model. It cannot provide required staged-row correction.
- Template asset filename hints that are not in the verified V10 manifest.
- Any template-specific renderer behavior, inferred first-name parsing, fuzzy
  header guessing, or correction logic that rewrites raw gate evidence.

## 6. Confirmed control-document resolutions

These legacy contradictions do not require a new decision because the clean rebuild
documents already resolve them:

| Conflict | Resolution |
|---|---|
| Legacy event companion default/backfill was `0` or derived from existing guests | Use the locked rebuild default **2**; event changes are explicit policy changes |
| Legacy import rows were immutable with no correction workflow | Preserve the original snapshot and add separately versioned correction/revalidation required by M4 |
| Legacy supported SQLite in disposable migration tooling | New development, test, staging, and later production-like verification use PostgreSQL |
| Legacy documents drifted across roles and implementation status | The new control pack and one `SUPER_ADMIN` MVP are authoritative |

## 7. USER DECISION REQUIRED

### 7.1 Private asset and font rights

Technical identity and visual approval do not establish copyright, private-use,
font-embedding, or redistribution permission. No provenance or licensing record is
inside the verified nine-entry package, as expected. Before moving the package out
of the frozen folder, the user must confirm:

1. the six exact assets and two exact fonts may be used privately for this system;
2. the intended generated-card use and font embedding are permitted;
3. whether any source file may ever be committed or redistributed.

Default safe decision until then: keep every package/source/font/asset byte outside
Git in an ignored private storage root.

### 7.2 QR issuance-to-render recovery boundary

The current rebuild documents simultaneously require:

- secure random opaque credentials;
- only a secure hash stored at rest;
- M5 issuance before M6 permanent-card generation; and
- M6 generation/rerendering with the real guest credential.

A random raw token cannot be reconstructed from its hash after the one-time
issuance boundary. The legacy project later solved this with a deterministic
secret-derived opaque payload, but that is not the same as the rebuild's stated
random-token contract. Before M5 implementation, approve one architecture and
reconcile the control documents:

1. approve deterministic cryptographic derivation from a protected server secret
   and immutable credential identity, while storing only the verifier;
2. move first credential issuance into the same transaction/boundary as first
   permanent-card generation so the random raw token is rendered before disposal;
3. approve encrypted recoverable bearer-token storage in addition to a hash, which
   would explicitly change the current hash-only-at-rest rule.

No option was selected during this audit. M5 later resolved the boundary by
rendering the random credential into a protected private QR image during M5
issuance, persisting only its verifier and internal artifact metadata, then
discarding the raw credential. M6 consumes the protected image and does not
recover or persist the raw bearer value.

### 7.3 Recovery destination and copy authorization

After rights are confirmed, explicitly approve the private destination for the
exact V10 bytes and authorize the copy from the frozen project. No package, asset,
font, preview, or other frozen artifact was copied during this phase.

## 8. New repository foundation assessment

| Foundation item | Assessment |
|---|---|
| Root control/docs pack | Present: `README.md`, `AGENTS.md`, `LICENSE`, `.gitignore`, `.gitattributes`, `.editorconfig`, examples, bootstrap, and supplied `docs/*` |
| Backend M0 skeleton | Present: `backend/README.md`, `backend/.env.example`, and placeholder `app`, `tests`, and `alembic` directories |
| Git repository and required branches | Not initialized; no `.git`, branch, or HEAD exists in the new project |
| M1 tooling | Intentionally not created: no `pyproject.toml`, application factory, Alembic config/history, test harness, or health endpoint |
| PostgreSQL plan | Documented in examples and environment/control docs; no database command was run |
| Private-file protections | `.gitignore` excludes environments, databases, backups, private storage, workbooks/exports, photos, generated artifacts, ZIPs, fonts, and private SVGs |
| New-tree private artifact audit | PASS: no `.env`, workbook/export, database/dump, ZIP, raster/photo, or font file was found |
| Frozen artifact transfer | PASS: nothing was copied |
| Recovery report | Present after this audit |

M0 is not accepted until the user reviews this report and resolves or defers the
three decisions in section 7. Repository initialization and implementation remain
stopped.

## 9. Audit verification performed

- read every bootstrap-required control document;
- read-only frozen Git branch/HEAD/log/ref inspection;
- SHA-256 of every candidate/canonical target archive and approved preview;
- in-memory ZIP inventory, duplicate/path safety, decompressed entry size, and
  SHA-256 verification;
- independent source-to-entry comparison for all nine logical files;
- independent reproduction of the logical package digest from the canonical
  manifest/file index;
- byte-for-byte candidate-to-canonical archive comparison;
- image dimensions/mode/metadata inspection;
- independent QR decode of the synthetic preview;
- targeted source/test/migration/history inspection for import, QR, rendering,
  cards, attendance, corrections, reports, and operational safety;
- new-tree foundation and private-file-name audit.

No backend test suite or migration was run because this phase neither implements
nor changes backend code. No Git repository was initialized, no commit was created,
and no remote operation occurred.
