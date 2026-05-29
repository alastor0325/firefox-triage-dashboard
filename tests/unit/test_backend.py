"""Unit tests for triage_dashboard.backend — the swappable apply/skip backend."""

from __future__ import annotations

import pytest

from triage_dashboard import applier, backend


# ─── BackendResult ──────────────────────────────────────────────────

def test_backend_result_required_fields() -> None:
    r = backend.BackendResult(ok=True, actions=[])
    assert r.ok is True
    assert r.actions == []
    assert r.output == ""
    assert r.side_effects == []


def test_backend_result_full_fields() -> None:
    actions = [applier.PlannedAction(kind="severity", description="set severity → S3")]
    r = backend.BackendResult(
        ok=True, actions=actions,
        output="some log", side_effects=["did the thing"],
    )
    assert r.actions == actions
    assert r.output == "some log"
    assert r.side_effects == ["did the thing"]


# ─── MockBackend.apply ──────────────────────────────────────────────

def test_mock_apply_returns_backend_result() -> None:
    r = backend.MockBackend().apply(1, {"bug_id": 1, "severity": "S3"})
    assert isinstance(r, backend.BackendResult)
    assert r.ok is True
    assert r.side_effects == []


def test_mock_apply_actions_equal_planner_output() -> None:
    """MockBackend delegates to applier.plan_apply — anything in the plan
    appears in the result's actions list, in the same order."""
    pending = {
        "bug_id": 2039425,
        "severity": "S3", "priority": "P3",
        "blocks_add": [1746557],
        "ni_targets": ["alwu@mozilla.com"],
        "cc_add": ["alwu@mozilla.com"],
        "comment": "root cause analysis",
    }
    r = backend.MockBackend().apply(2039425, pending)
    assert r.actions == applier.plan_apply(pending)


def test_mock_apply_output_signals_dry_run() -> None:
    r = backend.MockBackend().apply(1, {"bug_id": 1})
    assert "dry" in r.output.lower() or "mock" in r.output.lower()


def test_mock_apply_empty_pending_yields_empty_plan() -> None:
    """No fields → no actions, but still ok=True (nothing to do is fine)."""
    r = backend.MockBackend().apply(1, {"bug_id": 1})
    assert r.ok is True
    assert r.actions == []


# ─── MockBackend.skip ───────────────────────────────────────────────

def test_mock_skip_returns_backend_result() -> None:
    r = backend.MockBackend().skip(1, {"bug_id": 1})
    assert isinstance(r, backend.BackendResult)
    assert r.ok is True
    assert r.side_effects == []


def test_mock_skip_actions_equal_planner_output() -> None:
    pending = {"bug_id": 42}
    r = backend.MockBackend().skip(42, pending)
    assert r.actions == applier.plan_skip(pending)


def test_mock_skip_output_signals_dry_run() -> None:
    r = backend.MockBackend().skip(1, {"bug_id": 1})
    assert "dry" in r.output.lower() or "mock" in r.output.lower()


# ─── BugzillaCLIBackend stub (must raise — Phase 4.5 gate) ──────────

def test_real_backend_apply_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError) as exc:
        backend.BugzillaCLIBackend().apply(1, {"bug_id": 1, "severity": "S3"})
    msg = str(exc.value).lower()
    # The message should signal the approval gate, not a generic stub.
    assert "approval" in msg or "not wired" in msg or "not yet" in msg


def test_real_backend_skip_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        backend.BugzillaCLIBackend().skip(1, {"bug_id": 1})


# ─── get_backend() — env-var selection ──────────────────────────────

def test_get_backend_default_is_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRIAGE_DASHBOARD_LIVE", raising=False)
    assert isinstance(backend.get_backend(), backend.MockBackend)


def test_get_backend_live_env_selects_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", "1")
    assert isinstance(backend.get_backend(), backend.BugzillaCLIBackend)


def test_get_backend_non_1_value_stays_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact string '1' selects live — any other value is mock."""
    for val in ["0", "false", "", "yes", "true", "live"]:
        monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", val)
        assert isinstance(backend.get_backend(), backend.MockBackend), val


def test_get_backend_live_mode_call_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live env var selects real backend, but calling it still raises —
    proving the second gate (no real implementation) is in effect. Flipping
    only the env var must not produce a Bugzilla write."""
    monkeypatch.setenv("TRIAGE_DASHBOARD_LIVE", "1")
    with pytest.raises(NotImplementedError):
        backend.get_backend().apply(1, {"bug_id": 1})
