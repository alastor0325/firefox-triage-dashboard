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


# ─── POST /queue/remove ─────────────────────────────────────────────

def test_queue_remove_strips_matching_apply(triage_dir: Path) -> None:
    """Removing an apply by (action, bug_id, ts) drops just that entry."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-30T00:00:00+00:00"}\n'
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T00:01:00+00:00"}\n'
        '{"action":"bug-start","bug_id":1,"ts":"2026-05-30T00:02:00+00:00"}\n'
    )
    response = client.post("/queue/remove", data={
        "action": "apply", "bug_id": 1, "ts": "2026-05-30T00:01:00+00:00",
    })
    assert response.status_code == 200
    # Only refine + bug-start remain.
    actions = [
        json.loads(line)["action"]
        for line in queue_path.read_text().splitlines() if line
    ]
    assert actions == ["refine", "bug-start"]


def test_queue_remove_404_when_no_match(triage_dir: Path) -> None:
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-30T00:00:00+00:00"}\n'
    )
    response = client.post("/queue/remove", data={
        "action": "refine", "bug_id": 1, "ts": "2099-01-01T00:00:00+00:00",
    })
    assert response.status_code == 404


def test_queue_remove_400_on_missing_fields(triage_dir: Path) -> None:
    response = client.post("/queue/remove", data={
        "action": "refine", "bug_id": 1,  # no ts
    })
    assert response.status_code == 400
    response = client.post("/queue/remove", data={
        "bug_id": 1, "ts": "2026-05-30T00:00:00+00:00",  # no action
    })
    assert response.status_code == 400


def test_queue_remove_400_on_unknown_action(triage_dir: Path) -> None:
    """Action must be one of the known drainable types."""
    response = client.post("/queue/remove", data={
        "action": "future-thing", "bug_id": 1,
        "ts": "2026-05-30T00:00:00+00:00",
    })
    assert response.status_code == 400


def test_queue_remove_html_fragment_for_hx_request(triage_dir: Path) -> None:
    entry_ts = "2026-05-30T00:00:00+00:00"
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"apply","bug_id":1,"ts":"' + entry_ts + '"}\n'
    )
    response = client.post(
        "/queue/remove",
        data={"action": "apply", "bug_id": 1, "ts": entry_ts},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_queue_remove_json_for_non_htmx(triage_dir: Path) -> None:
    entry_ts = "2026-05-30T00:00:00+00:00"
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"bug-start","bug_id":1,"ts":"' + entry_ts + '"}\n'
    )
    response = client.post("/queue/remove", data={
        "action": "bug-start", "bug_id": 1, "ts": entry_ts,
    })
    assert response.json() == {"ok": True}


# ─── GET /queue/dropdown (topbar dropdown content) ──────────────────

def test_dropdown_empty_queue_renders_empty_state(triage_dir: Path) -> None:
    body = client.get("/queue/dropdown").text
    assert "Nothing queued" in body
    # Copy button must exist even when empty — but disabled.
    assert 'id="btn-copy-prompt"' in body
    assert "disabled" in body


def test_dropdown_renders_each_action_type(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    write_draft(triage_dir, 2, ni_targets=["x@y"])
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"refine","bug_id":1,"feedback":"shorten it","ts":"2026-05-30T14:30:00+00:00"}\n'
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T14:35:00+00:00"}\n'
        '{"action":"bug-start","bug_id":2,"ts":"2026-05-30T15:00:00+00:00"}\n'
    )
    body = client.get("/queue/dropdown").text
    assert "queue-badge--refine" in body
    assert "queue-badge--apply" in body
    assert "queue-badge--bug-start" in body
    assert "shorten it" in body
    # The whole row is a click target with htmx attributes pointing at
    # the bug's section tab — not just the bug id.
    assert 'class="queue-dropdown-jump"' in body
    assert 'href="?tab=triaged&bug=1"' in body
    assert 'href="?tab=needs-info&bug=2"' in body
    # And the htmx hooks for the partial swap.
    assert 'hx-get="?tab=triaged&bug=1"' in body
    assert 'hx-target="#tab-content"' in body
    assert 'hx-push-url="true"' in body


def test_dropdown_orphan_row_not_clickable(triage_dir: Path) -> None:
    """A queue entry whose bug has no pending JSON should NOT carry a
    jump link — there's no card to navigate to."""
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"apply","bug_id":9999,"ts":"2026-05-30T00:00:00+00:00"}\n'
    )
    body = client.get("/queue/dropdown").text
    assert "queue-dropdown-jump--orphan" in body
    # No href for the orphan row.
    assert 'href="?tab=' not in body or "bug=9999" not in body


def test_dropdown_remove_button_hits_queue_remove(triage_dir: Path) -> None:
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T00:00:00+00:00"}\n'
    )
    body = client.get("/queue/dropdown").text
    assert 'hx-post="/queue/remove"' in body
    assert '"action": "apply"' in body


def test_dropdown_copy_button_enabled_when_queue_has_entries(
    triage_dir: Path,
) -> None:
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T00:00:00+00:00"}\n'
    )
    body = client.get("/queue/dropdown").text
    # Pull out the copy button HTML and confirm no `disabled` attribute.
    import re
    m = re.search(
        r'<button[^>]*id="btn-copy-prompt"[^>]*>', body,
    )
    assert m is not None
    assert "disabled" not in m.group(0)
