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
- [x] Extract HTML from `mockup.html` → Jinja templates
- [ ] Bug detail enrichment: component/reporter via `bugzilla-cli get` with cache *(deferred — cards render fine without it; revisit if context feels sparse)*
- [x] CLI entry (`__main__.py`) that runs uvicorn and opens browser
- [x] Manual verification: real 15 drafts + 20 log entries render
- [x] Commit + push

#### Visual refinements (2026-05-28, follow-up)
- [x] Tab navigation (§1b Triaged / §1a Needs Info / §1c Close) — server-rendered via `?tab=...`, defaults to first non-empty section
- [x] Promoted **Watching** to its own tab
- [x] Removed Recent panel + sidebar entirely (single-column layout, max-width 1080px)
- [x] Auto-resizing comment textareas (inline JS — no wasted empty rows)
- [x] Tightened spacing throughout (smaller paddings, lighter card title, tighter tab gap)

**Run:** `~/projects/firefox-triage-dashboard/.venv/bin/triage-dashboard`
(opens `http://127.0.0.1:8765/`)

### Phase 1.5 — Deck view + bug context  ← NEXT

Major UX shift for the draft tabs (§1a / §1b / §1c): instead of a long
scroll of cards, the dashboard shows **one focused rich card at a time**
with a **left rail** to scan all bugs in the current tab and jump to any
of them. The focused card grows in content so the user can act without
bouncing to Bugzilla — reporter, platform, version, inventory, see-also,
description excerpt, recent comments, attachments.

**Watching** stays as a multi-item list (it's monitoring, not action).

**Layout** (mockup: `mockup-deck.html`):
```
┌──────────────────────────────────────────────────────┐
│ topbar · tabs                                         │
├────────┬─────────────────────────────────────────────┤
│ rail   │ deck-nav: pos · prev/next · keyboard hints  │
│ 220px  │ ┌────── focused card ──────────────────┐   │
│  bug-1 │ │ header · title · reporter line       │   │
│  bug-2◀│ │ inventory · see-also                 │   │
│  bug-3 │ │ ▸ description / ▸ comments / ▸ attach│   │
│  …     │ │ draft textarea + composer            │   │
│  bug-10│ │ Will apply · [Skip][Apply][copy]     │   │
│        │ └──────────────────────────────────────┘   │
└────────┴─────────────────────────────────────────────┘
```

**Sub-steps (in order):**
- [x] Layout split: rail (left) + deck stage (right). Watching tab unaffected.
- [x] `?bug=<id>` query param selects the active card; defaults to first in bucket.
- [x] Rail item click navigates to that bug (htmx swap).
- [ ] Deck-nav: position counter + progress bar + prev/next buttons.
- [ ] Keyboard: `↑/↓` prev/next, `a` apply, `s` skip, `/` focus search.
- [ ] Rail filter input (title + bug id substring).
- [ ] **Skill update** (separate session, `~/.claude/skills/triage/SKILL.md`):
  - Teach `/triage` to write a `bug_context` object into pending JSON at draft time.
  - Fields: `description_excerpt`, `platform`, `firefox_version`, `reporter_name`,
    `last_activity`, `inventory_present`, `inventory_missing`, `see_also`,
    `recent_comments`, `attachments`, `ai_reasoning` (§1b only).
- [ ] **Re-run `/triage`** to regenerate the existing pending JSON files with the new richer schema (so the dashboard has real data to render).
- [ ] Extend `Draft` dataclass with optional `bug_context` fields.
- [ ] Render `bug_context` in the focused card (gracefully absent when missing).
- [ ] Tests for active-card selection (done), rail rendering (done), bug_context rendering.

### Phase 2 — Live updates  ← PENDING

Page auto-refreshes when terminal `/triage` writes new pending drafts.

- [ ] Add `watchdog` dependency
- [ ] File watcher on `~/firefox-triage/pending/` and `triage-log.json`
- [ ] SSE endpoint `/events` that pushes reload signals
- [ ] htmx listener on the page that re-fetches affected cards
- [ ] Commit + push

### Phase 3 — Feedback loop with the AI  ← NEXT

The core review-and-revise workflow. You read the AI's draft, write
feedback in your own words ("don't ask about extensions, focus on
codec"), submit. The AI re-drafts using your feedback as context.
Repeat until you're happy, then move on. The textarea stays
read-mostly — direct edits are an escape hatch, not the main path.

**What gets built:**
- [ ] Feedback textarea + "Revise" button on each card
- [ ] `POST /draft/{id}/refine` writes a queue entry to `~/firefox-triage/claude-queue.jsonl`
- [ ] Card shows "⟳ revising…" state with Apply disabled while feedback is pending
- [ ] Revision history per card (collapsed by default), pulled from `~/firefox-triage/revisions/bug-<id>.jsonl`
- [ ] Version badge on the current draft (v1, v2, …)
- [ ] `/process-queue` skill: drains the queue, re-runs `/triage <id>` with feedback + prior draft as context, writes new pending JSON + appends to revision log
- [ ] Tests (queue writer, refine endpoint, revising-state rendering, history rendering)
- [ ] Commit + push

**Storage layout:**
```
~/firefox-triage/
├── pending/bug-<id>.json          ← current draft only
├── claude-queue.jsonl             ← feedback queue (in/out: dashboard writes, /process-queue consumes)
└── revisions/
    └── bug-<id>.jsonl             ← one line per revision: feedback + new draft snapshot
```

**Design decisions (locked):**
- Feedback can change fields (P/S, blocks, NI), not just text — AI revises whatever's appropriate
- Latest feedback wins on contradiction; full history retained as context
- Direct comment edits still allowed (textarea is read-mostly, not read-only) as a tiny escape hatch
- Bug context cached in pending JSON at draft time so /process-queue doesn't need to re-fetch Bugzilla

**Open**: per-paragraph feedback (vs whole-draft) — deferred, only build whole-draft for now.

### Phase 4 — Apply / Skip  ← PENDING

Buttons in the UI actually execute against Bugzilla.

- [ ] `POST /draft/{id}/apply` → subprocess `bugzilla-cli apply {id}`, stream output
- [ ] `POST /draft/{id}/skip` → delete pending file, append `skipped` to triage-log
- [ ] Toast / output pane for apply-result streaming
- [ ] Card fade-out + remove on success
- [ ] Commit + push

### Phase 5 — /bug-start handoff  ← PENDING

§1b cards have a "copy /bug-start {id}" button (already wired up in Phase 1).
This phase adds the queue-based handoff for full automation.

- [x] Clipboard JS for copy button *(done in Phase 1)*
- [ ] On apply for §1b cards, also write a `/bug-start` action to `~/firefox-triage/claude-queue.jsonl`
- [ ] `/process-queue` skill picks it up and runs `/bug-start <id>` (extends Phase 3's skill)
- [ ] Commit + push

### Phase 6 — Claude orchestration  ← PENDING (scope TBD)

Dashboard kicks off `/triage`, `/process-queue`, and `/bug-start` from
the UI without requiring a separate Claude terminal session.

**Open decision**: subprocess `claude -p`, persistent daemon, or Claude
Agent SDK embedded in the server. Pick when we get there.

- [ ] "Run /triage now" button
- [ ] Auto-drain claude-queue.jsonl when items appear (no manual `claude` invocation)
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
