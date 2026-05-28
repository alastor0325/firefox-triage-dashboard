"""Action queue: dashboard writes, /process-queue skill reads.

The queue is a JSONL file at `<triage_dir>/claude-queue.jsonl`. Each line is
an action that a Claude session, run separately by the user, drains by
invoking the `/process-queue` skill. Today the only action is `refine`,
which asks Claude to re-draft a pending triage comment given the user's
feedback. Future actions (e.g. `bug-start`, `re-triage`) will share the
same file shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_FILE = "claude-queue.jsonl"


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
