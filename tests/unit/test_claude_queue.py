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


# ─── append_apply — Phase 5.5 ────────────────────────────────────────

def test_append_apply_writes_a_jsonl_line(triage_dir: Path) -> None:
    fixed_ts = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    entry = claude_queue.append_apply(
        triage_dir, bug_id=2039425, now=fixed_ts,
    )
    queue_path = triage_dir / "claude-queue.jsonl"
    assert queue_path.is_file()
    lines = _read_lines(queue_path)
    assert lines == [entry]
    assert entry == {
        "action": "apply",
        "bug_id": 2039425,
        "ts": "2026-05-30T12:00:00+00:00",
    }


def test_append_apply_coexists_with_other_actions(triage_dir: Path) -> None:
    """All three action kinds can interleave in the queue file."""
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    claude_queue.append_apply(triage_dir, bug_id=1)
    claude_queue.append_bug_start(triage_dir, bug_id=1)
    lines = _read_lines(triage_dir / "claude-queue.jsonl")
    assert [l["action"] for l in lines] == ["refine", "apply", "bug-start"]


def test_apply_is_in_drainable_actions(triage_dir: Path) -> None:
    """The badge / drain count must include apply."""
    assert "apply" in claude_queue.DRAINABLE_ACTIONS


def test_prepare_queue_drain_counts_apply_entries(triage_dir: Path) -> None:
    claude_queue.append_apply(triage_dir, bug_id=42)
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert result["bugs_affected"] == 1


def test_pending_feedback_for_filters_out_apply(triage_dir: Path) -> None:
    """Apply isn't 'feedback on the draft' — the per-card list must skip it."""
    claude_queue.append_apply(triage_dir, bug_id=1)
    claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    items = claude_queue.pending_feedback_for(triage_dir, 1)
    assert [i["feedback"] for i in items] == ["x"]


# ─── drain prompt — apply step + no-auto-confirm gate ───────────────

def test_drain_prompt_describes_apply_action(triage_dir: Path) -> None:
    """The prompt tells Claude to run bugzilla-cli apply for each
    queued apply entry."""
    claude_queue.append_apply(triage_dir, bug_id=1)
    prompt = claude_queue.prepare_queue_drain(triage_dir)["prompt"]
    assert "apply" in prompt.lower()
    assert "bugzilla-cli apply" in prompt


def test_drain_prompt_has_explicit_no_auto_confirm_gate(
    triage_dir: Path,
) -> None:
    """The prompt MUST forbid auto-confirming the [y/N]. This is the
    production-write safety gate."""
    claude_queue.append_apply(triage_dir, bug_id=1)
    prompt = claude_queue.prepare_queue_drain(triage_dir)["prompt"]
    lo = prompt.lower()
    # Must mention [y/N] / y or N so the model knows the prompt format.
    assert "[y/n]" in lo or "y/n" in lo
    # Must forbid auto-confirmation flags AND stdin-piping bypasses.
    # The realistic ways a model would skip the gate are (a) `--yes`
    # or similar flag, and (b) piping `y` into stdin.
    assert "--yes" in lo
    assert "yes | bugzilla-cli" in lo or "echo y" in lo
    # And it must positively assert the user runs the command in the
    # foreground so they can see the preview.
    assert "foreground" in lo or "wait for the user" in lo


def test_drain_prompt_specifies_order_refines_applies_bug_starts(
    triage_dir: Path,
) -> None:
    """Drain order matters: revise before posting; post before starting
    investigation. Anchor on specific step phrases rather than action
    names (which appear together in step 2's partition list)."""
    claude_queue.append_apply(triage_dir, bug_id=1)
    body = (
        claude_queue.prepare_queue_drain(triage_dir)["prompt"]
        .split("Procedure:", 1)[1]
        .lower()
    )
    refine_step = body.find("apply refines first")
    apply_step = body.find("bugzilla-cli apply")
    bug_start_step = body.find("invoke the `bug-start` skill")
    assert 0 <= refine_step < apply_step < bug_start_step


def test_drain_prompt_only_apply_action_still_works(
    triage_dir: Path,
) -> None:
    """A queue with just an apply (no refines or bug-starts) still
    produces a usable prompt."""
    claude_queue.append_apply(triage_dir, bug_id=42)
    result = claude_queue.prepare_queue_drain(triage_dir)
    assert result["count"] == 1
    assert "bugzilla-cli apply" in result["prompt"]


def test_drain_prompt_says_to_leave_queue_intact_on_decline(
    triage_dir: Path,
) -> None:
    """If the user declines an apply ([y/N] → N) or it errors, the drain
    must NOT truncate the queue — the user (or a future drain) needs
    that state to recover. Lock the contract phrase into the prompt."""
    claude_queue.append_apply(triage_dir, bug_id=1)
    prompt = claude_queue.prepare_queue_drain(triage_dir)["prompt"].lower()
    assert "leave the queue intact" in prompt


# ─── remove_entry — generic queue remove (queue inspector) ──────────

def test_remove_entry_removes_apply(triage_dir: Path) -> None:
    entry = claude_queue.append_apply(
        triage_dir, bug_id=1,
        now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
    )
    removed = claude_queue.remove_entry(
        triage_dir, action="apply", bug_id=1, ts=entry["ts"],
    )
    assert removed is True
    assert _read_lines(triage_dir / "claude-queue.jsonl") == []


def test_remove_entry_removes_bug_start(triage_dir: Path) -> None:
    entry = claude_queue.append_bug_start(triage_dir, bug_id=1)
    removed = claude_queue.remove_entry(
        triage_dir, action="bug-start", bug_id=1, ts=entry["ts"],
    )
    assert removed is True
    assert _read_lines(triage_dir / "claude-queue.jsonl") == []


def test_remove_entry_removes_refine(triage_dir: Path) -> None:
    """remove_entry covers refine too — it's the unified API."""
    entry = claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="x",
        now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
    )
    removed = claude_queue.remove_entry(
        triage_dir, action="refine", bug_id=1, ts=entry["ts"],
    )
    assert removed is True


def test_remove_entry_returns_false_when_action_mismatch(
    triage_dir: Path,
) -> None:
    """Asking to remove an apply when only a refine exists at that
    (bug_id, ts) returns False — the action discriminator matters."""
    entry = claude_queue.append_refine(
        triage_dir, bug_id=1, feedback="x",
    )
    assert claude_queue.remove_entry(
        triage_dir, action="apply", bug_id=1, ts=entry["ts"],
    ) is False
    # Refine entry still intact.
    assert len(_read_lines(triage_dir / "claude-queue.jsonl")) == 1


def test_remove_refine_wrapper_still_works(triage_dir: Path) -> None:
    """The old remove_refine API stays — Phase 3.5 callers shouldn't break."""
    entry = claude_queue.append_refine(triage_dir, bug_id=1, feedback="x")
    assert claude_queue.remove_refine(
        triage_dir, bug_id=1, ts=entry["ts"],
    ) is True
