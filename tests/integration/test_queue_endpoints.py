"""Integration tests for the queue HTTP endpoints (count + prepare)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── GET /queue/count ───────────────────────────────────────────────

def test_queue_count_zero_when_no_file(triage_dir: Path) -> None:
    assert client.get("/queue/count").json() == {"count": 0}


def test_queue_count_counts_refine_entries(triage_dir: Path) -> None:
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"refine","bug_id":2,"feedback":"b","ts":"2026-05-29T00:01:00+00:00"}\n'
    )
    assert client.get("/queue/count").json() == {"count": 2}


def test_queue_count_ignores_unparseable_lines(triage_dir: Path) -> None:
    """Bad lines should be skipped, not crash the endpoint."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"x","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{ malformed\n'
        '\n'
    )
    assert client.get("/queue/count").json() == {"count": 1}


# ─── POST /queue/prepare ────────────────────────────────────────────

def test_prepare_empty_queue_returns_zero(triage_dir: Path) -> None:
    response = client.post("/queue/prepare")
    assert response.status_code == 200
    assert response.json() == {
        "count": 0, "prompt": None, "bugs_affected": 0,
    }


def test_prepare_returns_prompt_and_writes_no_md(triage_dir: Path) -> None:
    """Prompt is returned inline; nothing is written to disk by /prepare.
    The on-disk MD that the previous design wrote is gone for good."""
    write_draft(triage_dir, 42, comment="the original draft")
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":42,"feedback":"shorten it",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
    )

    response = client.post("/queue/prepare")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["bugs_affected"] == 1
    assert body["prompt"]
    # The prompt names the queue file (so Claude can Read it).
    assert "claude-queue.jsonl" in body["prompt"]
    # No on-disk MD: the previous design wrote ~/firefox-triage/CLAUDE_QUEUE_PROMPT.md;
    # this design never does.
    assert not (triage_dir / "CLAUDE_QUEUE_PROMPT.md").exists()


def test_prepare_response_shape_uses_bugs_affected_key(
    triage_dir: Path,
) -> None:
    """The pointer-style `feedbackPath` is gone; the new shape uses
    `bugs_affected` (count of distinct bug_ids in the queue)."""
    write_draft(triage_dir, 1)
    write_draft(triage_dir, 2)
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"refine","bug_id":1,"feedback":"b","ts":"2026-05-29T00:01:00+00:00"}\n'
        '{"action":"refine","bug_id":2,"feedback":"c","ts":"2026-05-29T00:02:00+00:00"}\n'
    )
    body = client.post("/queue/prepare").json()
    assert set(body.keys()) == {"count", "prompt", "bugs_affected"}
    assert body["count"] == 3
    assert body["bugs_affected"] == 2
    assert "feedbackPath" not in body
