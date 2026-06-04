"""Unit tests for triage_dashboard.applier — pure plan computation."""

from __future__ import annotations

from triage_dashboard import applier


def _pending(**fields) -> dict:
    base = {
        "bug_id": 1, "title": "x", "comment": "", "ni_targets": [],
        "priority": None, "severity": None, "blocks_add": [], "cc_add": [],
        "resolution": None, "keywords_add": [], "product": None,
        "component": None, "created_at": "",
    }
    base.update(fields)
    return base


def kinds(plan) -> list[str]:
    return [a.kind for a in plan]


# ─── plan_apply ─────────────────────────────────────────────────────

def test_empty_pending_makes_empty_plan() -> None:
    assert applier.plan_apply(_pending()) == []


def test_plan_includes_comment() -> None:
    plan = applier.plan_apply(_pending(comment="Hello, bug."))
    assert any(a.kind == "comment" for a in plan)


def test_plan_includes_severity_and_priority() -> None:
    plan = applier.plan_apply(_pending(severity="S3", priority="P3"))
    assert "severity" in kinds(plan)
    assert "priority" in kinds(plan)
    sev = next(a for a in plan if a.kind == "severity")
    assert "S3" in sev.description


def test_plan_includes_ni_per_target() -> None:
    """One NI entry per target — easier to read and to cancel a specific one."""
    plan = applier.plan_apply(_pending(ni_targets=["a@x.com", "b@y.com"]))
    ni_actions = [a for a in plan if a.kind == "ni"]
    assert len(ni_actions) == 2
    assert "a@x.com" in ni_actions[0].description
    assert "b@y.com" in ni_actions[1].description


def test_plan_includes_cc_add() -> None:
    plan = applier.plan_apply(_pending(cc_add=["triager@example.com"]))
    assert any(a.kind == "cc" and "triager" in a.description for a in plan)


def test_plan_includes_blocks_add() -> None:
    plan = applier.plan_apply(_pending(blocks_add=[1746557, 2023365]))
    blocks = [a for a in plan if a.kind == "blocks"]
    assert len(blocks) == 2
    assert "1746557" in blocks[0].description
    assert "2023365" in blocks[1].description


def test_plan_includes_keywords() -> None:
    plan = applier.plan_apply(_pending(keywords_add=["stalled"]))
    assert any(a.kind == "keyword" and "stalled" in a.description for a in plan)


def test_plan_includes_resolution() -> None:
    plan = applier.plan_apply(_pending(resolution="INCOMPLETE"))
    res = [a for a in plan if a.kind == "resolution"]
    assert len(res) == 1
    assert "INCOMPLETE" in res[0].description


def test_plan_includes_reassign() -> None:
    plan = applier.plan_apply(_pending(product="Core", component="Widget: Gtk"))
    rs = [a for a in plan if a.kind == "reassign"]
    assert len(rs) == 1
    assert "Widget: Gtk" in rs[0].description


def test_plan_watch_add_when_ni_targets() -> None:
    """Per the /triage skill, non-empty ni_targets implies a watch-add."""
    plan = applier.plan_apply(_pending(ni_targets=["x@y.com"]))
    assert "watch-add" in kinds(plan)


def test_plan_no_watch_add_when_no_ni() -> None:
    plan = applier.plan_apply(_pending())
    assert "watch-add" not in kinds(plan)


def test_plan_stable_order() -> None:
    """Plan order is: fields first (sev/pri/resolution/reassign), then blocks,
    cc, keywords, ni, comment last, watch-add at the very end. So a reviewer
    reads the Bugzilla-impacting metadata before the long comment text."""
    plan = applier.plan_apply(_pending(
        comment="hello",
        severity="S3", priority="P3",
        blocks_add=[1], cc_add=["x@y.com"], keywords_add=["regression"],
        ni_targets=["r@y.com"], resolution="INCOMPLETE",
    ))
    order = kinds(plan)
    # Sev/Pri before comment
    assert order.index("severity") < order.index("comment")
    assert order.index("priority") < order.index("comment")
    # Comment near the end, before watch-add
    assert order.index("comment") < order.index("watch-add")


# ─── plan_skip ──────────────────────────────────────────────────────

def test_plan_skip_describes_delete_and_log() -> None:
    plan = applier.plan_skip(_pending(bug_id=2039425))
    actions = kinds(plan)
    assert "delete-pending" in actions
    assert "log-skipped" in actions
