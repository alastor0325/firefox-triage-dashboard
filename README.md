# Firefox Triage Dashboard

A local web dashboard that turns Firefox A/V bug triage into a focused
review-and-approve flow. You run `/triage` in a Claude session, the
dashboard surfaces each draft as a card, you review with full
investigation context, then approve each Bugzilla action one at a time.

The dashboard is the *cockpit* — it doesn't make decisions on its own.
Every Bugzilla write is gated behind your approval and a final
`bugzilla-cli apply` confirmation at the terminal. Nothing posts to
Bugzilla automatically.

---

## The triage loop, end to end

```
1. Run /triage in a Claude session
       │
       │   Claude reads each new A/V bug from the last 14 days,
       │   classifies it, drafts the comment + actions, writes a
       │   pending JSON. For Analyzed bugs it also kicks off a
       │   shallow /bug-start investigation in parallel.
       ▼
2. Open the dashboard (http://127.0.0.1:8765)
       │
       │   Each draft renders as a card. The rail on the left lists
       │   every bug in the active tab; the focused card on the right
       │   shows the full draft + investigation findings + the exact
       │   set of Bugzilla actions that will fire on Apply.
       ▼
3. Review each card
       │   • Revise the draft  →  feedback queued for Claude
       │   • Skip               →  draft dismissed, no Bugzilla write
       │   • Apply              →  queued for a real bugzilla-cli apply
       ▼
4. Drain refines via the Process queue dropdown
       │
       │   Click the topbar "Process queue · N" → "Copy prompt → paste
       │   into Claude". The pasted prompt tells Claude to read the
       │   queue and re-draft each refined bug.
       ▼
5. Apply lands in Bugzilla via the terminal
       │
       │   Each queued apply runs `bugzilla-cli apply <bug_id>`, which
       │   prints the preview of what's about to change and waits for
       │   your [y/N]. That terminal prompt is the safety gate — no
       │   write happens without it.
       ▼
6. For Analyzed bugs, /bug-start fires automatically
           (already ran in parallel during step 1)
```

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

Investigation files live at
`~/firefox-bug-investigation/bug-<id>-investigation.md` and are pushed
to the
[firefox-bug-investigation](https://github.com/alastor0325/firefox-bug-investigation)
GitHub repo so the dashboard's "Open full investigation →" link
resolves publicly.

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
  shows the draft's deltas.

- **Pending feedback** — refines you've queued for this specific bug
  via the composer. ✕ on any row to remove it before draining.

---

## Drain queue

Two kinds of actions queue to `~/firefox-triage/claude-queue.jsonl`:

- **Refines** — when you click "Revise draft" with feedback, the next
  Claude session will re-draft that bug
- **Apply / bug-start** — when you click Apply, the `bugzilla-cli
  apply` and (for Analyzed bugs) `/bug-start` calls get queued

The topbar dropdown shows everything queued. Clicking "Copy prompt"
copies a short instruction set that you paste into a Claude session,
which then reads the JSONL and executes each entry. Real Bugzilla
writes still pause at `bugzilla-cli apply`'s `[y/N]` confirmation —
the prompt explicitly tells Claude not to auto-confirm.

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

# Each session
.venv/bin/triage-dashboard          # opens http://127.0.0.1:8765
```

The dashboard reads from `~/firefox-triage/` by default (or
`$TRIAGE_DIR`). Pending drafts live in `~/firefox-triage/pending/`,
investigations in `~/firefox-bug-investigation/`. Both directories are
created by the `/triage` and `/bug-start` skills, not by the
dashboard.

The dashboard ships with a mock Bugzilla backend by default —
clicking Apply shows what *would* happen but doesn't actually post.
Real writes go through `bugzilla-cli apply` in a separate Claude
terminal, gated by its `[y/N]` confirmation.

---

## Related projects

- **[firefox-bug-investigation](https://github.com/alastor0325/firefox-bug-investigation)** — investigation files written by `/bug-start`, linked from the dashboard's Findings block
- **[Claude-Skills](https://github.com/alastor0325/Claude-Skills)** — the `/triage` and `/bug-start` skills that drive the workflow
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
