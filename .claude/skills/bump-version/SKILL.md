---
description: >
  Release a new firefox-triage-dashboard version: bump pyproject.toml, test,
  commit, tag, push, publish to PyPI (as `triage-dashboard`), THEN file an issue
  on the fx-bug-toolkit repo asking it to bump the pinned dashboard version.
  fx-bug-toolkit PINS this dashboard, so the fx-bug-toolkit issue step is
  MANDATORY — a bump is not done until that issue is filed. Triggers on:
  "bump version", "/bump-version", "release the dashboard", "cut a release",
  "publish the triage dashboard".
allowed-tools: [Read, Edit, Bash, AskUserQuestion]
---

# firefox-triage-dashboard Version Bump

fx-bug-toolkit **pins** this dashboard: its `.claude-plugin/versions.json` plus
inline `REQUIRED="<X>"` pins (×3 in `skills/open-triage/SKILL.md`, ×1 in
`skills/update/SKILL.md`), enforced by a drift test. It installs from **PyPI** as
`pip install "triage-dashboard==<X>"`. So every bump must be mirrored by an issue
asking fx-bug-toolkit to bump that pin.

**MANDATORY: the bump is not complete until the fx-bug-toolkit issue is filed
(Step 6). Do not report done before then.**

Downstream repo: **`alastor0325/fx-bug-toolkit`**.

---

## Step 1 — Decide the new version

```bash
grep -m1 '^version' pyproject.toml
```

Use the caller's level (`patch`/`minor`/`major`) or explicit version; otherwise
`AskUserQuestion`. Set `OLD` and `NEW`.

## Step 2 — Bump pyproject.toml

Edit only the `version` field under `[project]`.

## Step 3 — Test (green before committing — see CLAUDE.md)

```bash
python -m pytest tests/
```

A failing test is a hard blocker.

## Step 4 — Commit, tag, push

```bash
git add pyproject.toml <other release files>
git commit -m "release: vNEW

<one-line summary>"
git tag vNEW
git push origin <current-branch>
git push origin vNEW
```

## Step 5 — Publish to PyPI

fx-bug-toolkit installs via `pip install "triage-dashboard==<X>"`, so the new
version must be on PyPI (distribution name **`triage-dashboard`**):

```bash
python -m build && python -m twine upload dist/*<NEW>*
```

(If publishing isn't possible right now, say so — the fx-bug-toolkit pin must NOT
be bumped to a version that isn't on PyPI yet.)

## Step 6 — File the fx-bug-toolkit issue (MANDATORY)

```bash
PREV=$(git describe --tags --abbrev=0 vNEW^ 2>/dev/null || echo "")
[ -n "$PREV" ] && git log --oneline "$PREV"..vNEW || git log --oneline -10 vNEW
```

Write the body to a temp file, then:

```bash
gh issue create -R alastor0325/fx-bug-toolkit \
  --title "bump triage-dashboard pin to vNEW" \
  --label enhancement \
  --body-file /tmp/dashboard-bump-issue.md && rm -f /tmp/dashboard-bump-issue.md
```

The body must include:
- The new version **NEW** and previous **OLD**, and a short changelog.
- The concrete ask: bump the pin to **NEW** in **all** these places (a drift test
  enforces they agree):
  - `.claude-plugin/versions.json` → `"firefox-triage-dashboard"`
  - the `REQUIRED="<X>"` pins in `skills/open-triage/SKILL.md` (three of them)
  - the `REQUIRED="<X>"` pin in `skills/update/SKILL.md`
- Confirm `pip install "triage-dashboard==NEW"` resolves (it's on PyPI).
- Source: the commit hash and tag `vNEW`.

Report the created issue URL.

## Step 7 — Summary

old → new, pushed tag, PyPI publish result, changelog, and the fx-bug-toolkit
issue URL. If the issue was NOT filed, say so loudly — the release is incomplete.
