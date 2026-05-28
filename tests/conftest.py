"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


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
