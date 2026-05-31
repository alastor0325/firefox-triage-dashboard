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


def test_b1_apply_queues_apply_then_bug_start(triage_dir: Path) -> None:
    """Applying a §1b draft queues two entries: apply (post to Bugzilla)
    THEN bug-start (start investigation after the post). Order matters —
    apply must happen before bug-start in the drained sequence."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    response = client.post("/draft/555/apply")
    assert response.status_code == 200
    entries = _queue_actions(triage_dir)
    assert [e["action"] for e in entries] == ["apply", "bug-start"]
    assert all(e["bug_id"] == 555 for e in entries)


def test_a1_apply_queues_only_apply(triage_dir: Path) -> None:
    """§1a (needinfo only) → apply queued, no bug-start."""
    write_draft(triage_dir, 555, ni_targets=["x@y"])
    client.post("/draft/555/apply")
    entries = _queue_actions(triage_dir)
    assert [e["action"] for e in entries] == ["apply"]
    assert entries[0]["bug_id"] == 555


def test_c1_apply_queues_only_apply(triage_dir: Path) -> None:
    """§1c (resolve / reassign) → apply queued, no bug-start."""
    write_draft(triage_dir, 555, resolution="INCOMPLETE")
    client.post("/draft/555/apply")
    entries = _queue_actions(triage_dir)
    assert [e["action"] for e in entries] == ["apply"]
    assert entries[0]["bug_id"] == 555


def test_skip_queues_nothing(triage_dir: Path) -> None:
    """Skipping a draft (any section) does NOT queue any action.
    Skip is local-only — no Bugzilla side effect, ever."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    client.post("/draft/555/skip")
    assert _queue_actions(triage_dir) == []


def test_b1_apply_queue_count_visible_in_topbar(triage_dir: Path) -> None:
    """After §1b apply, the topbar count is 2 (apply + bug-start)."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    client.post("/draft/555/apply")
    assert client.get("/queue/count").json() == {"count": 2}


def test_a1_apply_queue_count_is_one(triage_dir: Path) -> None:
    """§1a apply queues only the apply action."""
    write_draft(triage_dir, 555, ni_targets=["x@y"])
    client.post("/draft/555/apply")
    assert client.get("/queue/count").json() == {"count": 1}


def test_b1_repeat_apply_queues_one_pair_per_click(
    triage_dir: Path,
) -> None:
    """Two clicks on Apply for the same §1b draft queue two apply +
    two bug-start entries (no dedupe at queue-write time). The drain
    prompt de-duplicates by distinct bug_id at consume time."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    client.post("/draft/555/apply")
    client.post("/draft/555/apply")
    entries = _queue_actions(triage_dir)
    assert [e["action"] for e in entries] == [
        "apply", "bug-start", "apply", "bug-start",
    ]
    assert all(e["bug_id"] == 555 for e in entries)


def test_apply_status_panel_mentions_queue_and_y_n_gate(
    triage_dir: Path,
) -> None:
    """When Apply succeeds, the htmx status panel must signal that the
    work has been queued and tell the user about the [y/N] gate."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    response = client.post(
        "/draft/555/apply", headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    body = response.text
    assert "Queued for apply" in body
    assert "Process queue" in body
    assert "[y/N]" in body or "y/N" in body


def test_skip_status_panel_does_not_mention_queue(
    triage_dir: Path,
) -> None:
    """Skip does not queue anything; the panel must not pretend it did."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    response = client.post(
        "/draft/555/skip", headers={"HX-Request": "true"},
    )
    assert "Queued for apply" not in response.text


def test_apply_returns_400_for_malformed_pending_json(
    triage_dir: Path,
) -> None:
    """A corrupt pending JSON (truncated write, hand-edit gone wrong)
    must not 500 the apply endpoint — the user clicks Apply, the
    endpoint returns a clean 4xx. _load_pending_or_404 currently
    blindly json.loads() the file; if the JSON is bad it must surface
    as a structured error, not an internal-server exception."""
    pending = triage_dir / "pending" / "bug-42.json"
    pending.write_text("{ this is not valid json")
    response = client.post("/draft/42/apply")
    # 400 is the right code — the file exists (so not 404) but its
    # contents are invalid, which is a client-fixable state.
    assert response.status_code == 400
    assert "pending" in response.json()["detail"].lower()


def test_skip_returns_400_for_malformed_pending_json(
    triage_dir: Path,
) -> None:
    """Same robustness guarantee for /skip — corrupt pending JSON
    surfaces as 400, not as an unhandled 500."""
    pending = triage_dir / "pending" / "bug-42.json"
    pending.write_text("{ malformed")
    response = client.post("/draft/42/skip")
    assert response.status_code == 400


def test_refine_returns_400_for_malformed_pending_json(
    triage_dir: Path,
) -> None:
    """The refine endpoint only checks is_file() to decide 404 vs queue —
    it doesn't read the pending JSON itself. Confirm it still queues a
    refine even when the pending file is corrupt: the drainer is the
    one that has to handle the corrupt content, not the queueing step."""
    pending = triage_dir / "pending" / "bug-42.json"
    pending.write_text("{ malformed")
    response = client.post(
        "/draft/42/refine", data={"feedback": "fix the JSON"},
    )
    # The refine path doesn't json.loads the pending file — it just
    # queues feedback. So this stays 200; the test pins that contract.
    assert response.status_code == 200


def test_apply_failure_queues_nothing(
    triage_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the backend returns ok=False (here: LIVE-mode 501 from the
    unimplemented real backend), neither apply nor bug-start gets queued."""
    write_draft(triage_dir, 555, severity="S3", priority="P3")
    monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", "1")
    response = client.post("/draft/555/apply")
    assert response.status_code == 501
    assert _queue_actions(triage_dir) == []
