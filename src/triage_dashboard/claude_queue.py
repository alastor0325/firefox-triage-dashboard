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

3. Apply refines first. For each bug with refines, in ascending bug_id
   order:
   a. Read {pending_dir}/bug-<id>.json.
   b. Apply all feedback entries for that bug as a single revision pass.
      The feedback may direct you to change the comment text, adjust
      severity/priority/resolution, add or remove blocks, ni_targets, cc,
      or keywords, or reassign the component — apply whatever each
      feedback warrants. Preserve fields you weren't told to change.
   c. Write the updated JSON back to the same path.

4. For each queued apply (distinct bug_ids only), run:

       bugzilla-cli apply <bug_id>

   The CLI will print the post preview and prompt the user with [y/N].
   **SAFETY GATE — read carefully:**
   - Do NOT auto-confirm. Do NOT pass `--yes`, `-y`, or any flag that
     bypasses the prompt.
   - Wait for the user to type `y` or `N` at the terminal. This is the
     production-write gate — the user must approve each bug individually.
   - If the user answers N (or the apply errors), stop and ask the user
     how to proceed. Do NOT charge ahead to the next apply.
   - If the user answers y, the CLI posts to Bugzilla and deletes the
     pending JSON. Move on to the next apply.

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


def remove_refine(triage_dir: Path, *, bug_id: int, ts: str) -> bool:
    """Remove the one refine entry matching (bug_id, ts). Returns True if
    a matching entry was removed; False if the file or entry was missing.

    The JSONL is rewritten in place, preserving all non-matching lines
    verbatim (including malformed lines, so we don't silently destroy
    anything we don't understand).

    Concurrency: this is a non-atomic read-then-write. Callers must assume
    a single writer — the dashboard is local single-user, so concurrent
    `append_refine` from another process is not a real risk here.
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
                    and obj.get("action") == "refine"
                    and int(obj.get("bug_id") or 0) == int(bug_id)
                    and obj.get("ts") == ts
                ):
                    removed = True
                    continue
        kept.append(line)

    if not removed:
        return False

    # Rewrite. Preserve trailing newline behaviour of append_refine.
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
