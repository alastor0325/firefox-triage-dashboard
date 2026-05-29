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
from typing import Any, Iterable

QUEUE_FILE = "claude-queue.jsonl"
PROMPT_FILE = "CLAUDE_QUEUE_PROMPT.md"

_DRAFT_EXCERPT_CHARS = 350


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


def _excerpt(text: str, n: int = _DRAFT_EXCERPT_CHARS) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def format_queue_prompt(
    entries: Iterable[dict],
    drafts_by_bug_id: dict[int, dict],
    *,
    queue_path: str = "~/firefox-triage/claude-queue.jsonl",
) -> str:
    """Build the Markdown drain prompt for the queue.

    `entries` are the raw lines from `claude-queue.jsonl`. `drafts_by_bug_id`
    maps a bug id to its current pending JSON (a dict like the one in
    `pending/bug-<id>.json`) so each section can quote the current draft.
    Returns "" if there are no refine entries to process.
    """
    refines = [e for e in entries if e.get("action") == "refine" and e.get("bug_id")]
    if not refines:
        return ""

    # Group by bug_id, preserving the chronological order of first appearance.
    grouped: dict[int, list[dict]] = {}
    for e in refines:
        grouped.setdefault(int(e["bug_id"]), []).append(e)

    out: list[str] = []
    out.append("# Claude Queue — Drain Request\n")
    out.append("## Procedure\n")
    out.append(
        "For each bug below:\n\n"
        "1. Apply **all** the listed feedback together as one revision pass "
        "to `~/firefox-triage/pending/bug-<id>.json`. The feedback may direct "
        "you to change the `comment` text, severity/priority, blocks, "
        "ni_targets, or other fields — apply whatever the feedback warrants.\n"
        "2. Write the updated pending JSON back.\n\n"
        "After all bugs are processed:\n\n"
        f"3. Truncate `{queue_path}` to empty (its content is the queue's "
        "source of truth; empty file = nothing to drain).\n"
        "4. Print a one-line summary per bug describing what changed.\n"
    )

    out.append("\n## Bugs to drain\n")
    for bug_id, items in grouped.items():
        title = ""
        excerpt = ""
        if bug_id in drafts_by_bug_id:
            draft = drafts_by_bug_id[bug_id]
            title = (draft.get("title") or "").strip()
            excerpt = _excerpt(draft.get("comment") or "")
        n = len(items)
        suffix = "feedback item" if n == 1 else "feedback items"
        out.append(f"### Bug {bug_id} — {n} {suffix}")
        if title:
            out.append(f"*{title}*\n")
        if excerpt:
            out.append("**Current draft (excerpt):**\n")
            for line in excerpt.splitlines():
                out.append(f"> {line}" if line else ">")
        else:
            out.append("**Current draft:** _missing — no pending JSON for this bug._\n")
        out.append("\n**Feedback to apply:**\n")
        for i, item in enumerate(items, start=1):
            ts = (item.get("ts") or "")[:19].replace("T", " ")
            fb = (item.get("feedback") or "").strip()
            out.append(f"{i}. *({ts})* {fb}")
        out.append("")  # blank line between bugs
        out.append("---\n")

    return "\n".join(out)


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


def prepare_queue_drain(triage_dir: Path) -> dict[str, Any]:
    """Read the queue, build the drain prompt, write the Markdown to disk.

    Returns `{count, prompt, feedbackPath}`. When the queue is empty,
    returns `{count: 0, prompt: None, feedbackPath: None}` and writes
    nothing.
    """
    queue_path = triage_dir / QUEUE_FILE
    if not queue_path.is_file():
        return {"count": 0, "prompt": None, "feedbackPath": None}

    entries = _read_jsonl(queue_path)
    refines = [e for e in entries if e.get("action") == "refine" and e.get("bug_id")]
    if not refines:
        return {"count": 0, "prompt": None, "feedbackPath": None}

    # Load current pending JSON for each bug we'll reference.
    drafts_by_id: dict[int, dict] = {}
    for entry in refines:
        bug_id = int(entry["bug_id"])
        if bug_id in drafts_by_id:
            continue
        pending = triage_dir / "pending" / f"bug-{bug_id}.json"
        if pending.is_file():
            try:
                drafts_by_id[bug_id] = json.loads(pending.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue

    md = format_queue_prompt(
        refines, drafts_by_id, queue_path=str(queue_path),
    )
    md_path = triage_dir / PROMPT_FILE
    md_path.write_text(md, encoding="utf-8")

    prompt = (
        f"Read {md_path} and drain the Claude queue described there."
    )
    return {
        "count": len(refines),
        "prompt": prompt,
        "feedbackPath": str(md_path),
    }
