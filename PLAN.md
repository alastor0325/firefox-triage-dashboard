# Firefox Triage Dashboard — Implementation Plan

> Living document. Updated whenever phases progress or decisions change.
> Last updated: 2026-05-30 (Phase 5.8 done — Process queue dropdown)

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
- [x] Deck-nav: position counter + progress bar + prev/next buttons.
- [x] Keyboard: `↑/↓` (also `j/k`) prev/next, `a` apply, `s` skip, `/` focus search, `Esc` unfocus.
- [x] Rail filter input (substring filter over bug id + title; client-side).
- [ ] **Skill update** (separate session, `~/.claude/skills/triage/SKILL.md`):
  - Teach `/triage` to write a `bug_context` object into pending JSON at draft time.
  - Fields: `description_excerpt`, `platform`, `firefox_version`, `reporter_name`,
    `last_activity`, `inventory_present`, `inventory_missing`, `see_also`,
    `recent_comments`, `attachments`, `ai_reasoning` (§1b only).
- [ ] **Re-run `/triage`** to regenerate the existing pending JSON files with the new richer schema (so the dashboard has real data to render).
- [x] Extend `Draft` dataclass with optional `BugContext` fields.
- [x] Render `bug_context` in the focused card (byline / inventory / see-also / expandables — gracefully absent when missing).
- [x] Tests for active-card selection, rail rendering, bug_context rendering, schema parsing (90 tests passing).

### Phase 2 — Live updates  ← DONE

Page auto-refreshes when files in `~/firefox-triage/` change underfoot
(terminal `/triage`, `/process-queue`, manual edits, etc.).

- [x] `watchdog` dependency
- [x] `watch.py` with `event_for_path`, `FileWatchBroker` (async pub/sub),
      `TriageDirEventHandler`, `TriageDirWatcher`
- [x] Lifespan: broker bound to event loop, watcher started/stopped
- [x] `GET /events` SSE endpoint streaming `WatchEvent`s
- [x] Pure `sse_event_stream(queue, is_disconnected)` generator (testable
      without HTTP — covers all the streaming logic)
- [x] Client: `EventSource('/events')` + `htmx.ajax` to refresh `#tab-content`
      on `draft-changed` / `draft-deleted` / `watch-changed`
- [x] 30+ tests across pure helpers, broker, FS event mapping, and the
      generator — fast and reliable

### Phase 3 — Feedback loop with the AI  ← DONE

The core review-and-revise workflow. You read the AI's draft, write
feedback in your own words ("don't ask about extensions, focus on
codec"), submit. The AI re-drafts using your feedback as context.
Repeat until you're happy, then move on. The textarea stays
read-mostly — direct edits are an escape hatch, not the main path.

**What gets built:**
- [x] Feedback textarea + "Revise" composer on each card
- [x] `POST /draft/{id}/refine` writes a queue entry to `~/firefox-triage/claude-queue.jsonl`
- [x] Topbar **Process queue · N** button (count-aware via SSE `queue-changed`)
- [x] `POST /queue/prepare` returns the drain prompt for the clipboard *(superseded by Phase 3.5: was originally `CLAUDE_QUEUE_PROMPT.md` + pointer; now a static template + Claude reads JSONL directly)*
- [x] `GET /queue/count` for live count refresh
- [x] Dialog auto-opens with prompt + clipboard copy + re-copy fallback
- [x] Skipped: dedicated `/process-queue` skill — Claude drains the queue inline from the pasted procedure
- [x] Skipped: per-bug "revising…" state and revision history — can revisit if real use shows we need them
- [x] Tests: pure formatter (7), I/O wrapper (4), queue endpoints (6), watch event mapping, topbar button rendering

**Storage layout** *(post Phase 3.5)*:
```
~/firefox-triage/
├── pending/bug-<id>.json          ← current draft only
└── claude-queue.jsonl             ← feedback queue (dashboard writes; Claude drains directly)
```

**Design decisions (locked):**
- Feedback can change fields (P/S, blocks, NI), not just text — AI revises whatever's appropriate
- Latest feedback wins on contradiction; full history retained as context
- Direct comment edits still allowed (textarea is read-mostly, not read-only) as a tiny escape hatch
- Bug context cached in pending JSON at draft time so the drainer doesn't need to re-fetch Bugzilla

**Open**: per-paragraph feedback (vs whole-draft) — deferred, only build whole-draft for now.

### Phase 3.5 — Refinements (queue visibility + simplified handoff)  ← DONE

Two refinements to the Phase 3 design, driven by review feedback (2026-05-29):

#### Q1 — Inline queue visibility per card

Today, after clicking Revise, the textarea clears and you have no idea what's
queued for the bug you're looking at — or for any bug. Multiple Revise clicks
do accumulate (the JSONL is append-only), but the UI doesn't show it.

Fix: under each card's composer, render a "Pending feedback (N)" list when
the bug has queued items. Each entry shows timestamp + feedback text + a ✕
button to remove that specific entry. SSE `queue-changed` events refresh
the affected card section live.

- [x] Backend: `pending_feedback_for(triage_dir, bug_id)` reads JSONL and
      returns entries for that bug, ordered chronologically.
- [x] Backend: `POST /draft/{bug_id}/refine/remove` (with the entry's `ts`)
      rewrites the JSONL without that line; the watcher emits queue-changed.
- [x] Frontend: render the list under the composer in `card.html`.
- [x] Tests for both the helper and the endpoint, and for rendering.

#### Q2 — Drop the on-disk MD; short prompt + Claude reads JSONL (Option C)

Today, `/queue/prepare` writes `~/firefox-triage/CLAUDE_QUEUE_PROMPT.md` and
returns a short "read this file" pointer. The MD is built by stitching the
procedure block with each queued feedback rendered inline.

The design has two distinct files on disk (`claude-queue.jsonl` + the MD),
and the procedure text is regenerated and rewritten on every Process queue
click. Both can be avoided.

Fix: the procedure becomes a Python constant; `/queue/prepare` returns it
filled-in with the actual queue/pending paths, plus `{count, bugs_affected}`.
The clipboard payload IS the full procedure (~900 bytes), and tells Claude
to read `claude-queue.jsonl` itself and apply each entry to the matching
pending JSON. No second file on disk; nothing to clean up.

- [x] Replace `prepare_queue_drain` so it returns
      `{count, prompt, bugs_affected}` — no file I/O for the prompt.
- [x] Add `DRAIN_PROMPT_TEMPLATE` constant; remove `format_queue_prompt`
      and `PROMPT_FILE`.
- [x] Update `/queue/prepare` to surface the new shape.
- [x] Update the dialog copy in `index.html` — the paste is the full
      procedure, not a pointer.
- [x] Update unit + integration tests.

**Design rationale**:
- Storage = JSONL (programmatic ops: count badge, per-card list, remove).
- Clipboard = short text prompt with the procedure inline; tells Claude
  what files to read and what to do. Claude does the Read tool calls itself.
- One on-disk artifact for queue state (`claude-queue.jsonl`). The procedure
  lives in code as a template constant.
- Pending JSONs aren't queue infrastructure — they pre-exist as the drafts
  the dashboard renders.

### Phase 4 — Apply / Skip via swappable backend (mock-first)  ← NEXT

The dashboard's primary buttons (Apply / Skip / Apply & close / Reassign)
need to actually do something. Wiring them straight to `bugzilla-cli`
is risky — Bugzilla writes are production and irreversible, and we
don't trust the dashboard wiring blindly yet.

**Approach**: a swappable backend interface. The default is a **mock**
that computes and returns the plan without touching Bugzilla or local
state. A second slot is reserved for real `bugzilla-cli` invocation,
but **its implementation is gated** behind explicit user approval —
the class exists as a stub that raises `NotImplementedError`.

**Architecture**:
```
src/triage_dashboard/
├── applier.py   ← pure planner (done — commit 6e7119a)
│                  plan_apply(pending) → list[PlannedAction]
│                  plan_skip(pending)  → list[PlannedAction]
├── backend.py   ← NEW
│                  BackendResult dataclass: {ok, actions, output, side_effects}
│                  TriageBackend ABC: apply() / skip()
│                  MockBackend       — computes plan, no I/O
│                  BugzillaCLIBackend — stub; methods raise NotImplementedError
│                  get_backend()     — env-var-selected; default = mock
└── app.py       ← POST /draft/{id}/apply, POST /draft/{id}/skip
                   call get_backend(), return BackendResult
```

**Two gates between us and real Bugzilla writes**:
1. `TRIAGE_DASHBOARD_LIVE=1` env var (your conscious flip).
2. `BugzillaCLIBackend.apply/skip` actually have implementations.

Setting only gate (1) selects the real backend but its methods raise,
so nothing happens. Both gates active = real writes. The implementation
of gate (2) requires a separate, explicit "go" — see Phase 4.5.

**Where we examine mock results**:
- `BackendResult.actions` is a structured list — tests assert directly on it.
- The endpoint returns it (JSON for curl, HTML fragment for htmx).
- The dashboard status panel renders it for visual inspection.

**Sub-steps**:
- [x] `backend.py` with `BackendResult`, ABC, `MockBackend`, `BugzillaCLIBackend` stub, `get_backend()`
- [x] Unit tests: mock returns expected shape; stub raises; env-var selection works
- [x] `POST /draft/{id}/apply` and `POST /draft/{id}/skip` endpoints (with 501 surfaced if LIVE=1 but real backend unimplemented)
- [x] Integration tests with mock backend
- [x] UI wiring — buttons enabled, htmx-post, `BackendResult` rendered in per-card status panel

### Phase 4.5 — Real bugzilla-cli backend  ← GATED, DO NOT START WITHOUT EXPLICIT APPROVAL

- [ ] Implement `BugzillaCLIBackend.apply()` — subprocess `bugzilla-cli apply {id}`, capture stdout/stderr, parse, return `BackendResult`
- [ ] Implement `BugzillaCLIBackend.skip()` — delete pending file, append `skipped` to triage-log
- [ ] Cross-check parity: `MockBackend.apply().actions == BugzillaCLIBackend(...).preview(pending)` for the same pending JSON
- [ ] Integration tests via subprocess mocking
- [ ] **Do not start without explicit user request.**

### Phase 5 — /bug-start handoff  ← DONE

§1b cards have a "copy /bug-start {id}" button (already wired up in Phase 1).
This phase adds the queue-based handoff for full automation.

- [x] Clipboard JS for copy button *(done in Phase 1)*
- [x] On apply for §1b cards, also write a `/bug-start` action to `~/firefox-triage/claude-queue.jsonl`
- [x] Drain prompt explains both action types so Claude handles refines and bug-starts in one paste
- [x] Topbar badge counts bug-start entries alongside refines
- [x] Tests for §1a/§1b/§1c apply and skip
- [x] Commit + push

**Notes**:
- §1a apply (needinfo) and §1c apply (resolve/reassign) do NOT queue bug-start —
  there's nothing to investigate yet (§1a) or the bug is being closed (§1c).
- Apply failures (e.g. live mode with no real backend → 501) do NOT queue —
  the bug-start follows a successful apply, not an attempted one.
- The per-card "Pending feedback" list stays refine-only by design — bug-start
  isn't feedback on the draft, it's a follow-up action. Users see it only
  via the topbar count and the drain prompt.

### Phase 5.5 — Apply handoff via queue (gated by bugzilla-cli [y/N])  ← DONE

The dashboard's Apply button doesn't go straight to Bugzilla (that's Phase 4.5,
still gated). Instead, Apply queues an `apply` action; the drained Claude
session runs `bugzilla-cli apply <id>` which prompts the user with `[y/N]`
in their terminal before any production write happens. That preserves the
human-in-the-loop confirmation step without needing the full Phase 4.5 build.

**What gets built:**
- [x] `claude_queue.append_apply` + `apply` added to `DRAINABLE_ACTIONS`
- [x] `/draft/{id}/apply` queues an `apply` entry on every successful apply
      (§1a / §1b / §1c — all sections); §1b additionally queues bug-start
- [x] Drain prompt extended with an apply step + explicit no-auto-confirm gate
      (must wait for user `[y/N]` at the `bugzilla-cli apply` prompt)
- [x] Drain order locked to refines → applies → bug-starts
- [x] Status panel after Apply explains the queue + gate
- [x] Tests: append_apply, drain prompt has gate language, drain order,
      §1a/§1b/§1c apply queueing, skip queues nothing, failed apply queues
      nothing, status panel hint

**Notes**:
- Skip queues nothing — it's local-only, no Bugzilla side effect.
- Re-apply double-queues; the drain prompt de-duplicates by distinct bug_id.
- Drain prompt forbids `--yes` / `-y` flags AND stdin-piping bypasses
  (`yes |`, `echo y |`, heredocs, herestrings) — the realistic ways a
  drainer model would defeat the `[y/N]` gate.
- This pattern means Phase 4.5 (real `BugzillaCLIBackend.apply`) is optional
  in practice — the user can run live writes through the drain flow today.

**`TRIAGE_DASHBOARD_LIVE` is bypassed by design**:
The env var gates only the in-process `BugzillaCLIBackend`. Phase 5.5
routes around it via the queue, so real Bugzilla writes can happen with
the default mock backend. That's intentional — the user has to take
**three deliberate actions** to land a real write:
  1. Click Apply on the dashboard (queues the action).
  2. Click Process queue and paste the prompt into a Claude session.
  3. Type `y` at the `bugzilla-cli apply` `[y/N]` prompt for each bug.
The CLI's `[y/N]` is the actual safety gate, not the env var.

### Phase 5.8 — Process queue dropdown (replaces the modal)  ← DONE

The topbar Process queue button is now a `<details>` dropdown rather
than the (removed) modal dialog. Inline rows mirror the Queue tab's
content but in a compact, always-one-click-away view; explicit "Copy
prompt" button replaces the auto-copy-on-click behaviour.

- [x] `GET /queue/dropdown` returns the dropdown's HTML fragment.
- [x] Topbar uses `<details>` + `<summary>` (button) + a server-rendered
      `_queue_dropdown.html` panel.
- [x] Copy is explicit — clicking the dropdown no longer mutates the
      clipboard. The footer button fetches `/queue/prepare` and copies
      its `prompt` to the clipboard, then shows "Copied" feedback.
- [x] SSE `queue-changed` now refreshes the dropdown contents (not just
      the count badge) so the panel stays in sync when external tools
      mutate the JSONL.
- [x] Click-outside closes the dropdown.
- [x] The modal dialog (`queue-prompt-dialog`, the prompt textarea,
      Done/Copy-again buttons) is removed.

**Rationale**:
- Opening the dropdown ≠ intent to drain. Auto-copy clobbered the
  clipboard for users who just wanted to peek; explicit copy respects
  whatever the user had previously copied.
- Inline rows make "what's queued?" answerable in one click rather than
  requiring a tab switch.
- The Queue tab (Phase 5.7) is retained for the bigger view, but
  the dropdown is now the fast path.

### Phase 5.7 — Queue inspector tab  ← DONE

A "Queue" tab in the topbar lists every queued action (refines, applies,
bug-starts) in chronological order. Each row jumps to its bug's card and
can be removed individually without draining the queue.

- [x] Generalize `remove_refine` → `remove_entry(action, bug_id, ts)`;
      keep `remove_refine` as a thin wrapper for backwards compatibility.
- [x] `claude_queue.all_queued_actions(triage_dir)` returns structured
      rows ({action, bug_id, ts, feedback?}) skipping unknown / malformed
      lines.
- [x] `POST /queue/remove` accepting form `{action, bug_id, ts}`. 400 on
      missing fields or unknown action; 404 on no match.
- [x] "Queue" tab in TABS; `queue.html` template; per-row action badge,
      bug-id link to its section tab, action-specific detail, ts, ✕.
- [x] Orphan rows (bug has no pending JSON) render without the link.
- [x] Tab count badge mirrors the queue length.
- [x] Tests across helper, endpoint, and template.

**Notes**:
- The per-card "Pending feedback" list (Phase 3.5 Q1) stays — it's the
  fast path for managing refines on the card you're currently editing.
  The Queue tab is the broad view across all bugs and all action types.
- The ✕ button's hx-swap removes the row instantly; SSE `queue-changed`
  also refreshes the rest of the page so counts stay in sync.

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
