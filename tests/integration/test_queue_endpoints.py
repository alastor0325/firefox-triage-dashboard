"""Integration tests for the queue HTTP endpoints (count + prepare)."""

from __future__ import annotations

import json
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
    body = response.json()
    assert body["count"] == 0
    assert body["prompt"] is None
    assert body["feedbackPath"] is None


def test_prepare_writes_md_and_returns_prompt(triage_dir: Path) -> None:
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
    assert body["prompt"]
    assert "claude" in body["prompt"].lower()
    assert body["feedbackPath"].endswith("CLAUDE_QUEUE_PROMPT.md")

    md = (triage_dir / "CLAUDE_QUEUE_PROMPT.md").read_text()
    assert "Bug 42" in md
    assert "shorten it" in md
    assert "the original draft" in md


def test_prepare_overwrites_md_on_repeat(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, comment="orig")
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"first",'
        '"ts":"2026-05-29T00:00:00+00:00"}\n'
    )
    client.post("/queue/prepare")

    # Append more and re-prepare — file is overwritten, not appended.
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"first",'
        '"ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"refine","bug_id":1,"feedback":"second",'
        '"ts":"2026-05-29T00:01:00+00:00"}\n'
    )
    client.post("/queue/prepare")

    md = (triage_dir / "CLAUDE_QUEUE_PROMPT.md").read_text()
    assert "first" in md and "second" in md
    # Single header, not two stacked drains.
    assert md.count("# Claude Queue") == 1
