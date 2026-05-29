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


# ─── format_queue_prompt (pure) ─────────────────────────────────────

def test_format_queue_prompt_empty_entries() -> None:
    """No entries → empty string (caller decides what to do)."""
    assert claude_queue.format_queue_prompt([], {}) == ""


def test_format_queue_prompt_single_refine() -> None:
    entries = [{
        "action": "refine", "bug_id": 1,
        "feedback": "shorten the analysis",
        "ts": "2026-05-29T14:30:00+00:00",
    }]
    drafts = {1: {
        "bug_id": 1, "title": "WebCodecs assert",
        "comment": "Thanks for the report.\nLong analysis...",
    }}
    out = claude_queue.format_queue_prompt(entries, drafts)
    assert "Drain procedure" in out or "Procedure" in out
    assert "Bug 1" in out
    assert "shorten the analysis" in out
    # Includes a quoted excerpt of the current draft.
    assert "Thanks for the report" in out
    # Title appears so the reader can scan.
    assert "WebCodecs assert" in out


def test_format_queue_prompt_groups_feedback_by_bug() -> None:
    """Two refine actions on the same bug → ONE section listing both."""
    entries = [
        {"action": "refine", "bug_id": 7, "feedback": "shorter please", "ts": "2026-05-29T01:00:00+00:00"},
        {"action": "refine", "bug_id": 7, "feedback": "no bisect mention", "ts": "2026-05-29T02:00:00+00:00"},
    ]
    drafts = {7: {"bug_id": 7, "title": "x", "comment": "..."}}
    out = claude_queue.format_queue_prompt(entries, drafts)
    # Both feedbacks present.
    assert "shorter please" in out
    assert "no bisect mention" in out
    # Only ONE "### Bug 7" header.
    assert out.count("### Bug 7") == 1


def test_format_queue_prompt_handles_missing_pending() -> None:
    """If a queued bug's pending JSON is gone (e.g. already applied), the
    section still appears, with a clear note instead of a draft excerpt."""
    entries = [{
        "action": "refine", "bug_id": 999, "feedback": "x",
        "ts": "2026-05-29T00:00:00+00:00",
    }]
    out = claude_queue.format_queue_prompt(entries, {})
    assert "Bug 999" in out
    assert "missing" in out.lower() or "no pending" in out.lower()


def test_format_queue_prompt_multiple_bugs_each_get_a_section() -> None:
    entries = [
        {"action": "refine", "bug_id": 1, "feedback": "a", "ts": "2026-05-29T00:00:00+00:00"},
        {"action": "refine", "bug_id": 2, "feedback": "b", "ts": "2026-05-29T00:01:00+00:00"},
        {"action": "refine", "bug_id": 3, "feedback": "c", "ts": "2026-05-29T00:02:00+00:00"},
    ]
    drafts = {i: {"bug_id": i, "title": f"bug-{i}", "comment": "..."} for i in (1, 2, 3)}
    out = claude_queue.format_queue_prompt(entries, drafts)
    assert "### Bug 1" in out
    assert "### Bug 2" in out
    assert "### Bug 3" in out


def test_format_queue_prompt_includes_truncate_instruction() -> None:
    """The prompt MUST tell Claude to clear the queue file after processing."""
    entries = [{"action": "refine", "bug_id": 1, "feedback": "x",
                "ts": "2026-05-29T00:00:00+00:00"}]
    out = claude_queue.format_queue_prompt(entries, {1: {"comment": "..."}})
    lo = out.lower()
    assert "truncate" in lo or "empty" in lo or "remove" in lo
    assert "claude-queue.jsonl" in out


def test_format_queue_prompt_ignores_non_refine_actions() -> None:
    """Future action types we don't know about yet → ignored gracefully."""
    entries = [
        {"action": "refine", "bug_id": 1, "feedback": "a", "ts": "2026-05-29T00:00:00+00:00"},
        {"action": "some-future-thing", "bug_id": 2, "data": "...", "ts": "2026-05-29T00:01:00+00:00"},
    ]
    out = claude_queue.format_queue_prompt(entries, {1: {"comment": "..."}})
    assert "Bug 1" in out
    assert "Bug 2" not in out


# ─── prepare_queue_drain (I/O wrapper) ──────────────────────────────

def test_prepare_queue_drain_empty(triage_dir: Path) -> None:
    """No queue file or empty queue → count: 0, no MD written."""
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 0
    assert result["prompt"] is None
    assert not (triage_dir / "CLAUDE_QUEUE_PROMPT.md").exists()


def test_prepare_queue_drain_writes_md_and_returns_prompt(
    triage_dir: Path,
) -> None:
    # Set up: one queued refine + the corresponding pending JSON.
    claude_queue.append_refine(triage_dir, bug_id=42, feedback="shorten it")
    (triage_dir / "pending").mkdir(exist_ok=True)
    (triage_dir / "pending" / "bug-42.json").write_text(json.dumps({
        "bug_id": 42, "title": "test", "comment": "the original draft",
    }))

    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    md_path = triage_dir / "CLAUDE_QUEUE_PROMPT.md"
    assert md_path.is_file()
    body = md_path.read_text()
    assert "Bug 42" in body
    assert "shorten it" in body
    assert "the original draft" in body
    # Prompt is a short pointer at the file, suitable for clipboard.
    assert str(md_path) in result["prompt"]
    assert len(result["prompt"]) < 300


def test_prepare_queue_drain_skips_unparseable_lines(triage_dir: Path) -> None:
    """A malformed line in the queue file shouldn't crash the prepare step."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"good","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{ not valid json\n'
        '\n'
    )
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1


def test_prepare_queue_drain_overwrites_md(triage_dir: Path) -> None:
    """Re-running prepare should rewrite the same path, not append."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="first")
    claude_queue.prepare_queue_drain(triage_dir)
    first = (triage_dir / "CLAUDE_QUEUE_PROMPT.md").read_text()

    # Add more, prepare again.
    claude_queue.append_refine(triage_dir, bug_id=2, feedback="second")
    claude_queue.prepare_queue_drain(triage_dir)
    second = (triage_dir / "CLAUDE_QUEUE_PROMPT.md").read_text()

    assert "first" in second and "second" in second
    # And the old file isn't appended onto — second is one prompt, not two.
    assert second.count("# Claude Queue") == 1
