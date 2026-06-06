"""Integration tests: read-only vs reply mode UX on the full-page render."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard import claude_queue, reply_mode
from triage_dashboard.app import app

client = TestClient(app)

# A draft that exercises every write affordance: P/S (will-apply diff),
# plus the owner-routing writes the user must NOT see in read-only.
DRAFT = dict(
    severity="S3",
    priority="P2",
    ni_targets=["reporter@example.com"],
    cc_add=["cc-me@example.com"],
    assigned_to="assignee@example.com",
)


def _force_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BUGZILLA_BOT_API_KEY", raising=False)
    monkeypatch.setattr(reply_mode, "SECRETS_PATH", tmp_path / "no-secrets")


def _force_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BUGZILLA_BOT_API_KEY", "test-key")
    monkeypatch.setenv("TRIAGE_OWNER", "owner@example.com")
    monkeypatch.setattr(reply_mode, "SECRETS_PATH", tmp_path / "no-secrets")


def test_read_only_shows_badge_and_no_apply_button(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_read_only(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123, **DRAFT)
    body = client.get("/").text
    assert "badge-readonly" in body  # the header badge
    assert "btn-readonly" in body  # the disabled pill in place of Apply
    assert 'hx-post="/draft/123/apply"' not in body


def test_read_only_hides_owner_cc_ni_assign(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_read_only(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123, **DRAFT)
    body = client.get("/").text
    # No owner CC/NI/Assign toggles, and no cc/ni/assignee diff rows.
    assert "owner-toggles" not in body
    assert "reporter@example.com" not in body  # +ni row hidden
    assert "cc-me@example.com" not in body  # +cc row hidden
    assert "assignee@example.com" not in body  # assign-to row hidden
    # The triage decision (proposed S/P) is still shown read-only.
    assert "S3" in body and "P2" in body
    # The will-apply diff is relabelled "Proposed" (nothing applies in read-only).
    assert "diff--proposed" in body


def test_reply_mode_shows_apply_and_owner_affordances(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_reply(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123, **DRAFT)
    body = client.get("/").text
    assert "badge-readonly" not in body
    assert 'hx-post="/draft/123/apply"' in body
    assert "owner-toggles" in body  # CC me / NI me / Assign me
    assert "reporter@example.com" in body
    assert "cc-me@example.com" in body
    assert "assignee@example.com" in body
    assert "diff--proposed" not in body  # reply mode keeps the "Will apply" label


def test_write_routes_are_blocked_in_read_only(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_read_only(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123, **DRAFT)
    # apply: no-op (never queues)
    client.post("/draft/123/apply")
    assert claude_queue.is_apply_queued(triage_dir, 123) is False
    # owner + field writes: refused
    assert client.post("/draft/123/owner/cc").status_code == 403
    assert client.post("/draft/123/owner/assign").status_code == 403
    assert client.post("/draft/123/field/severity", data={"value": "S1"}).status_code == 403
