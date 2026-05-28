# Firefox Triage Dashboard — Implementation Plan

> Living document. Updated whenever phases progress or decisions change.
> Last updated: 2026-05-28

## What this is

A local web dashboard that consumes the output of the `/triage` skill
(stored under `~/firefox-triage/`) and lets the user review, edit, and
act on pending triage drafts in one page. Long-term goal: orchestrate
Claude skills (`/triage`, `/bug-start`) directly from the dashboard.

- **Repo**: https://github.com/alastor0325/firefox-triage-dashboard (private)
- **Local**: `~/projects/firefox-triage-dashboard/`
- **Data source**: `~/firefox-triage/` (shared with the `/triage` skill — single source of truth)

## Architecture

```
~/firefox-triage/                       ← data (owned by /triage skill)
├── pending/bug-*.json                  ← per-bug draft (read+written by dashboard)
├── triage-log.json                     ← session history (read+appended by dashboard)
├── ni-watch.json                       ← watch state (read by dashboard)
└── watch-tmp-*.json                    ← deferred watch-add (read+removed)

~/projects/firefox-triage-dashboard/    ← code
├── pyproject.toml                      ← uv-managed
├── src/triage_dashboard/
│   ├── __main__.py                     ← `triage-dashboard` CLI entry
│   ├── app.py                          ← FastAPI app
│   ├── data.py                         ← JSON loaders (pending/, log, watch)
│   ├── actions.py                      ← bugzilla-cli wrappers      [Phase 3]
│   ├── watch.py                        ← file watcher → SSE          [Phase 2]
│   ├── claude.py                       ← orchestration               [Phase 5]
│   ├── templates/{base,index,card,sidebar}.html
│   └── static/style.css
└── mockup.html                         ← visual reference (frozen)
```

## Decisions locked

| Date | Decision |
|---|---|
| 2026-05-28 | Repo lives at `~/projects/firefox-triage-dashboard/`, separate from `~/firefox-triage/` data |
| 2026-05-28 | Private GitHub repo (`alastor0325/firefox-triage-dashboard`) |
| 2026-05-28 | Stack: FastAPI + Jinja2 + htmx + Geist/Geist Mono CSS; no SPA, no build step |
| 2026-05-28 | Phase-1-first delivery: read-only dashboard, sign-off, then iterate |
| 2026-05-28 | Skill drafts, dashboard displays — no duplicate gap-analysis or drafting logic in the dashboard |
| 2026-05-28 | Claude invocation approach (Phase 5) deferred — decide when we get there |

## Phases

### Phase 1 — Read-only dashboard  ← AWAITING USER SIGN-OFF

`triage-dashboard` opens browser at `localhost:8765` and renders pending
drafts with real data from `~/firefox-triage/`.

- [x] pyproject.toml + project scaffold (stdlib venv, uv-compatible)
- [x] FastAPI app skeleton (`app.py`) with single `GET /`
- [x] Data loaders (`data.py`): pending drafts, triage-log, ni-watch
- [x] Section grouping logic (§1a / §1b / §1c) inferred from pending JSON fields
- [x] Extract CSS from `mockup.html` → `static/style.css`
- [x] Extract HTML from `mockup.html` → Jinja templates (base, index, card, sidebar)
- [ ] Bug detail enrichment: component/reporter via `bugzilla-cli get` with cache *(deferred — cards render fine without it; revisit if context feels sparse)*
- [x] CLI entry (`__main__.py`) that runs uvicorn and opens browser
- [x] Manual verification: real 15 drafts + 20 log entries render
- [x] Commit + push

**Run:** `~/projects/firefox-triage-dashboard/.venv/bin/triage-dashboard`
(opens `http://127.0.0.1:8765/`)

### Phase 2 — Live updates  ← PENDING

Page auto-refreshes when terminal `/triage` writes new pending drafts.

- [ ] Add `watchdog` dependency
- [ ] File watcher on `~/firefox-triage/pending/` and `triage-log.json`
- [ ] SSE endpoint `/events` that pushes reload signals
- [ ] htmx listener on the page that re-fetches affected cards
- [ ] Commit + push

### Phase 3 — Apply / Skip / Edit  ← PENDING

Buttons in the UI actually execute against Bugzilla.

- [ ] `POST /draft/{id}/apply` → subprocess `bugzilla-cli apply {id}`, stream output
- [ ] `POST /draft/{id}/skip` → delete pending file, append `skipped` to triage-log
- [ ] `PUT /draft/{id}` → save edits to pending JSON (comment text)
- [ ] Toast / output pane for apply-result streaming
- [ ] Card fade-out + remove on success
- [ ] Commit + push

### Phase 4 — /bug-start handoff  ← PENDING

§1b cards have a "copy /bug-start {id}" button + write action to queue file.

- [ ] Clipboard JS for copy button
- [ ] `~/firefox-triage/claude-queue.jsonl` writer on apply for §1b cards
- [ ] Commit + push

### Phase 5 — Claude orchestration  ← PENDING (scope TBD)

Dashboard kicks off `/triage` and `/bug-start` from the UI; investigation
results stream back.

**Open decision**: subprocess `claude -p`, queue file + `/process-queue`
skill, or Claude Agent SDK embedded in the server. Pick when we get there.

- [ ] "Run /triage now" button
- [ ] Auto-trigger `/bug-start` after §1b apply
- [ ] Investigation result display
- [ ] Commit + push

## Open questions

- (Phase 1) Should component/reporter come from `bugzilla-cli get` on demand, or be cached locally? Decide during implementation based on speed.
- (Phase 5) Which Claude invocation method? Defer.

## Out of scope (intentional)

- Authentication (local-only, single user)
- Daemonization via launchd (manual `triage-dashboard` invocation)
- Search / filter / pagination
- Field-edits in browser (only the comment is editable; field changes go back through Claude/skill)
- Mobile responsive beyond what the mockup already does
