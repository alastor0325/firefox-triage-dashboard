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


# ─── HTML response when called via htmx ────────────────────────────

def test_refine_returns_html_fragment_for_hx_request(triage_dir: Path) -> None:
    """htmx clients get an HTML fragment so it can be swapped into the page."""
    write_draft(triage_dir, 2039425)
    response = client.post(
        "/draft/2039425/refine",
        data={"feedback": "shorten the analysis"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    # The fragment should echo back the user's feedback so they see it took.
    assert "shorten the analysis" in body
    # And signal that it's queued for revision (visible to the user).
    assert "Revising" in body or "revising" in body or "Queued" in body


def test_refine_returns_json_for_non_htmx_request(triage_dir: Path) -> None:
    """Non-htmx callers (curl, scripts, tests) still get JSON."""
    write_draft(triage_dir, 1)
    response = client.post("/draft/1/refine", data={"feedback": "x"})
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True


def test_refine_hx_fragment_escapes_html(triage_dir: Path) -> None:
    """User-submitted feedback must be HTML-escaped before being echoed back."""
    write_draft(triage_dir, 1)
    response = client.post(
        "/draft/1/refine",
        data={"feedback": "<script>alert(1)</script>"},
        headers={"HX-Request": "true"},
    )
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


# ─── POST /draft/{id}/refine/remove ─────────────────────────────────

def test_refine_remove_removes_matching_entry(triage_dir: Path) -> None:
    """Posting the entry's ts removes that one entry from the queue."""
    write_draft(triage_dir, 1)
    # Queue two entries.
    client.post("/draft/1/refine", data={"feedback": "keep"})
    client.post("/draft/1/refine", data={"feedback": "drop"})

    entries = _queue_entries(triage_dir)
    assert len(entries) == 2
    drop_ts = entries[1]["ts"]

    response = client.post(
        "/draft/1/refine/remove", data={"ts": drop_ts}
    )
    assert response.status_code == 200

    remaining = _queue_entries(triage_dir)
    assert [e["feedback"] for e in remaining] == ["keep"]


def test_refine_remove_returns_404_when_entry_missing(
    triage_dir: Path,
) -> None:
    """Unknown ts → 404, queue untouched."""
    write_draft(triage_dir, 1)
    client.post("/draft/1/refine", data={"feedback": "a"})

    response = client.post(
        "/draft/1/refine/remove", data={"ts": "2099-01-01T00:00:00+00:00"}
    )
    assert response.status_code == 404
    assert len(_queue_entries(triage_dir)) == 1


def test_refine_remove_returns_404_when_bug_has_no_entries(
    triage_dir: Path,
) -> None:
    """Removing from a bug with no queued feedback → 404."""
    write_draft(triage_dir, 1)
    response = client.post(
        "/draft/1/refine/remove", data={"ts": "2026-05-29T00:00:00+00:00"}
    )
    assert response.status_code == 404


def test_refine_remove_rejects_empty_ts(triage_dir: Path) -> None:
    """Missing/empty ts → 400."""
    write_draft(triage_dir, 1)
    response = client.post("/draft/1/refine/remove", data={"ts": ""})
    assert response.status_code == 400


def test_refine_remove_returns_html_fragment_for_hx_request(
    triage_dir: Path,
) -> None:
    """htmx callers get back an HTML fragment they can swap in to clear
    the removed item from the rendered list."""
    write_draft(triage_dir, 1)
    client.post("/draft/1/refine", data={"feedback": "x"})
    ts = _queue_entries(triage_dir)[0]["ts"]

    response = client.post(
        "/draft/1/refine/remove",
        data={"ts": ts},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_refine_remove_returns_json_for_non_htmx_request(
    triage_dir: Path,
) -> None:
    """Non-htmx callers get JSON."""
    write_draft(triage_dir, 1)
    client.post("/draft/1/refine", data={"feedback": "x"})
    ts = _queue_entries(triage_dir)[0]["ts"]

    response = client.post("/draft/1/refine/remove", data={"ts": ts})
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"ok": True}
