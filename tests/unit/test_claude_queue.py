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


# ─── pending_feedback_for — per-bug queue inspection ────────────────

def test_pending_feedback_for_returns_empty_when_no_queue(
    triage_dir: Path,
) -> None:
    """No queue file → no pending feedback for any bug."""
    assert claude_queue.pending_feedback_for(triage_dir, 1) == []


def test_pending_feedback_for_returns_entries_for_that_bug_only(
    triage_dir: Path,
) -> None:
    """Filter by bug_id; preserve chronological order (file order)."""
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="a",
        now=datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc),
    )
    claude_queue.append_refine(
        triage_dir, bug_id=2, feedback="b",
        now=datetime(2026, 5, 29, 0, 1, tzinfo=timezone.utc),
    )
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="c",
        now=datetime(2026, 5, 29, 0, 2, tzinfo=timezone.utc),
    )
    items = claude_queue.pending_feedback_for(triage_dir, 1)
    assert [(i["feedback"], i["ts"]) for i in items] == [
        ("a", "2026-05-29T00:00:00+00:00"),
        ("c", "2026-05-29T00:02:00+00:00"),
    ]
    # bug_id is included in each item so callers can use it as a key.
    assert all(i["bug_id"] == 1 for i in items)


def test_pending_feedback_for_ignores_non_refine_actions(
    triage_dir: Path,
) -> None:
    """Future action types or other shapes → filtered out."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"future","bug_id":1,"ts":"2026-05-29T00:01:00+00:00"}\n'
        '{ malformed line\n'
    )
    items = claude_queue.pending_feedback_for(triage_dir, 1)
    assert len(items) == 1
    assert items[0]["feedback"] == "a"


# ─── remove_refine — rewrites the JSONL without one entry ───────────

def test_remove_refine_removes_the_matching_entry(triage_dir: Path) -> None:
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="keep me",
        now=datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc),
    )
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="remove me",
        now=datetime(2026, 5, 29, 0, 1, tzinfo=timezone.utc),
    )

    removed = claude_queue.remove_refine(
        triage_dir, bug_id=1, ts="2026-05-29T00:01:00+00:00",
    )
    assert removed is True

    remaining = claude_queue.pending_feedback_for(triage_dir, 1)
    assert [r["feedback"] for r in remaining] == ["keep me"]


def test_remove_refine_returns_false_when_not_found(triage_dir: Path) -> None:
    """Mismatched bug_id or ts → no-op, returns False."""
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="a",
        now=datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc),
    )

    assert claude_queue.remove_refine(
        triage_dir, bug_id=999, ts="2026-05-29T00:00:00+00:00",
    ) is False
    assert claude_queue.remove_refine(
        triage_dir, bug_id=1, ts="2099-01-01T00:00:00+00:00",
    ) is False
    # The original entry is still there.
    assert len(claude_queue.pending_feedback_for(triage_dir, 1)) == 1


def test_remove_refine_preserves_other_bugs(triage_dir: Path) -> None:
    claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="x",
        now=datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc),
    )
    claude_queue.append_refine(
        triage_dir, bug_id=2, feedback="y",
        now=datetime(2026, 5, 29, 0, 1, tzinfo=timezone.utc),
    )
    claude_queue.remove_refine(
        triage_dir, bug_id=1, ts="2026-05-29T00:00:00+00:00",
    )
    assert claude_queue.pending_feedback_for(triage_dir, 1) == []
    assert [i["feedback"] for i in claude_queue.pending_feedback_for(triage_dir, 2)] == ["y"]


def test_remove_refine_returns_false_when_queue_missing(
    triage_dir: Path,
) -> None:
    """No queue file at all → False, no crash."""
    assert claude_queue.remove_refine(
        triage_dir, bug_id=1, ts="2026-05-29T00:00:00+00:00",
    ) is False


# ─── append_bug_start — Phase 5 ─────────────────────────────────────

def test_append_bug_start_writes_a_jsonl_line(triage_dir: Path) -> None:
    fixed_ts = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    entry = claude_queue.append_bug_start(
        triage_dir, bug_id=2039425, now=fixed_ts,
    )
    queue_path = triage_dir / "claude-queue.jsonl"
    assert queue_path.is_file()
    lines = _read_lines(queue_path)
    assert lines == [entry]
    assert entry == {
        "action": "bug-start",
        "bug_id": 2039425,
        "ts": "2026-05-29T12:00:00+00:00",
    }


def test_append_bug_start_coexists_with_refines(triage_dir: Path) -> None:
    """The queue holds both action types interleaved; both are preserved."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    claude_queue.append_bug_start(triage_dir, bug_id=1)
    claude_queue.append_refine(triage_dir, bug_id=2, feedback="y")
    lines = _read_lines(triage_dir / "claude-queue.jsonl")
    assert [l["action"] for l in lines] == ["refine", "bug-start", "refine"]


def test_append_bug_start_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    claude_queue.append_bug_start(target, bug_id=42)
    assert (target / "claude-queue.jsonl").is_file()


def test_append_bug_start_default_ts_is_now(triage_dir: Path) -> None:
    entry = claude_queue.append_bug_start(triage_dir, bug_id=1)
    parsed = datetime.fromisoformat(entry["ts"])
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5


# pending_feedback_for is for the per-card list and stays refine-only;
# bug-start actions live in the queue but aren't surfaced as "feedback".
def test_pending_feedback_for_filters_out_bug_start(triage_dir: Path) -> None:
    claude_queue.append_bug_start(triage_dir, bug_id=1)
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    items = claude_queue.pending_feedback_for(triage_dir, 1)
    assert [i["feedback"] for i in items] == ["x"]


# prepare_queue_drain counts ALL drainable actions (both kinds) — that's
# what the topbar badge represents to the user.
def test_prepare_queue_drain_counts_bug_start_too(triage_dir: Path) -> None:
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    claude_queue.append_bug_start(triage_dir, bug_id=2)
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 2
    assert result["bugs_affected"] == 2


def test_prepare_queue_drain_with_only_bug_start_actions(
    triage_dir: Path,
) -> None:
    """No refines but at least one bug-start → still a drainable queue."""
    claude_queue.append_bug_start(triage_dir, bug_id=42)
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert result["bugs_affected"] == 1
    assert result["prompt"] is not None


def test_drain_prompt_describes_bug_start_action(triage_dir: Path) -> None:
    """The prompt must tell Claude how to handle bug-start entries —
    invoking the /bug-start skill for each."""
    claude_queue.append_bug_start(triage_dir, bug_id=1)
    prompt = claude_queue.prepare_queue_drain(triage_dir)["prompt"]
    assert "bug-start" in prompt
    assert "/bug-start" in prompt


def test_prepare_queue_drain_bugs_affected_dedups_across_action_types(
    triage_dir: Path,
) -> None:
    """One bug with both a refine and a bug-start → bugs_affected is 1,
    not 2. count still reflects both entries (2)."""
    claude_queue.append_refine(triage_dir, bug_id=42, feedback="x")
    claude_queue.append_bug_start(triage_dir, bug_id=42)
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 2
    assert result["bugs_affected"] == 1
