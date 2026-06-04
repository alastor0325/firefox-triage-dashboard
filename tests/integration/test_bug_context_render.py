"""Integration tests for bug_context rendering on the focused card."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── byline (reporter / platform / version / last activity) ──────────

def test_byline_renders_reporter_platform_version(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "reporter_name": "Ryan McCartney",
            "platform": "Windows 10 x64",
            "firefox_version": "150.0",
        },
    )
    body = client.get("/").text
    assert "Ryan McCartney" in body
    assert "Windows 10 x64" in body
    assert "150.0" in body


def test_found_chip_omitted_when_no_version_number(triage_dir: Path) -> None:
    # firefox_version with no actual version number → no 'Found' chip,
    # but the platform chip still renders the version line.
    write_draft(
        triage_dir, 1,
        bug_context={"platform": "Windows 11", "firefox_version": "unspecified"},
    )
    body = client.get("/").text
    assert "Platform" in body
    assert "Found" not in body


def test_found_chip_shown_when_version_number_present(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"firefox_version": "Nightly 153.0a1"})
    body = client.get("/").text
    assert "Found" in body
    assert "153.0a1" in body


def test_byline_falls_back_to_email_when_no_name(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"reporter_email": "user@example.com"},
    )
    body = client.get("/").text
    assert "user@example.com" in body


def test_byline_renders_last_activity_date(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"last_activity": "2026-05-22T14:08:00Z"},
    )
    body = client.get("/").text
    # The date portion is enough — we don't need to show the time.
    assert "2026-05-22" in body


# ─── inventory chips ─────────────────────────────────────────────────

def test_inventory_chips_rendered_when_present(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "inventory_present": ["platform / version", "test URLs"],
            "inventory_missing": ["about:support", "media log"],
        },
    )
    body = client.get("/").text
    assert 'class="inventory"' in body
    assert "platform / version" in body
    assert "test URLs" in body
    assert "about:support" in body
    assert "media log" in body


def test_inventory_chips_absent_when_inventory_empty(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "platform": "Linux",
            "inventory_present": [],
            "inventory_missing": [],
        },
    )
    body = client.get("/").text
    assert 'class="inventory"' not in body


# ─── see-also pills ─────────────────────────────────────────────────

def test_see_also_pills_link_to_bugs(triage_dir: Path) -> None:
    """Non-regressor see_also entries render as see-also-pill items
    (regressors get their own block — see test_see_also_split)."""
    write_draft(
        triage_dir, 1,
        bug_context={
            "see_also": [
                {"bug_id": 2012108, "label": "follow-up fix"},
                {"bug_id": 2012109, "label": "same root cause"},
            ],
        },
    )
    body = client.get("/").text
    assert "see-also-pill" in body
    assert "2012108" in body
    assert "follow-up fix" in body
    assert "2012109" in body
    assert "same root cause" in body


# ─── expandable sections ────────────────────────────────────────────

def test_description_excerpt_in_expandable(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={"description_excerpt": "When playing HEVC content via DASH..."},
    )
    body = client.get("/").text
    assert "Bug description" in body
    assert "When playing HEVC content" in body


def test_key_comments_in_expandable(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "recent_comments": [
                {"author": "dev-a@example.com", "ts": "2026-05-22T14:08:00Z",
                 "text": "Looking at the HEVCChangeMonitor path."},
            ],
        },
    )
    body = client.get("/").text
    assert "key comment" in body.lower()
    assert "dev-a@example.com" in body
    assert "Looking at the HEVCChangeMonitor path" in body


def test_key_comments_filters_out_bots(triage_dir: Path) -> None:
    """Bot routing noise is dropped — section disappears if only bots."""
    write_draft(
        triage_dir, 1,
        bug_context={
            "recent_comments": [
                {"author": "release-mgmt-account-bot@mozilla.tld",
                 "ts": "2026-05-22T14:08:00Z",
                 "text": "Bugbug moved bug to Core::Audio/Video: Playback."},
            ],
        },
    )
    body = client.get("/").text
    assert "Bugbug moved" not in body
    # With only noise, the section should be omitted entirely.
    assert "key comment" not in body.lower()


def test_attachments_in_expandable(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "attachments": [{"name": "profile.json", "url": "https://example/p"}],
        },
    )
    body = client.get("/").text
    assert "Attachments" in body
    assert "profile.json" in body


def test_ai_reasoning_only_for_b1(triage_dir: Path) -> None:
    """AI reasoning expandable shows up only on §1b cards (root cause analysis)."""
    write_draft(
        triage_dir, 1, severity="S3", priority="P3",
        bug_context={"ai_reasoning": "Source: dom/media/...; line: 281."},
    )
    body = client.get("/").text
    assert "AI reasoning" in body
    assert "dom/media/" in body


# ─── "Changed since Awaiting" note ──────────────────────────────────

def test_change_note_renders_label_and_text(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1,
        bug_context={
            "change_note": "Reporter attached a media log → re-triaged §1b",
        },
    )
    body = client.get("/").text
    assert "change-note" in body
    assert "Changed since Awaiting" in body
    assert "Reporter attached a media log" in body


def test_change_note_absent_when_empty(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"change_note": ""})
    body = client.get("/").text
    assert "change-note" not in body
    assert "Changed since Awaiting" not in body


def test_change_note_absent_when_not_set(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"platform": "Linux"})
    body = client.get("/").text
    assert "change-note" not in body
    assert "Changed since Awaiting" not in body


# ─── graceful absence ───────────────────────────────────────────────

def test_card_without_bug_context_has_no_byline_or_more_sections(
    triage_dir: Path,
) -> None:
    """Drafts without bug_context render as plain — none of the new sections appear."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    body = client.get("/").text
    assert "card-byline" not in body
    assert "see-also-pill" not in body
    assert "Bug description" not in body
    assert "Attachments" not in body
