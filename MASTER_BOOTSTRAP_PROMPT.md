# Master Bootstrap Prompt — Fresh Backend Rebuild

Use this prompt as the first message to the implementation agent that can access the frozen legacy project folder.

---

You are the implementation engineer for a clean rebuild of a wedding e-card and guest validation backend.

This task is agent-neutral. Follow it whether you are Codex, Claude Code, Gemini, or another engineering agent.

## PRIMARY GOAL

Create a fresh repository and finish the BACKEND completely, without drifting from the milestone plan.

Do not begin Flutter.

Do not continue developing inside the old project.

The old/frozen project is read-only evidence that contains valuable contracts, tests, and the exact approved card design.

The new repository is a clean implementation.

## CRITICAL CONTEXT

The previous project became too large and drifted across unrelated features and staging work. We are restarting to regain control, not because every previous decision was wrong.

Preserve proven decisions and the exact approved card design, but do not blindly copy the old backend.

The development sequence is controlled by `docs/Milestones.md`.

When a milestone/staging checkpoint fails, fix that exact failure and rerun it. Do not jump to another feature.

## GIT WORKFLOW — LOCKED

Required branches:

main
staging
dev
feature/*

Rules:

- initialize a new Git repository
- create `main`, `staging`, and `dev`
- work only on `feature/*` branches created from `dev`
- one focused task per feature branch
- create local commits after tests pass
- DO NOT push, pull, fetch, open PRs, change remotes, rebase accepted history, or force-push unless the user explicitly authorizes that operation
- accepted feature commits integrate into `dev`
- full release-candidate testing occurs on `staging`
- only accepted staging state advances to `main`
- user controls final GitHub upload

## DATABASE — LOCKED

Use PostgreSQL for development, disposable integration tests, staging, and production later.

Do not switch the rebuild back to SQLite as the main DB.

Create hard environment guards so disposable test/migration commands cannot accidentally operate on staging or production databases.

System default maximum companions per invitation = 2.

## FIRST ACTION — DO NOT IMPLEMENT FEATURES

Before writing backend feature code, perform a READ-ONLY targeted audit of the frozen legacy project.

You have access to the frozen folder. Search it deliberately rather than reading everything indiscriminately.

Read the new repository documents first:

- AGENTS.md
- README.md
- docs/ProjectCharter.md
- docs/Architecture.md
- docs/Workflow.md
- docs/Milestones.md
- docs/LegacyRecovery.md
- docs/CardDesignReference.md
- docs/ExcelImport.md
- docs/Security.md

Then inspect the frozen project for only high-value recovery evidence.

## FROZEN PROJECT RECOVERY TARGETS

### 1. Exact approved card design

Locate the Wedding Floral Version 10 package by SHA-256:

b0ec16e8d1b1a715f16c55b14bec100066fc1dc05f90c7c86b239f7ee734c782

Approved default preview SHA-256:

1f7e8a38af04b0729935d04e8473a1e74a7309066bac875b4068ba48e784dda8

Expected identity:

- template_key = wedding_floral
- version = 10
- canvas = 1240x1748
- 9 logical entries
- 6 assets
- 2 fonts
- preview QR = PREVIEW-QR-DO-NOT-SCAN

Locate and verify the exact package/logical assets/fonts.

Do not redraw/recreate the card.

Keep recovered private template assets outside Git unless redistribution rights are explicitly confirmed.

Record exact safe paths/hashes in the recovery report.

### 2. Excel-import contracts/edge cases

Inspect old tests/services/docs to recover accepted behavior for:

- configured seven canonical guest fields
- intelligent deterministic header mapping
- titles/presentation/footer rows
- formula non-execution
- gaps/missing required values
- partial import of valid rows
- duplicate detection
- immutable batch snapshots
- companion-policy validation
- real-workbook issues previously discovered

Do not copy real spreadsheets or expose guest row contents.

Important target for the clean rebuild: invalid staged rows must be correctable inside the backend/app workflow, revalidated, and later imported without rebuilding the whole Excel sheet.

### 3. Secure QR contracts

Recover accepted security behavior, not raw tokens/data.

### 4. Card-generation contracts

Recover generic manifest/layer behavior, photo framing controls, name-presentation policy, approval concepts, and deterministic rendering requirements.

### 5. Attendance/correction/report contracts

Recover stable domain semantics so we do not rediscover already solved edge cases, but do not implement them before their milestone.

### 6. Migration/schema lessons

Inspect legacy migrations only to learn domain constraints and historical mistakes.

Do NOT copy the old migration chain into the new repository.

The new repository starts with a clean Alembic history.

## RECOVERY OUTPUT

Create:

`docs/LegacyRecoveryReport.md`

It must classify findings into:

1. REUSE EXACTLY
2. REIMPLEMENT FROM CONTRACT
3. IGNORE
4. USER DECISION REQUIRED

Also produce a short table:

| Area | Proven legacy behavior | New rebuild decision | Evidence |

Do not write feature code yet.

## NEW REPOSITORY FOUNDATION

After the recovery report is accepted by the user, create/verify the clean repository foundation:

- README.md
- AGENTS.md
- LICENSE
- .gitignore
- .env.example
- backend/.env.example
- docs/* supplied in the bootstrap pack
- backend package skeleton
- pyproject/dependency management
- formatting/lint/test config
- Docker/PostgreSQL local development configuration if appropriate
- Alembic clean baseline
- safe environment loader
- disposable PostgreSQL guard
- health endpoint only when Milestone 1 begins

Never commit real `.env` values.

Never commit private template packages, fonts, spreadsheets, photos, backups, generated cards, databases, or QR tokens.

## IMPLEMENTATION PROCESS AFTER FOUNDATION

Work through `docs/Milestones.md` strictly in order.

For every task:

1. state current milestone/checkpoint;
2. read relevant docs;
3. create feature branch from accepted `dev`;
4. implement only the requested acceptance criterion;
5. run focused tests;
6. run required regression/full suite;
7. run lint/format/compile/migration checks;
8. inspect Git for private files;
9. create one focused local commit;
10. return a concise engineering report;
11. stop for review.

Do not create several future milestones at once.

## STAGING ACCEPTANCE — LOCKED

The main staging journey is Jordan & Jessica's wedding.

Do not replace it with random disconnected synthetic stories.

At staging, continue the same event through:

Event setup
→ card design/photo
→ Excel import
→ valid-row confirmation
→ invalid-row correction/revalidation
→ guest reconciliation
→ QR issuance
→ permanent cards
→ gate scanning
→ attendance/companions
→ corrections
→ reports

Each staging report must state the previous accepted checkpoint and the next exact checkpoint.

When a real user Excel workbook is required, STOP and ask the user to explicitly provide the desired workbook. Do not silently reuse a cached workbook.

## DOCUMENTATION TIMING

Do not spend the project continuously rewriting all planning documents.

Use the supplied docs as control documents during implementation.

After the entire staging campaign passes, perform one documentation reconciliation milestone so every document matches the actual tested implementation. Then promote staging to main.

## DEFINITION OF SUCCESS

The backend is not complete until:

- Excel import including invalid-row correction is accepted
- real secure QR credentials work
- the exact approved Wedding Floral design is recovered
- permanent personalized cards use real guest QR credentials
- gate validation works
- attendance/companions/corrections work
- reports reconcile
- full Jordan & Jessica staging scenario passes
- security/privacy checks pass
- docs are reconciled
- staging is promoted to main locally
- final main verification passes

STOP after the initial frozen-project recovery report and repository-foundation assessment. Do not implement Milestone 1 until the user approves the report.
