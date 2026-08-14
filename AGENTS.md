# AGENTS.md — Engineering Control Rules

## 1. Mission

Build the backend of a secure, deterministic, production-ready wedding e-card and guest validation system without drifting from the milestone plan.

The backend must be fully accepted before Flutter implementation begins.

## 2. Agent neutrality

These rules apply to any implementation agent: Codex, Claude Code, Gemini, or another coding agent.

Do not write instructions that depend on one vendor-specific tool unless the user explicitly requests it.

## 3. Sources of truth

Read before every task:

1. `docs/ProjectCharter.md`
2. `docs/Architecture.md`
3. `docs/Workflow.md`
4. `docs/Milestones.md`
5. the feature-specific document, if one exists

When documents conflict, stop and report the conflict before implementation.

Do not silently choose one interpretation.

## 4. Anti-drift rule

Every implementation task must identify:

- current milestone
- exact acceptance criterion being addressed
- previous accepted baseline
- files expected to change
- tests required
- explicit stop condition
- exact next checkpoint

Do not add unrelated features, refactors, architecture changes, deployment work, UI work, reporting enhancements, or "nice-to-have" improvements.

A staging failure creates only one allowed next action: diagnose and fix that failure, then rerun the same checkpoint.

## 5. Milestone gate rule

Do not skip milestones merely because later functionality already exists in the frozen project.

A milestone is complete only when:

- implementation exists
- focused tests pass
- full backend regression passes
- real staging acceptance for that milestone passes
- no unresolved blocker remains

## 6. Git workflow

Required branches:

```text
main
staging
dev
feature/*
```

Rules:

- create feature branches from `dev`
- one focused task per feature branch
- commit locally only
- never push unless the user explicitly authorizes it
- never force-push
- never amend an accepted commit
- never rebase accepted history without explicit authorization
- integrate accepted feature commits into `dev`
- advance `staging` only for a controlled acceptance campaign
- advance `main` only after staging acceptance is complete

Commit format:

```text
type(scope): concise description
```

Examples:

```text
feat(imports): add staged row correction
fix(imports): classify trailing footers safely
feat(qr): issue secure guest credentials
feat(cards): persist generated guest cards
```

## 7. Database rule

PostgreSQL is the database platform for this rebuild.

Use separate databases/environments for:

- development
- disposable tests
- staging
- production later

Never allow a test/disposable command to fall back to staging or production.

Before any migration command, verify:

- environment name
- host
- database name
- connected database identity
- current Alembic revision
- intended target revision

## 8. Private data rule

Never commit:

- `.env`
- credentials or tokens
- real guest spreadsheets
- guest exports
- wedding photographs
- private template source assets/fonts unless redistribution is explicitly approved
- generated cards
- QR images or raw QR credentials
- database dumps
- staging backups
- test evidence containing private row values

Use privacy-safe aggregates in reports.

## 9. Excel import invariant

The Excel workflow is a first-class product feature, not a helper script.

It must:

- handle configured canonical guest fields
- tolerate reordered columns
- identify safe aliases deterministically
- ignore undefined columns according to contract
- ignore presentation/title/footer rows
- never execute formulas
- preserve malformed guest rows as visible validation failures
- import only valid rows after confirmation
- preserve invalid rows for correction
- support administrator correction and revalidation of staged invalid rows
- support duplicate detection and safe re-import

System default maximum companions per invitation: **2**.

Never silently change that default to satisfy a workbook.

## 10. Card design invariant

The approved Wedding Floral design must be preserved exactly.

Do not redraw it from memory.

Recover and verify the approved template package from the frozen project using `docs/CardDesignReference.md`.

Renderer behavior must remain generic and manifest-driven.

No template-specific rendering branches.

## 11. QR security invariant

QR codes contain only high-entropy opaque tokens.

- never encode guest PII
- store only a secure hash of the credential
- bind credential to guest and event
- support active/inactive/reissue lifecycle
- preview QR must be synthetic and non-resolving
- permanent cards must use real guest credentials

## 12. Staging continuity rule

The main end-to-end acceptance story is the **Jordan & Jessica wedding scenario**.

Staging reports must show continuation from the previous checkpoint rather than random disconnected test events.

The test journey is:

```text
Event
→ Card design/photo
→ Excel import
→ Valid guest persistence + invalid-row correction
→ QR issuance
→ Permanent cards
→ Gate validation
→ Attendance/companions
→ Corrections
→ Reports
```

Use isolated synthetic fixtures only for narrow technical tests. They do not replace the continuous staging scenario.

## 13. Documentation timing

During active implementation, update documentation only when required to prevent a dangerous contradiction.

After full staging acceptance, run a dedicated documentation reconciliation milestone and align all docs to the tested implementation before promoting to `main`.

## 14. Required task report

Every agent task must report:

- milestone/checkpoint
- branch + HEAD
- changes made
- migrations
- tests run and results
- privacy/security checks
- Git status
- commit hash if created
- whether anything was pushed
- blockers
- exact next checkpoint

Avoid inflated reports containing unrelated details.
