"""Unit tests for triage_dashboard.claude_queue — the action queue writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from triage_dashboard import claude_queue


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_append_refine_writes_a_jsonl_line(triage_dir: Path) -> None:
    fixed_ts = datetime(2026, 5, 28, 18, 42, 31, tzinfo=timezone.utc)
    entry = claude_queue.append_refine(
        triage_dir, bug_id=2039425, feedback="shorten the analysis", now=fixed_ts
    )

    queue_path = triage_dir / "claude-queue.jsonl"
    assert queue_path.is_file()
    lines = _read_lines(queue_path)
    assert lines == [entry]
    assert entry == {
        "action": "refine",
        "bug_id": 2039425,
        "feedback": "shorten the analysis",
        "ts": "2026-05-28T18:42:31+00:00",
    }


def test_append_refine_appends_not_overwrites(triage_dir: Path) -> None:
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="a")
    claude_queue.append_refine(triage_dir, bug_id=2, feedback="b")
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="c")
    lines = _read_lines(triage_dir / "claude-queue.jsonl")
    assert [(e["bug_id"], e["feedback"]) for e in lines] == [
        (1, "a"), (2, "b"), (1, "c"),
    ]


def test_append_refine_creates_parent_dir_if_missing(tmp_path: Path) -> None:
    """Triage dir doesn't exist yet — appending should still work."""
    target = tmp_path / "fresh"
    claude_queue.append_refine(target, bug_id=42, feedback="x")
    assert (target / "claude-queue.jsonl").is_file()


def test_append_refine_rejects_empty_feedback(triage_dir: Path) -> None:
    """Empty / whitespace-only feedback is rejected — no point queueing it."""
    with pytest.raises(ValueError):
        claude_queue.append_refine(triage_dir, bug_id=1, feedback="")
    with pytest.raises(ValueError):
        claude_queue.append_refine(triage_dir, bug_id=1, feedback="   \n  ")
    # Nothing should have been written to the queue.
    assert not (triage_dir / "claude-queue.jsonl").exists()


def test_append_refine_default_ts_is_now(
    triage_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `now` is omitted, the entry carries the current UTC time."""
    entry = claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    parsed = datetime.fromisoformat(entry["ts"])
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5
