"""Integration tests for POST /draft/{id}/apply and /draft/{id}/skip
(mock backend, dry-run by default)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── POST /draft/{id}/apply ─────────────────────────────────────────

def test_apply_returns_200(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425, severity="S3", priority="P3")
    response = client.post("/draft/2039425/apply")
    assert response.status_code == 200


def test_apply_404_for_unknown_bug(triage_dir: Path) -> None:
    response = client.post("/draft/9999999/apply")
    assert response.status_code == 404


def test_apply_json_response_for_curl(triage_dir: Path) -> None:
    """Non-htmx callers get a structured JSON BackendResult."""
    write_draft(
        triage_dir, 2039425,
        severity="S3", priority="P3",
        blocks_add=[1746557],
        ni_targets=["alwu@mozilla.com"],
    )
    body = client.post("/draft/2039425/apply").json()
    assert body["action"] == "apply"
    assert body["bug_id"] == 2039425
    assert body["ok"] is True
    kinds = [a["kind"] for a in body["actions"]]
    assert "severity" in kinds
    assert "priority" in kinds
    assert "blocks" in kinds
    assert "ni" in kinds
    assert "watch-add" in kinds


def test_apply_html_response_for_htmx(triage_dir: Path) -> None:
    """htmx clients get an HTML fragment they can swap into the page."""
    write_draft(triage_dir, 2039425, severity="S3", priority="P3")
    response = client.post(
        "/draft/2039425/apply", headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # Should not be a full document.
    assert "<!doctype" not in body.lower()
    # The plan content should be visible.
    assert "S3" in body
    assert "P3" in body
    # And the dry-run signal.
    assert "dry" in body.lower() or "DRY" in body


def test_apply_does_not_delete_pending(triage_dir: Path) -> None:
    """Mock backend MUST NOT delete the pending file — that's a real-mode
    side effect."""
    write_draft(triage_dir, 2039425, severity="S3")
    pending = triage_dir / "pending" / "bug-2039425.json"
    assert pending.is_file()
    client.post("/draft/2039425/apply")
    assert pending.is_file()


def test_apply_does_not_touch_triage_log(triage_dir: Path) -> None:
    """Mock backend MUST NOT append to triage-log.json."""
    write_draft(triage_dir, 2039425, severity="S3")
    log_path = triage_dir / "triage-log.json"
    log_path.write_text(json.dumps([]))
    before = log_path.read_text()
    client.post("/draft/2039425/apply")
    assert log_path.read_text() == before


def test_apply_live_mode_returns_501_until_real_impl(
    triage_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-suspenders: flipping LIVE=1 selects the real backend, but
    its apply() raises NotImplementedError → endpoint surfaces a clean 501.
    The endpoint MUST NOT silently downgrade to mock and MUST NOT write
    anything to disk."""
    monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", "1")
    write_draft(triage_dir, 2039425, severity="S3")
    response = client.post("/draft/2039425/apply")
    assert response.status_code == 501
    assert "approval" in response.json()["detail"].lower()
    # Pending file MUST still exist — no real write happened.
    assert (triage_dir / "pending" / "bug-2039425.json").is_file()


# ─── POST /draft/{id}/skip ──────────────────────────────────────────

def test_skip_returns_200(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    response = client.post("/draft/2039425/skip")
    assert response.status_code == 200


def test_skip_404_for_unknown_bug(triage_dir: Path) -> None:
    response = client.post("/draft/9999999/skip")
    assert response.status_code == 404


def test_skip_json_response(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    body = client.post("/draft/2039425/skip").json()
    assert body["action"] == "skip"
    assert body["bug_id"] == 2039425
    kinds = [a["kind"] for a in body["actions"]]
    assert "delete-pending" in kinds
    assert "log-skipped" in kinds


def test_skip_does_not_delete_pending(triage_dir: Path) -> None:
    write_draft(triage_dir, 2039425)
    pending = triage_dir / "pending" / "bug-2039425.json"
    client.post("/draft/2039425/skip")
    assert pending.is_file()


# ─── §1b Apply queues a bug-start action (Phase 5) ──────────────────

def _queue_actions(triage_dir: Path) -> list[dict]:
    path = triage_dir / "claude-queue.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_b1_apply_queues_bug_start(triage_dir: Path) -> None:
    """Applying a §1b draft (severity+priority set) queues a /bug-start."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    response = client.post("/draft/555/apply")
    assert response.status_code == 200
    entries = _queue_actions(triage_dir)
    assert len(entries) == 1
    assert entries[0]["action"] == "bug-start"
    assert entries[0]["bug_id"] == 555


def test_a1_apply_does_not_queue_bug_start(triage_dir: Path) -> None:
    """§1a drafts (needinfo only) → no /bug-start handoff."""
    write_draft(triage_dir, 555, ni_targets=["x@y"])
    client.post("/draft/555/apply")
    assert _queue_actions(triage_dir) == []


def test_c1_apply_does_not_queue_bug_start(triage_dir: Path) -> None:
    """§1c drafts (resolve / reassign) → no /bug-start handoff."""
    write_draft(triage_dir, 555, resolution="INCOMPLETE")
    client.post("/draft/555/apply")
    assert _queue_actions(triage_dir) == []


def test_b1_skip_does_not_queue_bug_start(triage_dir: Path) -> None:
    """Skipping any draft (even §1b) does NOT queue /bug-start."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    client.post("/draft/555/skip")
    assert _queue_actions(triage_dir) == []


def test_b1_apply_queue_count_visible_in_topbar(triage_dir: Path) -> None:
    """After §1b apply, the topbar count is 1 (bug-start counts)."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    client.post("/draft/555/apply")
    # Use the count endpoint.
    assert client.get("/queue/count").json() == {"count": 1}


def test_b1_apply_failure_does_not_queue_bug_start(
    triage_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the backend returns ok=False, no bug-start gets queued.

    We achieve this by pointing at LIVE mode where the real backend
    raises NotImplementedError → 501. The queue must stay empty.
    """
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", "1")
    response = client.post("/draft/555/apply")
    assert response.status_code == 501
    assert _queue_actions(triage_dir) == []
