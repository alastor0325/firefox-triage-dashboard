"""Unit tests for the triage-owner CC/NI toggle helpers in data.py — the
per-draft 'CC me' / 'NI me' opt-ins (default off)."""

from __future__ import annotations

import json
from pathlib import Path

from triage_dashboard import data


# ─── toggle_in_list (pure) ───────────────────────────────────────────

def test_toggle_in_list_add() -> None:
    assert data.toggle_in_list([], "a@b", True) == ["a@b"]


def test_toggle_in_list_remove() -> None:
    assert data.toggle_in_list(["a@b", "c@d"], "a@b", False) == ["c@d"]


def test_toggle_in_list_add_is_idempotent() -> None:
    assert data.toggle_in_list(["a@b"], "a@b", True) == ["a@b"]


def test_toggle_in_list_preserves_others_on_add() -> None:
    assert data.toggle_in_list(["rep@x"], "owner@x", True) == ["rep@x", "owner@x"]


def test_toggle_in_list_empty_value_is_noop() -> None:
    assert data.toggle_in_list(["x"], "", True) == ["x"]


# ─── triage_owner ────────────────────────────────────────────────────

def test_triage_owner_trims(monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_OWNER", "  owner@x.com  ")
    assert data.triage_owner() == "owner@x.com"


def test_triage_owner_unset(monkeypatch) -> None:
    monkeypatch.delenv("TRIAGE_OWNER", raising=False)
    assert data.triage_owner() == ""


# ─── set_owner_membership (read-modify-write) ────────────────────────

def _draft(tmp_path: Path, **fields) -> Path:
    pend = tmp_path / "pending"
    pend.mkdir(exist_ok=True)
    base = {"bug_id": 5, "cc_add": [], "ni_targets": []}
    base.update(fields)
    p = pend / "bug-5.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def test_set_owner_membership_add_then_remove_cc(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_OWNER", "owner@x.com")
    p = _draft(tmp_path)
    assert data.set_owner_membership(tmp_path, 5, "cc_add", True) is True
    assert json.loads(p.read_text())["cc_add"] == ["owner@x.com"]
    assert data.set_owner_membership(tmp_path, 5, "cc_add", False) is True
    assert json.loads(p.read_text())["cc_add"] == []


def test_set_owner_membership_ni_preserves_reporter(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_OWNER", "owner@x.com")
    p = _draft(tmp_path, ni_targets=["reporter@x.com"])
    data.set_owner_membership(tmp_path, 5, "ni_targets", True)
    assert json.loads(p.read_text())["ni_targets"] == ["reporter@x.com", "owner@x.com"]


def test_set_owner_membership_no_owner_is_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TRIAGE_OWNER", raising=False)
    _draft(tmp_path)
    assert data.set_owner_membership(tmp_path, 5, "cc_add", True) is False


def test_set_owner_membership_missing_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_OWNER", "owner@x.com")
    (tmp_path / "pending").mkdir()
    assert data.set_owner_membership(tmp_path, 999, "cc_add", True) is False


def test_set_owner_membership_unknown_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_OWNER", "owner@x.com")
    _draft(tmp_path)
    assert data.set_owner_membership(tmp_path, 5, "bogus", True) is False


# ─── broker self-write suppression (no whole-tab SSE refresh on toggle) ──

def test_broker_suppress_path_within_window() -> None:
    from triage_dashboard.watch import FileWatchBroker
    b = FileWatchBroker()
    assert b.suppressed("/t/pending/bug-1.json") is False
    b.suppress_path("/t/pending/bug-1.json")
    assert b.suppressed("/t/pending/bug-1.json") is True       # this path skipped
    assert b.suppressed("/t/pending/bug-2.json") is False      # others unaffected


def test_broker_suppress_path_expires() -> None:
    from triage_dashboard.watch import FileWatchBroker
    b = FileWatchBroker()
    b.suppress_path("/t/pending/bug-1.json")
    b._suppressed["/t/pending/bug-1.json"] = 0.0               # force past expiry
    assert b.suppressed("/t/pending/bug-1.json") is False
