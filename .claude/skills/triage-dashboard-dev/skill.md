# triage-dashboard Dev Loop

The **triage-dashboard Dev Loop** is a mandatory workflow for all code changes. All steps are required and cannot be skipped.

## The Cycle

Development proceeds through six sequential steps: understand the task, extract & develop, write tests, agent review, commit & push, then conclude.

## Core Requirements

- All new or changed logic must be extracted into **pure/testable functions** (no I/O, no network calls) so they can be unit tested directly.
- Every function added or changed must have **unit tests** covering its branches. If behavior is removed, a test must assert the removal holds.
- **`pytest tests/` must pass** before committing. Failing tests are a hard blocker — fix them, do not work around them.
- **README.md must be updated** whenever a command is added/removed or a flag/default changes.
- **Bumping the version means syncing the fx-bug-toolkit pin.** The fx-bug-toolkit plugin runs this dashboard from a pip install pinned to a git tag, so a version bump that isn't tagged and pinned never reaches users. Whenever `pyproject.toml` `version` changes: (1) tag `vX.Y.Z` matching it exactly (a mismatch makes the plugin's version check re-install forever) and push the tag; (2) in fx-bug-toolkit, bump `REQUIRED` in `skills/triage-dashboard/SKILL.md` (all blocks) **and** `skills/update/SKILL.md`, bump the plugin version + CHANGELOG, and tag `fx-bug-toolkit--vA.B.C`.
- Unit tests go in `tests/unit/`, mock all I/O and network calls.
- Integration tests go in `tests/integration/` (only for changes touching real I/O, the FastAPI app, or external commands like `bugzilla-cli`).
- Write the failing test first — confirm it fails before implementing.
- Each commit must be small and focused on one logical change. Never bundle multiple unrelated refinements into one commit.

## Process Details

### Step 1 — Understand
Read the relevant source files before touching anything. Understand the existing structure: which functions are involved, what tests already cover them.

### Step 2 — Extract & Develop
Write the implementation. Extract logic into named pure functions first, then call them from handlers/routes. Keep entry points thin — they only wire up I/O and call pure functions.

### Step 3 — Write Tests
For every function added or changed, write unit tests:
- Happy path
- Each meaningful branch or flag
- Regression guards for removed behavior

Run and confirm green:
```
pytest tests/
```

### Step 4 — Agent Review
Run `/simplify` to have a fresh-context agent review the changes for code quality, reuse, and efficiency. Apply any fixes before committing.

### Step 5 — Commit & Push
```
git commit -m "<type>: <what and why>"
git push
```
Both are required. Never commit without pushing.

### Step 6 — Conclude
Summarize: what changed, what tests were added, whether README/PLAN was updated.
