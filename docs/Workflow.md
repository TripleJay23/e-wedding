# Engineering Workflow

## 1. Development lifecycle

```text
plan milestone
→ feature branch
→ implement focused task
→ focused tests
→ full backend regression
→ local commit
→ review
→ fast-forward into dev
→ when milestone candidate is ready, advance staging
→ run continuous staging acceptance
→ fix failures on feature branches
→ rerun same failed checkpoint
```

## 2. Branch responsibilities

### `feature/*`

One focused task. Created from current accepted `dev`.

### `dev`

Integration branch for accepted feature commits.

### `staging`

Release-candidate branch and full workflow acceptance environment.

A staging failure does not trigger unrelated development. It produces a focused fix that returns through `feature/* → dev → staging`.

### `main`

Backend release branch. Only receives the fully accepted staging state.

## 3. Remote operations

Default rule: agents do not push, fetch, pull, open PRs, or alter remotes unless the user explicitly authorizes that operation.

The user controls the eventual GitHub upload.

## 4. PostgreSQL workflow

Use PostgreSQL throughout this rebuild.

Before migrations, agents must print/verify a sanitized target identity and current/target revision.

Disposable integration databases must have unmistakably disposable names and an explicit guard that refuses staging/production names.

## 5. Staging story continuity

The main acceptance narrative is Jordan & Jessica's wedding.

Every staging report starts with:

```text
Scenario: Jordan & Jessica Wedding
Current checkpoint: ...
Previous accepted checkpoint: ...
Current persisted event state: ...
Input used: ...
Expected behavior: ...
Observed behavior: ...
Outcome: PASS / BLOCKED
Next exact checkpoint: ...
```

Do not replace this storyline with random synthetic scenarios. Synthetic data is allowed for narrow regression/concurrency tests only.

## 6. Excel staging workflow

```text
explicit user workbook upload
→ structural understanding
→ mapping
→ row classification
→ validation
→ preview
→ user/admin review
→ confirm valid rows
→ reconcile persisted guests
→ inspect invalid rows
→ correct staged invalid values in app/backend
→ revalidate
→ confirm newly eligible rows
→ duplicate/re-import test
→ Excel milestone accepted
```

The system must prompt for/use the workbook explicitly supplied for that checkpoint. It must not silently reuse a cached previous private workbook.

## 7. Failure workflow

When a checkpoint fails:

1. stop at first meaningful defect;
2. classify root cause;
3. do not fix in the same acceptance task;
4. create one narrow fix task;
5. implement on `feature/*`;
6. test and commit locally;
7. integrate into `dev`;
8. advance `staging`;
9. rerun the exact same acceptance checkpoint/input.

## 8. Documentation workflow

Do not continuously rewrite all architecture documents during the staging campaign.

After the entire backend staging journey passes:

1. audit implementation against docs;
2. update all docs to actual tested behavior;
3. run final security/release checks;
4. promote staging to main;
5. verify main;
6. user performs GitHub upload.
