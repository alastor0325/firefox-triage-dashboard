"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _default_reply_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Default every test to **reply mode** and isolate from the real
    `~/.config/triage/secrets`.

    The dashboard reads a Bugzilla API key (env or secrets file) to decide
    reply vs read-only; without this, tests would inherit whatever key the
    machine happens to have, making apply/owner-affordance assertions
    machine-dependent. Reply mode is the historical default (full affordances),
    so existing tests keep passing everywhere. Read-only tests override by
    deleting the env var.
    """
    from triage_dashboard import reply_mode

    monkeypatch.setenv("BUGZILLA_BOT_API_KEY", "test-key")
    monkeypatch.setattr(
        reply_mode, "SECRETS_PATH", tmp_path_factory.mktemp("nosecrets") / "secrets"
    )


@pytest.fixture
def triage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an empty triage data directory and point $TRIAGE_DIR at it.

    Both data loaders and the FastAPI app honour $TRIAGE_DIR, so tests get
    an isolated workspace per test without touching ~/firefox-triage/.
    """
    (tmp_path / "pending").mkdir()
    monkeypatch.setenv("TRIAGE_DIR", str(tmp_path))
    return tmp_path


def write_draft(triage_dir: Path, bug_id: int, **overrides: Any) -> Path:
    """Write a pending bug-N.json file with sensible defaults for testing."""
    base = {
        "bug_id": bug_id,
        "title": f"test bug {bug_id}",
        "comment": "",
        "ni_targets": [],
        "priority": None,
        "severity": None,
        "blocks_add": [],
        "cc_add": [],
        "resolution": None,
        "keywords_add": [],
        "product": None,
        "component": None,
        "created_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    path = triage_dir / "pending" / f"bug-{bug_id}.json"
    path.write_text(json.dumps(base))
    return path


def write_applied_draft(triage_dir: Path, bug_id: int, **overrides: Any) -> Path:
    """Write an applied/bug-N.json archive (same shape as a pending draft)."""
    base = {
        "bug_id": bug_id,
        "title": f"test bug {bug_id}",
        "comment": "",
        "ni_targets": [],
        "priority": None,
        "severity": None,
        "blocks_add": [],
        "cc_add": [],
        "resolution": None,
        "keywords_add": [],
        "product": None,
        "component": None,
        "created_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    applied = triage_dir / "applied"
    applied.mkdir(exist_ok=True)
    path = applied / f"bug-{bug_id}.json"
    path.write_text(json.dumps(base))
    return path
