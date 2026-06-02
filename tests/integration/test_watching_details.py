"""Integration tests for the folded report/findings on the Awaiting tab."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_applied_draft
from triage_dashboard.app import app


client = TestClient(app)


def _write_watch(triage_dir: Path, *entries: dict) -> None:
    (triage_dir / "ni-watch.json").write_text(json.dumps(list(entries)))


def _watch_item(html: str, bug_id: int) -> str:
    """Inner HTML of the watch-item <li> for the given bug_id."""
    m = re.search(
        rf'<li class="watch-item">(.*?(?:>{bug_id}<).*?)</li>',
        html,
        re.DOTALL,
    )
    assert m, f"watch-item for {bug_id} not found"
    return m.group(1)


def test_applied_archive_renders_folded_report(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    _write_watch(triage_dir, {"bug_id": 12345, "title": "Tracked bug"})
    write_applied_draft(
        triage_dir, 12345,
        comment="please do not show this comment",
        bug_context={"platform": "Windows 11 x64", "reporter_name": "Ada"},
    )
    item = _watch_item(client.get("/?tab=watching").text, 12345)
    # A collapsed <details class="watch-report"> with no `open` attribute.
    m = re.search(r'<details class="watch-report"([^>]*)>', item)
    assert m, "watch-report details not rendered"
    assert "open" not in m.group(1)
    assert "Details" in item
    # Report content is present.
    assert "Windows 11 x64" in item
    assert "Ada" in item


def test_investigation_findings_appear_folded(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    _write_watch(triage_dir, {"bug_id": 12345, "title": "Tracked bug"})
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    (inv_dir / "bug-12345-investigation.md").write_text(
        "---\n"
        "bug_id: 12345\n"
        "status: investigated\n"
        "root_cause: HEVC mapping table mis-identifies missing MFT\n"
        "---\n# body\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    item = _watch_item(client.get("/?tab=watching").text, 12345)
    assert '<details class="watch-report"' in item
    assert "Root cause:" in item
    assert "HEVC mapping table mis-identifies missing MFT" in item


def test_draft_comment_not_rendered(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    _write_watch(triage_dir, {"bug_id": 12345, "title": "Tracked bug"})
    write_applied_draft(
        triage_dir, 12345,
        comment="SECRET-DRAFT-COMMENT-do-not-show",
        bug_context={"platform": "Linux"},
    )
    body = client.get("/?tab=watching").text
    assert "SECRET-DRAFT-COMMENT-do-not-show" not in body


def test_applied_diff_and_current_sp_render(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    _write_watch(triage_dir, {"bug_id": 12345, "title": "Tracked bug"})
    write_applied_draft(
        triage_dir, 12345,
        comment="please do not show this comment",
        severity="S3",
        priority="P2",
        resolution="DUPLICATE",
        dupe_of=999,
        ni_targets=["dev@example.com"],
        blocks_add=[424242],
        bug_component="Audio/Video: Playback",
        bug_context={
            "platform": "Windows 11 x64",
            "current_severity": "S2",
            "current_priority": "P1",
        },
    )
    item = _watch_item(client.get("/?tab=watching").text, 12345)
    # Applied-diff content inside the fold.
    assert "S3" in item
    assert "dev@example.com" in item
    assert "DUPLICATE" in item
    assert "424242" in item
    # Component and current S/P (from bug_context) surfaced in the head.
    assert "Audio/Video: Playback" in item
    assert "badge-level" in item
    assert "S2" in item
    assert "P1" in item
    # Still collapsed by default and no interactive controls.
    m = re.search(r'<details class="watch-report"([^>]*)>', item)
    assert m and "open" not in m.group(1)
    assert "composer" not in item
    assert "Apply" not in item
    assert "please do not show this comment" not in item


def test_minimal_entry_without_archive_or_investigation(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    _write_watch(triage_dir, {"bug_id": 99999, "title": "Bare bug"})
    item = _watch_item(client.get("/?tab=watching").text, 99999)
    assert "watch-report" not in item
    assert "Bare bug" in item
