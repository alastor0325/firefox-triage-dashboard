"""Integration tests for the deck-view layout: rail (left) + single focused card."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── rail (left list) ────────────────────────────────────────────────

def test_rail_lists_every_bug_in_the_current_tab(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["a@b"])
    write_draft(triage_dir, 2, ni_targets=["a@b"])
    write_draft(triage_dir, 3, ni_targets=["a@b"])
    body = client.get("/?tab=needs-info").text
    # Every bug should have a rail entry (this attribute is unique to rail items)
    assert body.count('class="rail-item') >= 3
    assert 'data-bug-id="1"' in body
    assert 'data-bug-id="2"' in body
    assert 'data-bug-id="3"' in body


def test_rail_does_not_show_bugs_from_other_tabs(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["a@b"])           # §1a
    write_draft(triage_dir, 2, severity="S3", priority="P3") # §1b
    body = client.get("/?tab=needs-info").text
    assert 'data-bug-id="1"' in body      # in §1a → in rail
    assert 'data-bug-id="2"' not in body  # §1b lives in a different tab


def test_rail_marks_the_active_bug(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["a@b"])
    write_draft(triage_dir, 2, ni_targets=["a@b"])
    body = client.get("/?tab=needs-info&bug=2").text
    # Exactly one rail item is the active one.
    assert body.count("rail-item is-active") == 1
    # And it's bug-2, not bug-1.
    import re
    active_match = re.search(
        r'class="rail-item is-active"[^>]*data-bug-id="(\d+)"', body
    )
    assert active_match is not None
    assert active_match.group(1) == "2"


# ─── active card selection ──────────────────────────────────────────

def test_only_the_active_card_is_rendered_in_the_stage(
    triage_dir: Path,
) -> None:
    """The deck stage shows exactly one card at a time, not the whole bucket."""
    write_draft(triage_dir, 1, ni_targets=["a@b"], title="alpha")
    write_draft(triage_dir, 2, ni_targets=["a@b"], title="bravo")
    write_draft(triage_dir, 3, ni_targets=["a@b"], title="charlie")
    body = client.get("/?tab=needs-info&bug=2").text
    # The card with id="card-N" is only in the stage, not in the rail.
    assert 'id="card-2"' in body
    assert 'id="card-1"' not in body
    assert 'id="card-3"' not in body


def test_active_card_defaults_to_first_in_bucket(triage_dir: Path) -> None:
    write_draft(triage_dir, 10, ni_targets=["a@b"])
    write_draft(triage_dir, 20, ni_targets=["a@b"])
    write_draft(triage_dir, 30, ni_targets=["a@b"])
    body = client.get("/?tab=needs-info").text
    # Without an explicit ?bug=, the lowest-numbered bug is active (sort order).
    assert 'id="card-10"' in body
    assert 'id="card-20"' not in body


def test_invalid_bug_param_falls_back_to_first(triage_dir: Path) -> None:
    write_draft(triage_dir, 10, ni_targets=["a@b"])
    write_draft(triage_dir, 20, ni_targets=["a@b"])
    body = client.get("/?tab=needs-info&bug=999999").text
    # 999999 is not in the bucket — fall back to first.
    assert 'id="card-10"' in body


def test_bug_param_for_wrong_tab_falls_back(triage_dir: Path) -> None:
    """A ?bug= that exists but is in a different tab should fall back."""
    write_draft(triage_dir, 1, ni_targets=["a@b"])              # §1a
    write_draft(triage_dir, 2, severity="S3", priority="P3")    # §1b
    body = client.get("/?tab=needs-info&bug=2").text
    # bug 2 is in §1b — falls back to first §1a bug.
    assert 'id="card-1"' in body
    assert 'id="card-2"' not in body


# ─── watching tab — keeps multi-item list ──────────────────────────

def test_watching_tab_does_not_use_the_rail(
    triage_dir: Path, monkeypatch
) -> None:
    """Watching is monitoring, not action — no deck, no rail."""
    import json
    (triage_dir / "ni-watch.json").write_text(
        json.dumps([
            {"bug_id": 7777, "title": "x", "ni_targets": [], "added_at": ""},
            {"bug_id": 8888, "title": "y", "ni_targets": [], "added_at": ""},
        ])
    )
    body = client.get("/?tab=watching").text
    assert 'class="rail-item' not in body
    # Both watched bugs are present (no single-item selection).
    assert "7777" in body
    assert "8888" in body
