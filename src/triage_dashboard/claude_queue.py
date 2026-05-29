"""Action queue: dashboard writes, a separate Claude session drains.

The queue is a JSONL file at `<triage_dir>/claude-queue.jsonl`. Each line is
an action that Claude drains by reading the file directly. Today the only
action is `refine`, which asks Claude to re-draft a pending triage comment
given the user's feedback.

`prepare_queue_drain` builds the short clipboard prompt the dashboard hands
the user when they click "Process queue". The prompt tells Claude where to
find the JSONL and what to do — Claude reads the JSONL itself, so the
queue contents are NOT embedded in the prompt. This keeps the clipboard
payload small and avoids any second on-disk artifact.
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
    Each line is one feedback record:
    {{"action":"refine","bug_id":<int>,"feedback":<string>,"ts":<iso8601>}}
- {pending_dir}/bug-<id>.json
    The current triage draft for that bug. Fields include `comment`,
    `severity`, `priority`, `resolution`, `blocks_add`, `ni_targets`,
    `keywords_add`, `cc_add`, `component`, `product`, and a free-form
    `ai_reasoning` block.

Procedure:

1. Read {queue_path}. If it is empty, stop.

2. Group entries by bug_id, preserving order within each bug.

3. For each bug, in ascending bug_id order:
   a. Read {pending_dir}/bug-<id>.json.
   b. Apply all feedback entries for that bug as a single revision pass.
      The feedback may direct you to change the comment text, adjust
      severity/priority/resolution, add or remove blocks, ni_targets, cc,
      or keywords, or reassign the component — apply whatever each
      feedback warrants. Preserve fields you weren't told to change.
   c. Write the updated JSON back to the same path.

4. After all bugs are processed, truncate {queue_path} to empty
   (write a zero-byte file).

5. Print a one-line summary per bug describing what changed, e.g.
     2039425: shortened analysis, removed bisect mention
     2042320: added request for media log

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


def _refines(entries: list[dict]) -> list[dict]:
    return [
        e for e in entries
        if e.get("action") == "refine" and e.get("bug_id")
    ]


_EMPTY = {"count": 0, "prompt": None, "bugs_affected": 0}


def prepare_queue_drain(triage_dir: Path) -> dict[str, Any]:
    """Build the short clipboard prompt for draining the queue.

    Returns `{count, prompt, bugs_affected}`. When the queue is missing
    or has no refine entries, returns the empty shape and writes nothing.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return dict(_EMPTY)

    refines = _refines(_read_jsonl(queue_path))
    if not refines:
        return dict(_EMPTY)

    prompt = DRAIN_PROMPT_TEMPLATE.format(
        queue_path=str(queue_path),
        pending_dir=str(triage_dir / "pending"),
    )
    return {
        "count": len(refines),
        "prompt": prompt,
        "bugs_affected": len({int(e["bug_id"]) for e in refines}),
    }
