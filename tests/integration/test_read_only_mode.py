"""Integration tests: read-only vs reply mode UX on the full-page render."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard import claude_queue, reply_mode
from triage_dashboard.app import app

client = TestClient(app)


def _force_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No env key and a non-existent secrets file ⇒ read-only."""
    monkeypatch.delenv("BUGZILLA_BOT_API_KEY", raising=False)
    monkeypatch.setattr(reply_mode, "SECRETS_PATH", tmp_path / "no-secrets")


def _force_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BUGZILLA_BOT_API_KEY", "test-key")
    monkeypatch.setattr(reply_mode, "SECRETS_PATH", tmp_path / "no-secrets")


def test_read_only_shows_badge_and_no_apply_button(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_read_only(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123)
    body = client.get("/").text
    assert "badge-readonly" in body  # the header badge
    assert "btn-readonly" in body  # the disabled pill in place of Apply
    # no live apply action is offered
    assert 'hx-post="/draft/123/apply"' not in body


def test_reply_mode_shows_apply_button_no_badge(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_reply(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123)
    body = client.get("/").text
    assert "badge-readonly" not in body
    assert 'hx-post="/draft/123/apply"' in body


def test_apply_route_is_a_no_op_in_read_only(
    triage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_read_only(monkeypatch, tmp_path)
    write_draft(triage_dir, bug_id=123)
    client.post("/draft/123/apply")
    assert claude_queue.is_apply_queued(triage_dir, 123) is False
