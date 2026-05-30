"""Integration tests for the card-head severity/priority pills.

The card-head reflects the bug's CURRENT Bugzilla state (from
`bug_context.current_severity` / `current_priority`), not the draft's
proposed values. The draft's proposed values still appear in the
"Will apply" footer.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── color-coded S/P pills (current Bugzilla state) ─────────────────

def test_card_head_renders_s1_p1_modifier_classes(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"current_severity": "S1", "current_priority": "P1"},
    )
    body = client.get("/").text
    assert "badge-level--s1" in body
    assert "badge-level--p1" in body
    # The old combined draft-derived badge-ps pill is gone.
    assert 'class="badge badge-ps"' not in body


def test_card_head_renders_s3_p3_modifier_classes(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"current_severity": "S3", "current_priority": "P3"},
    )
    body = client.get("/").text
    assert "badge-level--s3" in body
    assert "badge-level--p3" in body


def test_card_head_missing_sp_renders_unknown_warning(triage_dir: Path) -> None:
    write_draft(triage_dir, 1)
    body = client.get("/").text
    # Two unknown pills with placeholder text.
    assert body.count("badge-level--unknown") >= 2
    assert "S?" in body
    assert "P?" in body


def test_card_head_partial_sp_only_severity(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"current_severity": "S3"},
    )
    body = client.get("/").text
    assert "badge-level--s3" in body
    assert "badge-level--unknown" in body
    # Priority pill specifically renders the unknown placeholder.
    assert "P?" in body


def test_card_head_unrecognised_level_falls_back_to_unknown(
    triage_dir: Path,
) -> None:
    """Legacy values like 'critical' or 'normal' shouldn't produce an
    unstyled pill — they fall through to the same warning treatment as
    a missing value."""
    write_draft(
        triage_dir, 1,
        bug_context={"current_severity": "critical", "current_priority": "normal"},
    )
    body = client.get("/").text
    # Two unknown pills.
    assert body.count("badge-level--unknown") >= 2
