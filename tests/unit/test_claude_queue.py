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


# ─── prepare_queue_drain — short prompt, no on-disk MD ─────────────

def test_prepare_queue_drain_empty(triage_dir: Path) -> None:
    """No queue file → count: 0, no prompt, nothing written."""
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result == {"count": 0, "prompt": None, "bugs_affected": 0}
    # No on-disk MD artifact may be created — this design is jsonl-only.
    assert not (triage_dir / "CLAUDE_QUEUE_PROMPT.md").exists()


def test_prepare_queue_drain_queue_file_empty(triage_dir: Path) -> None:
    """Zero-byte queue file → same shape as missing file."""
    (triage_dir / "claude-queue.jsonl").write_text("")
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result == {"count": 0, "prompt": None, "bugs_affected": 0}


def test_prepare_queue_drain_returns_prompt_count_bugs_affected(
    triage_dir: Path,
) -> None:
    """One entry → count 1, bugs_affected 1, prompt is a non-empty string."""
    claude_queue.append_refine(triage_dir, bug_id=42, feedback="shorten it")
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert result["bugs_affected"] == 1
    assert isinstance(result["prompt"], str) and result["prompt"].strip()


def test_prepare_queue_drain_does_not_write_md_file(triage_dir: Path) -> None:
    """The new design has no on-disk MD — only the JSONL."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    claude_queue.prepare_queue_drain(triage_dir)
    assert not (triage_dir / "CLAUDE_QUEUE_PROMPT.md").exists()


def test_prepare_queue_drain_prompt_names_the_queue_file(
    triage_dir: Path,
) -> None:
    """The prompt must tell Claude WHERE the JSONL lives so it can Read it."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert "claude-queue.jsonl" in result["prompt"]
    # And the actual triage dir path (so it works regardless of $HOME or env).
    assert str(triage_dir / "claude-queue.jsonl") in result["prompt"]


def test_prepare_queue_drain_prompt_names_pending_dir(
    triage_dir: Path,
) -> None:
    """The prompt tells Claude where the pending JSONs are."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert "pending" in result["prompt"]
    assert str(triage_dir / "pending") in result["prompt"]


def test_prepare_queue_drain_prompt_describes_full_procedure(
    triage_dir: Path,
) -> None:
    """The prompt must enumerate the steps Claude takes: read jsonl,
    apply per-bug, truncate jsonl, summarize."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    prompt = claude_queue.prepare_queue_drain(triage_dir)["prompt"]
    lo = prompt.lower()
    assert "read" in lo and "claude-queue.jsonl" in prompt
    assert "group" in lo or "for each bug" in lo
    assert "truncate" in lo or "empty" in lo
    assert "summary" in lo or "one-line" in lo


def test_prepare_queue_drain_bugs_affected_counts_distinct_bugs(
    triage_dir: Path,
) -> None:
    """3 entries spanning 2 bugs → bugs_affected=2, count=3."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="a")
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="b")
    claude_queue.append_refine(triage_dir, bug_id=2, feedback="c")
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 3
    assert result["bugs_affected"] == 2


def test_prepare_queue_drain_skips_unparseable_lines(
    triage_dir: Path,
) -> None:
    """A malformed line shouldn't crash the prepare step."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"good","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{ not valid json\n'
        '\n'
    )
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert result["bugs_affected"] == 1


def test_prepare_queue_drain_ignores_non_refine_actions(
    triage_dir: Path,
) -> None:
    """Future action types we don't know about → ignored."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"some-future-thing","bug_id":2,"data":"...","ts":"2026-05-29T00:01:00+00:00"}\n'
    )
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert result["bugs_affected"] == 1
