"""Action queue: dashboard writes, a separate Claude session drains.

The queue is a JSONL file at `<triage_dir>/claude-queue.jsonl`. Each line
is one action:

- `refine`    — re-draft a pending triage comment given user feedback.
- `apply`     — run `bugzilla-cli apply <bug_id>` to post the draft to
                Bugzilla; the CLI's [y/N] prompt is the production-write
                gate.
- `bug-start` — kick off `/bug-start <bug_id>` after a §1b Apply.

`prepare_queue_drain` builds the short clipboard prompt the dashboard hands
the user when they click "Process queue". The prompt tells Claude where to
find the JSONL and what to do — Claude reads the JSONL itself, so the
queue contents are NOT embedded in the prompt. This keeps the clipboard
payload small and avoids any second on-disk artifact.

Single-writer assumption: the dashboard is the only producer of queue
entries, and a Claude drain session is the only consumer. Concurrent
writes (e.g. dashboard appending while drain truncates) are not
guarded against — this is a local single-user tool.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_FILE = "claude-queue.jsonl"

DRAIN_PROMPT_TEMPLATE = """\
Drain the Claude queue.

Files involved:
- {queue_path}
    Each line is one of three action records:
      refine:     {{"action":"refine","bug_id":<int>,"feedback":<string>,"ts":<iso8601>}}
      apply:      {{"action":"apply","bug_id":<int>,"ts":<iso8601>}}
      bug-start:  {{"action":"bug-start","bug_id":<int>,"ts":<iso8601>}}
- {pending_dir}/bug-<id>.json
    The current triage draft for that bug. Fields include `comment`,
    `severity`, `priority`, `resolution`, `blocks_add`, `ni_targets`,
    `keywords_add`, `cc_add`, `component`, `product`, and a free-form
    `ai_reasoning` block.

Procedure:

1. Read {queue_path}. If it is empty, stop.

2. Partition entries by action:
   - All `refine` entries → group by bug_id.
   - All `apply` entries → collect distinct bug_ids.
   - All `bug-start` entries → collect distinct bug_ids.

3. Apply refines first. Process the bugs with refines CONCURRENTLY, not
   one-at-a-time — the bugs are independent and serial processing wastes
   time. Bug-id order does not matter.

   a. Fan out investigations in parallel. Many refines ask for a "deeper
      investigation" (code navigation, profile analysis, bug/email
      lookups). These are the slow part and they are independent, so
      launch them as parallel background subagents (`Agent` with
      `run_in_background: true`) in a single batch up front — one per
      bug that needs investigation. Pick the right agent type per task
      (e.g. `gecko-navigator` for Gecko code questions). Refines that are
      purely directive (reword, change S/P, add a blocker) need no
      subagent.
   b. As each investigation returns, invoke the `triage-apply-feedback`
      skill via the Skill tool with the bug_id and that bug's feedback
      list, passing along the investigation findings. The skill handles
      the redraft AND the required lesson-extraction pass that keeps
      /triage improving over time — do not inline the refine logic here.
      Skipping the skill means future runs lose the correction signal.
   c. Wiki lessons: decide autonomously, do NOT ask the user to confirm.
      The skill drafts each lesson and applies its step-4 criteria (add a
      cited, generalizable, durable fact to the wiki; route process /
      drafting rules to the /triage skill; skip one-offs, unsourced
      claims, and security-bug details). Add the worthwhile ones yourself
      and record each decision (added / skipped / skill-updated, with the
      reason) in the decisions-log — that log is the audit trail and the
      user can veto via a later refine. No AskUserQuestion gate.
   d. SAFETY: if a refine requires downloading a file (e.g. a bug
      attachment, media sample, or fixture) to investigate, follow the
      `/download-guard` rule — never auto-download. It presents a Yes/No
      AskUserQuestion per file and, on Yes, fetches into the one shared
      `~/.cache/firefox-download-guard/` temp folder. Such a bug stays
      blocked until the user approves; process the others in parallel
      meanwhile.

4. For each queued apply (distinct bug_ids only), confirm with the user
   via the AskUserQuestion tool BEFORE applying — one yes/no question
   per bug. In the question, summarize exactly what the apply will write
   (the draft's `comment` and the field changes from the pending JSON:
   severity/priority, resolution, ni_targets, blocks_add, keywords_add,
   cc_add, component/product, dupe_of) so the user can decide.

   **SAFETY GATE — this is the production-write boundary. Read carefully:**
   - Ask ONE AskUserQuestion per bug, with a clear Yes (apply) / No
     (skip) choice. The user's answer to that question IS the approval —
     there is no separate terminal [y/N] step.
   - If the user chooses No (or dismisses the question): do NOT apply
     that bug, and leave its queue entry intact.
   - If the user chooses Yes: run the apply, exactly:

         bugzilla-cli apply <bug_id>

     The CLI prompts its own [y/N]; supply the approval the user just
     gave by feeding `y` on stdin (e.g.
     `printf 'y\\n' | bugzilla-cli apply <bug_id>`). NEVER apply a bug
     the user did not explicitly approve in its question.
   - If the apply errors, stop and ask the user how to proceed. Do NOT
     charge ahead to the next apply.
   - On a successful apply the CLI posts to Bugzilla and deletes the
     pending JSON. Move on to the next bug.

5. For each bug-start entry, invoke the `bug-start` skill via the Skill
   tool with the bug_id as its argument — do not just print the slash
   command, actually run the skill so the investigation flow kicks off.
   Distinct bug_ids only (de-duplicate if the same bug appears twice).

6. After all actions are processed successfully, truncate {queue_path}
   to empty (write a zero-byte file). If any apply was declined by the
   user, leave the queue intact and let the user decide what to do.

7. Print a one-line summary per bug describing what happened, e.g.
     2039425: refined — shortened analysis
     2040167: applied (user confirmed)
     2042320: apply declined by user
     2045110: /bug-start invoked

Begin.
"""


def append_refine(
    triage_dir: Path,
    *,
    bug_id: int,
    feedback: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a refine-the-draft action to the queue file.

    Raises ValueError if feedback is empty/whitespace — there's nothing
    for Claude to do with it.
    """
    stripped = feedback.strip()
    if not stripped:
        raise ValueError("refine feedback must not be empty")

    triage_dir.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    entry: dict[str, Any] = {
        "action": "refine",
        "bug_id": int(bug_id),
        "feedback": stripped,
        "ts": ts,
    }
    with (triage_dir / QUEUE_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def append_bug_start(
    triage_dir: Path,
    *,
    bug_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a bug-start action to the queue file.

    Queued by /draft/<id>/apply for §1b drafts; drained by a Claude
    session which then invokes `/bug-start <bug_id>`.
    """
    triage_dir.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    entry: dict[str, Any] = {
        "action": "bug-start",
        "bug_id": int(bug_id),
        "ts": ts,
    }
    with (triage_dir / QUEUE_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def append_apply(
    triage_dir: Path,
    *,
    bug_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append an apply action to the queue file.

    Queued by /draft/<id>/apply for any draft; drained by a Claude
    session which then runs `bugzilla-cli apply <bug_id>`. The CLI's
    built-in [y/N] prompt is the production-write gate — the drain
    prompt instructs Claude NOT to auto-confirm.
    """
    triage_dir.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    entry: dict[str, Any] = {
        "action": "apply",
        "bug_id": int(bug_id),
        "ts": ts,
    }
    with (triage_dir / QUEUE_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _read_jsonl(path: Path) -> list[dict]:
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


DRAINABLE_ACTIONS = ("refine", "apply", "bug-start")


def _refines(entries: list[dict]) -> list[dict]:
    return [
        e for e in entries
        if e.get("action") == "refine" and e.get("bug_id")
    ]


def _drainables(entries: list[dict]) -> list[dict]:
    """Entries the drainer will act on — used by counts and the badge."""
    return [
        e for e in entries
        if e.get("action") in DRAINABLE_ACTIONS and e.get("bug_id")
    ]


def all_queued_actions(triage_dir: Path) -> list[dict]:
    """Return every drainable queue entry in chronological file order.

    Used by the queue-inspector tab. Each item has:
      - `action`: one of `DRAINABLE_ACTIONS`
      - `bug_id`: int
      - `ts`: str (the original ISO timestamp — also the remove-key)
      - `feedback`: str for refines, else None

    Non-drainable / unknown / malformed lines are silently skipped, so
    the inspector never crashes on a partially-written file or a future
    action shape it doesn't recognise.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return []
    out: list[dict] = []
    for e in _drainables(_read_jsonl(queue_path)):
        out.append({
            "action": e["action"],
            "bug_id": int(e["bug_id"]),
            "ts": e.get("ts", ""),
            "feedback": e.get("feedback") if e["action"] == "refine" else None,
        })
    return out


def apply_bug_ids(rows: list[dict]) -> set[int]:
    """Pure: the set of bug ids with a queued `apply` action, from the rows
    `all_queued_actions` returns. Drives the 'queued to apply this round'
    treatment on cards + rail rows."""
    out: set[int] = set()
    for r in rows:
        if r.get("action") == "apply":
            try:
                out.add(int(r["bug_id"]))
            except (KeyError, TypeError, ValueError):
                pass
    return out


def pending_feedback_for(triage_dir: Path, bug_id: int) -> list[dict]:
    """Return all queued refine entries for `bug_id`, file order preserved.

    Each entry has keys `bug_id`, `feedback`, `ts`. Returns an empty list
    when the queue file is missing or has no matching entries.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return []
    out: list[dict] = []
    for e in _refines(_read_jsonl(queue_path)):
        if int(e["bug_id"]) == int(bug_id):
            out.append({
                "bug_id": int(e["bug_id"]),
                "feedback": e.get("feedback", ""),
                "ts": e.get("ts", ""),
            })
    return out


def remove_entry(
    triage_dir: Path, *, action: str, bug_id: int, ts: str,
) -> bool:
    """Remove the one queue entry matching (action, bug_id, ts).

    Returns True if a matching entry was removed; False if the file or
    entry was missing, or if the action discriminator didn't match.

    The JSONL is rewritten in place, preserving all non-matching lines
    verbatim (including malformed lines, so we don't silently destroy
    anything we don't understand).

    Concurrency: this is a non-atomic read-then-write. Callers must assume
    a single writer — the dashboard is local single-user, so concurrent
    appends from another process are not a real risk here.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return False

    kept: list[str] = []
    removed = False
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not removed:
            stripped = line.strip()
            if stripped:
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    obj = None
                if (
                    isinstance(obj, dict)
                    and obj.get("action") == action
                    and int(obj.get("bug_id") or 0) == int(bug_id)
                    and obj.get("ts") == ts
                ):
                    removed = True
                    continue
        kept.append(line)

    if not removed:
        return False

    body = "\n".join(kept)
    if body and not body.endswith("\n"):
        body += "\n"
    queue_path.write_text(body, encoding="utf-8")
    return True


def remove_refine(triage_dir: Path, *, bug_id: int, ts: str) -> bool:
    """Phase 3.5 wrapper — delegates to `remove_entry` for refine."""
    return remove_entry(triage_dir, action="refine", bug_id=bug_id, ts=ts)


def is_apply_queued(triage_dir: Path, bug_id: int) -> bool:
    """True if at least one `apply` entry for `bug_id` is in the queue.

    This is the source of truth for the dashboard's Apply/Applied toggle:
    a draft is "applied" iff its apply action is sitting in the queue
    waiting to be drained.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return False
    for entry in _read_jsonl(queue_path):
        if entry.get("action") == "apply" and int(entry.get("bug_id") or 0) == int(bug_id):
            return True
    return False


def remove_apply(triage_dir: Path, bug_id: int) -> bool:
    """Remove every queued `apply` entry for `bug_id` (the toggle's revert).

    Returns True if at least one entry was removed. Unlike `remove_entry`
    this is keyed on (action, bug_id) only — the dashboard has no handle on
    the original ts when the user clicks "Applied" to undo, and a draft
    should never legitimately have more than one pending apply anyway.
    Non-matching lines (including malformed ones) are preserved verbatim.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return False

    kept: list[str] = []
    removed = False
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = None
            if (
                isinstance(obj, dict)
                and obj.get("action") == "apply"
                and int(obj.get("bug_id") or 0) == int(bug_id)
            ):
                removed = True
                continue
        kept.append(line)

    if not removed:
        return False

    body = "\n".join(kept)
    if body and not body.endswith("\n"):
        body += "\n"
    queue_path.write_text(body, encoding="utf-8")
    return True


_EMPTY = {"count": 0, "prompt": None, "bugs_affected": 0}


def prepare_queue_drain(triage_dir: Path) -> dict[str, Any]:
    """Build the short clipboard prompt for draining the queue.

    Returns `{count, prompt, bugs_affected}`. `count` covers every
    drainable action type (`refine`, `apply`, `bug-start`) — i.e.
    everything the drainer will touch. `bugs_affected` is the number
    of distinct `bug_id`s across all those actions. When the queue is
    missing or has no drainable entries, returns the empty shape.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return dict(_EMPTY)

    drainables = _drainables(_read_jsonl(queue_path))
    if not drainables:
        return dict(_EMPTY)

    prompt = DRAIN_PROMPT_TEMPLATE.format(
        queue_path=str(queue_path),
        pending_dir=str(triage_dir / "pending"),
    )
    return {
        "count": len(drainables),
        "prompt": prompt,
        "bugs_affected": len({int(e["bug_id"]) for e in drainables}),
    }
