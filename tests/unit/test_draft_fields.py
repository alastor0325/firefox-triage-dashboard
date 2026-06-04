"""Unit tests for the Will-apply severity/priority override helpers in
data.py — `level_options` (selectable values) and `set_draft_field` (persist
an explicit S/P override onto a pending draft)."""

from __future__ import annotations

import json
from pathlib import Path

from triage_dashboard import data


# ─── level_options (pure) ────────────────────────────────────────────

def test_level_options_severity() -> None:
    assert data.level_options("severity") == ("S1", "S2", "S3", "S4")


def test_level_options_priority() -> None:
    assert data.level_options("priority") == ("P1", "P2", "P3", "P4", "P5")


def test_level_options_unknown_field_is_empty() -> None:
    assert data.level_options("bogus") == ()


def test_level_options_back_the_level_class_sets() -> None:
    # The ordered option tuples are the single source for the (unordered)
    # validation sets level_class() uses.
    for s in data.level_options("severity"):
        assert data.level_class(s) == s.lower()
    for p in data.level_options("priority"):
        assert data.level_class(p) == p.lower()


# ─── set_draft_field (read-modify-write) ─────────────────────────────

def _draft(tmp_path: Path, **fields) -> Path:
    pend = tmp_path / "pending"
    pend.mkdir(exist_ok=True)
    base = {"bug_id": 5, "severity": "S3", "priority": "P3"}
    base.update(fields)
    p = pend / "bug-5.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_set_draft_field_overrides_severity(tmp_path) -> None:
    p = _draft(tmp_path)
    assert data.set_draft_field(tmp_path, 5, "severity", "S1") is True
    assert json.loads(p.read_text())["severity"] == "S1"


def test_set_draft_field_overrides_priority(tmp_path) -> None:
    p = _draft(tmp_path)
    assert data.set_draft_field(tmp_path, 5, "priority", "P1") is True
    assert json.loads(p.read_text())["priority"] == "P1"


def test_set_draft_field_is_case_insensitive(tmp_path) -> None:
    p = _draft(tmp_path)
    assert data.set_draft_field(tmp_path, 5, "severity", "s2") is True
    assert json.loads(p.read_text())["severity"] == "S2"


def test_set_draft_field_rejects_invalid_value(tmp_path) -> None:
    p = _draft(tmp_path)
    assert data.set_draft_field(tmp_path, 5, "severity", "P1") is False  # wrong family
    assert data.set_draft_field(tmp_path, 5, "severity", "S9") is False
    assert json.loads(p.read_text())["severity"] == "S3"                 # unchanged


def test_set_draft_field_unknown_field_is_noop(tmp_path) -> None:
    _draft(tmp_path)
    assert data.set_draft_field(tmp_path, 5, "status", "NEW") is False


def test_set_draft_field_missing_file(tmp_path) -> None:
    (tmp_path / "pending").mkdir()
    assert data.set_draft_field(tmp_path, 999, "severity", "S1") is False
