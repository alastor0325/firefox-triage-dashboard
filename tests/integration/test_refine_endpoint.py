"""Integration tests for POST /draft/{id}/refine."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


def _queue_entries(triage_dir: Path) -> list[dict]:
    path = triage_dir / "claude-queue.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_refine_writes_a_queue_entry(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/refine",
        data={"feedback": "shorten the analysis"},
    )
    assert response.status_code == 200
    entries = _queue_entries(triage_dir)
    assert len(entries) == 1
    assert entries[0]["action"] == "refine"
    assert entries[0]["bug_id"] == 2039425
    assert entries[0]["feedback"] == "shorten the analysis"


def test_refine_returns_200_on_success(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/refine", data={"feedback": "x"}
    )
    assert response.status_code == 200


def test_refine_rejects_empty_feedback(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/refine", data={"feedback": ""}
    )
    assert response.status_code == 400
    assert _queue_entries(triage_dir) == []


def test_refine_rejects_whitespace_only_feedback(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/refine", data={"feedback": "   \n\t  "}
    )
    assert response.status_code == 400
    assert _queue_entries(triage_dir) == []


def test_refine_404_on_unknown_bug(triage_dir: Path) -> None:
    """Cannot refine a draft that doesn't exist in pending/."""
    response = client.post(
        "/draft/9999999/refine", data={"feedback": "x"}
    )
    assert response.status_code == 404
    assert _queue_entries(triage_dir) == []


def test_refine_appends_multiple_entries(triage_dir: Path) -> None:
    """Repeated feedback on the same bug appends, doesn't overwrite."""
    write_draft(triage_dir, 1)
    client.post("/draft/1/refine", data={"feedback": "a"})
    client.post("/draft/1/refine", data={"feedback": "b"})
    entries = _queue_entries(triage_dir)
    assert [e["feedback"] for e in entries] == ["a", "b"]
