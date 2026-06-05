# Firefox Triage Dashboard

A local web dashboard that turns Firefox A/V bug triage into a focused
review-and-approve flow. You run `/triage` in a Claude session, the
dashboard surfaces each draft as a card, you review with full
investigation context, then approve each Bugzilla action one at a time.

The dashboard is the *cockpit* — it doesn't make decisions on its own.
Every Bugzilla write is gated behind your approval and a final
`bugzilla-cli apply` confirmation at the terminal. Nothing posts to
Bugzilla automatically.

### Read-only vs reply mode

The dashboard detects whether a Bugzilla **API key** is configured — the same
signal `bugzilla-cli` uses (`$BUGZILLA_BOT_API_KEY`, or
`~/.config/triage/secrets`):

- **Reply mode** (key present) — full flow: Apply / skip per card and the
  Process-queue drain that writes via `bugzilla-cli apply`.
- **Read-only** (no key) — the dashboard shows a **"read-only · drafts only"**
  badge, replaces each card's Apply with a disabled **Read-only** pill, and the
  Process-queue drain prompt is told to **skip the apply (write) step**. You
  still get the full AI drafts to review; nothing can be written back until you
  enable reply mode (`bugzilla-cli setup`). Detection is live — configure a key
  and reload to switch to reply mode.

---

## The triage loop, end to end

Three actors share the work: **`/triage`** analyzes bugs and writes
drafts (read-only on Bugzilla), the **dashboard** is where you review and
decide, and the **Process queue** drain is the *only* thing that writes to
Bugzilla — and only after a per-bug approval.

```
        ┌──────────────────────────────────────────────────────────┐
        │  /triage  (full)            READ-ONLY on Bugzilla        │
        │ ───────────────────────────────────────────────────────  │
        │  1. watch-poll  → replies on NI'd bugs                   │
        │       (replied / ni_cleared / stale / auto_removed / …)  │
        │  2. fetch new bugs (last 14 days)                        │
        │  3. analyze each (parallel sub-agents;                   │
        │       /bug-start investigation for §1b)                  │
        └───────────────────────────┬──────────────────────────────┘
                                    │ writes (local files only)
                                    ▼
      pending/bug-<id>.json  +  ni-watch.json  +  investigation .md
                                     │
                                     ▼
        ┌──────────────────────────────────────────────────────────┐
        │  DASHBOARD            reads those files; never writes BMO│
        │  tabs: Analyzed · Needs Info · Close/Reassign · Awaiting │
        │  you review each draft →                                 │
        └────┬───────────────────┬───────────────────┬─────────────┘
             │ Revise            │ Apply (toggle)    │ Skip
             ▼                   ▼                   ▼
        queue: refine       queue: apply       delete draft
        (+feedback)              │              (no queue)
             └───────────────────┴──── append → claude-queue.jsonl
                                     │
                                     ▼
        ┌──────────────────────────────────────────────────────────┐
        │  PROCESS QUEUE  (drain prompt) — partitions by action    │
        │ ───────────────────────────────────────────────────────  │
        │  refine    → /triage-apply-feedback                      │
        │               (re-draft JSON + capture wiki lesson)      │
        │  apply     → AskUserQuestion per bug  (yes / no)         │
        │               └─ yes → bugzilla-cli apply   ◄── ONLY     │
        │                        • posts comment/NI/fields to BMO  │
        │                        • archives draft → applied/       │
        │                        • adds bug to ni-watch            │
        │  bug-start → /bug-start                                  │
        └───────────────────────────┬──────────────────────────────┘
                                    │ applied bug
                                    ▼
        Awaiting reply tab  ◄── folded report (applied/ + investigation),
                                     │          no AI draft comment
                                     │  reporter answers the NI on Bugzilla
                                     ▼
        next  /triage  → watch-poll sees the reply ──────┐
                                                         │
        ◄────────────────────────────────────────────────┘  loop
```

**Second round?** Just run full `/triage` again (no arguments). One run
both re-polls the NI watch list for replies (`watch-poll`) and fetches
any new bugs from the last 14 days. `/triage <id>` single-bug mode skips
*both*, so use bare `/triage` to close the loop.

---

## What each tab means

The topbar has four tabs. Each one represents a decision class — a
draft sits in exactly one of them, based on what's in its pending JSON.

### Analyzed (§1b)

These are bugs you've **triaged**: severity + priority set, root cause
identifiable from the available data (media-preset profile, media log
with NS_ERROR / decoder signal, crash ID with media stack frame, or
reporter-cited source code). The draft includes:

- A public comment summarizing findings
- Severity + priority
- Optional meta-bug blocker (e.g. autoplay, YouTube playback)
- A needinfo on the triage owner so the bug enters their queue

**Apply does:** posts the comment, sets S/P, adds blocks/CC, sends the
NI — all bundled into one atomic `bugzilla-cli apply` call.

Each Analyzed card carries a **Findings** block surfacing the
`/bug-start` investigation: root cause, affected files (linked to
searchfox at the specific lines cited), regression range, related
bugs, complexity. The investigation runs in shallow ("triage") mode
during `/triage` so the dashboard has data to show without making
`/triage` take an hour. Re-run `/bug-start <bug_id>` (no flag) in
deep mode when you're ready to implement.

### Needs Info (§1a)

The bug doesn't yet have enough data for triage. The draft asks the
reporter (or sometimes a Mozilla engineer) for the specific missing
pieces — `about:support`, a media-preset profile, a clean-profile
test, a media log with the `media` preset enabled, etc.

**Apply does:** posts the question + sets the needinfo flag on the
reporter. Adds the bug to a local watch list so we notice when the
reporter replies.

### Close / Reassign (§1c)

The bug shouldn't live in A/V triage queue any longer:

- **Duplicate** of another bug — resolve as DUPLICATE, link the original
- **Invalid** — works as intended, post the rationale
- **Incomplete** — reporter stalled, no path forward
- **Wrong component** — reassign to the team that actually owns it
- **Stalled** — add the `stalled` keyword, leave open

**Apply does:** sets the resolution (or reassigns product/component)
and posts the explanatory comment.

### Awaiting reply

Bugs we've already needinfo'd, waiting on a response. This tab is
read-only — there's no Apply action because we're not the actors
right now. A `stalled` indicator surfaces when the NI is more than
14 days old, so we know which ones need a follow-up or an
INCOMPLETE resolution.

Each entry keeps the bug's full context from when it was applied —
component and current S/P in the collapsed row, and a folded **Details**
disclosure with the report (platform, reporter, `ai_reasoning`), the
applied changes, and any investigation findings. The AI draft comment is
the only thing dropped. The detail is restored from the archived draft
(`~/firefox-triage/applied/bug-<id>.json`, written by `bugzilla-cli
apply`) plus the investigation file.

---

## How we investigate bugs

The dashboard doesn't investigate code itself — that's `/bug-start`'s
job. The two skills work together via the dashboard:

- **`/triage`** classifies each bug. For Analyzed bugs, it dispatches a
  shallow `/bug-start --triage-mode` subagent in parallel with the
  others (parallel cap of 4 at a time, so total wall-clock is bounded).

- **`/bug-start --triage-mode`** produces a shallow but useful
  investigation file: root cause hypothesis or verification, affected
  files (with line citations where the body cites them), regression
  range if found, related bugs, complexity estimate. It does *not*
  produce an implementation plan, patch arrangement, or test plan —
  that's deep mode's job, run later when you're ready to write code.

- **`/bug-start` (no flag)** runs deep mode and produces the full
  investigation document. You invoke this manually when you're about
  to fix the bug.

Investigation files live in `$FX_BUG_INVESTIGATION_DIR` (default
`~/.fx-bug-toolkit/bug-investigation/`, shared with the fx-bug-toolkit
plugin that writes them) as `bug-<id>-investigation.md`. The dashboard
serves them itself at `/investigation/<id>`, so the "Open full
investigation →" link is always valid and nothing has to leave your
machine (no remote repo required).

If the bug doesn't have enough data for `/bug-start` to be useful (no
media profile, no media log, no crash signature, etc.), the skill
classifies it Needs Info instead and asks the reporter for the
missing piece — no investigation runs.

---

## Key signals on a card

While reading a card, watch for these:

- **Rail tags** (small pills on each rail item):
  - `emergency` — bug has `sec-critical`, `sec-high`, or `topcrash`
    keyword. Look at this first.
  - `regression` — bug has the `regression` keyword. Backout candidate
    or land-day fast-track.
  - `stalled` (Awaiting reply tab only) — NI sent >14 days ago.

- **Findings block** on Analyzed cards — investigation results.
  - Status pill: `investigated` (done) / `investigating` (mid-run) /
    `investigation-stalled` (lock file >30 min old, /bug-start may
    have crashed)
  - "shallow · re-run for deep" badge when the investigation was
    triage-mode only

- **Card-head S/P pills** show the bug's *current* Bugzilla state —
  not what the draft will set. The "Will apply" footer at the bottom
  shows the draft's deltas. In that footer the **severity / priority
  are editable dropdowns**: they default to the AI's proposed level,
  and selecting a different one overrides what will actually be applied
  this round.

- **Pending feedback** — refines you've queued for this specific bug
  via the composer. ✕ on any row to remove it before draining.

---

## Drain queue

Actions queue to `~/firefox-triage/claude-queue.jsonl`:

- **Refines** — when you click "Revise draft" with feedback, the next
  Claude session re-drafts that bug via `/triage-apply-feedback`
- **Apply** — clicking Apply queues a single `apply` action and the
  button flips to **Applied**; clicking Applied again removes it (the
  toggle is reversible). `/bug-start` is **not** chained onto Apply — it
  already runs during `/triage` for Analyzed bugs.

The topbar dropdown shows everything queued. Clicking "Copy prompt"
copies a short instruction set that you paste into a Claude session,
which then reads the JSONL and processes each entry. For every queued
apply, the drain asks you **per bug** (a yes/no question) before posting;
on yes it runs `bugzilla-cli apply <id>` with your approval. That per-bug
confirmation is the safety gate — no Bugzilla write happens without it.

You can remove queued items individually (✕ in the dropdown) without
draining the whole queue.

---

## Running it locally

```bash
# One-time
git clone https://github.com/alastor0325/firefox-triage-dashboard.git
cd firefox-triage-dashboard
python3 -m venv .venv
.venv/bin/pip install -e .

# Each session — foreground (blocks the terminal, opens a browser)
.venv/bin/triage-dashboard          # opens http://127.0.0.1:8765

# Or run it in the background with the control script
scripts/serve.sh start              # launch (no browser pop), health-checked
scripts/serve.sh status             # is it up? PID + HTTP check
scripts/serve.sh restart            # stop + start
scripts/serve.sh logs               # tail the server log
scripts/serve.sh stop               # stop it
# env overrides: HOST=0.0.0.0 PORT=9000 scripts/serve.sh start
```

The dashboard reads triage data from `~/firefox-triage/` by default (or
`$TRIAGE_DIR`), and investigations from `$FX_BUG_INVESTIGATION_DIR`
(default `~/.fx-bug-toolkit/bug-investigation/` — the same variable and
default the fx-bug-toolkit plugin uses, so both see the same files).
Pending drafts live in `~/firefox-triage/pending/`. These directories are
created by the `/triage` and `/bug-start` skills, not by the dashboard.

The dashboard ships with a mock Bugzilla backend by default —
clicking Apply shows what *would* happen but doesn't actually post.
Real writes go through `bugzilla-cli apply` in a separate Claude
terminal, gated by its `[y/N]` confirmation.

---

## Related projects

- **[fx-bug-toolkit](https://github.com/alastor0325/fx-bug-toolkit)** — the Claude Code plugin behind the workflow: `/bug-start` writes the investigation files this dashboard reads (from `$FX_BUG_INVESTIGATION_DIR`), and the `/triage` skills are being folded in there too
- **[bugzilla-cli](https://github.com/alastor0325/bugzilla-cli)** — the underlying tool for Bugzilla I/O, including the `[y/N]` apply prompt

---

## Why this exists

A/V triage involves a lot of context per bug: the bug's description,
artifacts (profiles, logs, crashes), prior triage history, related
bugs, and increasingly, AI-generated analysis. Doing this in
Bugzilla's web UI means flipping between many tabs per bug. The
dashboard consolidates all of it into one card per bug, surfaces the
investigation findings inline, and keeps the human-in-the-loop
guarantee firmly at the `bugzilla-cli apply` boundary — so the speedup
comes from better tooling, not from skipping review.
