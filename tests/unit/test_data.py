"""Unit tests for triage_dashboard.data — pure JSON loaders + classification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import write_draft
from triage_dashboard import data


# ─── classify_section ────────────────────────────────────────────────

def test_classify_section_1a_when_only_ni() -> None:
    """A bare needinfo draft with no P/S and no resolution is §1a."""
    assert data.classify_section({"ni_targets": ["x@y"]}) == "§1a"


def test_classify_section_1b_when_severity_set() -> None:
    assert data.classify_section({"severity": "S3"}) == "§1b"


def test_classify_section_1b_when_priority_set() -> None:
    assert data.classify_section({"priority": "P3"}) == "§1b"


def test_classify_section_1c_when_resolution_set() -> None:
    """Resolution overrides any P/S — INCOMPLETE/FIXED is §1c regardless."""
    assert (
        data.classify_section({"resolution": "INCOMPLETE", "severity": "S3"})
        == "§1c"
    )


def test_classify_section_1c_when_component_reassigned() -> None:
    assert data.classify_section({"component": "Widget: Gtk"}) == "§1c"


def test_classify_section_1c_when_product_reassigned() -> None:
    assert data.classify_section({"product": "Core"}) == "§1c"


# ─── load_drafts ────────────────────────────────────────────────────

def test_load_drafts_empty_dir(triage_dir: Path) -> None:
    assert data.load_drafts(triage_dir) == []


def test_load_drafts_missing_pending_dir(tmp_path: Path) -> None:
    """No pending/ subdirectory at all → empty result, not an error."""
    assert data.load_drafts(tmp_path) == []


def test_load_drafts_reads_one(triage_dir: Path) -> None:
    write_draft(triage_dir, 12345, title="hello", severity="S3", priority="P3")
    drafts = data.load_drafts(triage_dir)
    assert len(drafts) == 1
    assert drafts[0].bug_id == 12345
    assert drafts[0].title == "hello"
    assert drafts[0].section == "§1b"


def test_load_drafts_skips_malformed(triage_dir: Path) -> None:
    """Files that aren't valid JSON are silently skipped, not crashed on."""
    write_draft(triage_dir, 11111)
    (triage_dir / "pending" / "bug-22222.json").write_text("{ not json")
    drafts = data.load_drafts(triage_dir)
    bug_ids = sorted(d.bug_id for d in drafts)
    assert bug_ids == [11111]


def test_load_drafts_sorted_by_bug_id(triage_dir: Path) -> None:
    write_draft(triage_dir, 30000)
    write_draft(triage_dir, 10000)
    write_draft(triage_dir, 20000)
    drafts = data.load_drafts(triage_dir)
    # sorted() on path names yields 10000 < 20000 < 30000 alphabetically too.
    assert [d.bug_id for d in drafts] == [10000, 20000, 30000]


# ─── group_by_section ──────────────────────────────────────────────

def test_group_by_section_buckets_all_three() -> None:
    drafts = [
        data.Draft(
            bug_id=1, title="a", comment="", ni_targets=[], priority="P3",
            severity="S3", blocks_add=[], cc_add=[], resolution=None,
            keywords_add=[], product=None, component=None, created_at="",
            section="§1b",
        ),
        data.Draft(
            bug_id=2, title="b", comment="", ni_targets=["x@y"], priority=None,
            severity=None, blocks_add=[], cc_add=[], resolution=None,
            keywords_add=[], product=None, component=None, created_at="",
            section="§1a",
        ),
        data.Draft(
            bug_id=3, title="c", comment="", ni_targets=[], priority=None,
            severity=None, blocks_add=[], cc_add=[], resolution="INCOMPLETE",
            keywords_add=[], product=None, component=None, created_at="",
            section="§1c",
        ),
    ]
    groups = data.group_by_section(drafts)
    assert [d.bug_id for d in groups["§1b"]] == [1]
    assert [d.bug_id for d in groups["§1a"]] == [2]
    assert [d.bug_id for d in groups["§1c"]] == [3]


def test_group_by_section_always_has_all_three_keys() -> None:
    """Empty input still returns all three keys with empty lists."""
    groups = data.group_by_section([])
    assert set(groups) == {"§1a", "§1b", "§1c"}
    assert all(v == [] for v in groups.values())


# ─── load_log ──────────────────────────────────────────────────────

def test_load_log_empty_when_file_missing(triage_dir: Path) -> None:
    assert data.load_log(triage_dir) == []


def test_load_log_returns_most_recent_first(triage_dir: Path) -> None:
    entries = [
        {"bug_id": i, "date": f"2026-05-{i:02d}", "decision": "ni_sent",
         "component": "", "reporter": "", "reason": "",
         "priority": None, "severity": None}
        for i in range(1, 6)
    ]
    (triage_dir / "triage-log.json").write_text(json.dumps(entries))
    log = data.load_log(triage_dir, limit=20)
    # Last entry written is the most recent; should appear first.
    assert [e.bug_id for e in log] == [5, 4, 3, 2, 1]


def test_load_log_respects_limit(triage_dir: Path) -> None:
    entries = [
        {"bug_id": i, "date": "", "decision": "ni_sent",
         "component": "", "reporter": "", "reason": "",
         "priority": None, "severity": None}
        for i in range(50)
    ]
    (triage_dir / "triage-log.json").write_text(json.dumps(entries))
    log = data.load_log(triage_dir, limit=5)
    assert len(log) == 5


def test_load_log_malformed_returns_empty(triage_dir: Path) -> None:
    (triage_dir / "triage-log.json").write_text("not json")
    assert data.load_log(triage_dir) == []


# ─── load_watch ────────────────────────────────────────────────────

def test_load_watch_empty_when_file_missing(triage_dir: Path) -> None:
    assert data.load_watch(triage_dir) == []


def test_load_watch_dict_format(triage_dir: Path) -> None:
    """ni-watch.json keyed by bug_id is supported."""
    payload = {
        "2039425": {
            "title": "WebCodecs crash",
            "ni_targets": ["alwu@mozilla.com"],
            "added_at": "2026-05-21",
        }
    }
    (triage_dir / "ni-watch.json").write_text(json.dumps(payload))
    watch = data.load_watch(triage_dir)
    assert len(watch) == 1
    assert watch[0].bug_id == 2039425
    assert watch[0].title == "WebCodecs crash"
    assert watch[0].ni_targets == ["alwu@mozilla.com"]


def test_load_watch_list_format(triage_dir: Path) -> None:
    """ni-watch.json as a list of entries is also supported."""
    payload = [{"bug_id": 9999, "title": "x", "ni_targets": [], "added_at": ""}]
    (triage_dir / "ni-watch.json").write_text(json.dumps(payload))
    watch = data.load_watch(triage_dir)
    assert len(watch) == 1 and watch[0].bug_id == 9999


# ─── compute_stats ─────────────────────────────────────────────────

def test_compute_stats_counts_everything() -> None:
    drafts = [
        data.Draft(bug_id=1, title="", comment="", ni_targets=[],
                   priority="P3", severity="S3", blocks_add=[], cc_add=[],
                   resolution=None, keywords_add=[], product=None,
                   component=None, created_at="", section="§1b"),
        data.Draft(bug_id=2, title="", comment="", ni_targets=["x"],
                   priority=None, severity=None, blocks_add=[], cc_add=[],
                   resolution=None, keywords_add=[], product=None,
                   component=None, created_at="", section="§1a"),
        data.Draft(bug_id=3, title="", comment="", ni_targets=["x"],
                   priority=None, severity=None, blocks_add=[], cc_add=[],
                   resolution=None, keywords_add=[], product=None,
                   component=None, created_at="", section="§1a"),
    ]
    watch = [data.WatchEntry(bug_id=42, title="", ni_targets=[], added_at="")]
    stats = data.compute_stats(drafts, watch)
    assert stats.pending_total == 3
    assert stats.pending_by_section == {"§1b": 1, "§1a": 2, "§1c": 0}
    assert stats.watching == 1


# ─── triage_dir_from_env ───────────────────────────────────────────

def test_triage_dir_from_env_uses_override(
    triage_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRIAGE_DIR", str(triage_dir))
    assert data.triage_dir_from_env() == triage_dir


def test_triage_dir_from_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIAGE_DIR", raising=False)
    assert data.triage_dir_from_env() == data.DEFAULT_TRIAGE_DIR


# ─── bug_context (optional rich context block) ─────────────────────

def test_draft_without_bug_context_loads(triage_dir: Path) -> None:
    """Drafts written before bug_context was a thing still load cleanly."""
    write_draft(triage_dir, 1)
    drafts = data.load_drafts(triage_dir)
    assert drafts[0].bug_context is None


def test_draft_with_full_bug_context(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 2039853,
        bug_context={
            "description_excerpt": "When playing HEVC content via DASH-LL...",
            "platform": "Windows 10 x64",
            "firefox_version": "150.0",
            "reporter_email": "ryan.mccartney@bbc.co.uk",
            "reporter_name": "Ryan McCartney",
            "last_activity": "2026-05-22T14:08:00Z",
            "inventory_present": ["platform / version", "three test URLs"],
            "inventory_missing": ["about:support", "media log"],
            "see_also": [
                {"bug_id": 1981503, "label": "regressor"},
                {"bug_id": 2012108, "label": "follow-up fix"},
            ],
            "recent_comments": [
                {"author": "jya@mozilla.com", "ts": "2026-05-22T14:08:00Z",
                 "text": "Looking at HEVCChangeMonitor path."},
            ],
            "attachments": [
                {"name": "profile.json", "url": "https://...", "size": 412000},
            ],
            "ai_reasoning": "",
        },
    )
    ctx = data.load_drafts(triage_dir)[0].bug_context
    assert ctx is not None
    assert ctx.platform == "Windows 10 x64"
    assert ctx.firefox_version == "150.0"
    assert ctx.reporter_name == "Ryan McCartney"
    assert ctx.inventory_present == ["platform / version", "three test URLs"]
    assert ctx.inventory_missing == ["about:support", "media log"]
    assert ctx.see_also == [
        {"bug_id": 1981503, "label": "regressor"},
        {"bug_id": 2012108, "label": "follow-up fix"},
    ]
    assert len(ctx.recent_comments) == 1
    assert ctx.attachments[0]["name"] == "profile.json"


def test_bug_context_partial_fields_default_empty(triage_dir: Path) -> None:
    """An incomplete bug_context shouldn't crash — missing fields default empty."""
    write_draft(
        triage_dir, 1,
        bug_context={"platform": "Linux", "firefox_version": "151.0"},
    )
    ctx = data.load_drafts(triage_dir)[0].bug_context
    assert ctx is not None
    assert ctx.platform == "Linux"
    assert ctx.reporter_name == ""
    assert ctx.inventory_present == []
    assert ctx.see_also == []


def test_bug_context_malformed_falls_back_to_none(triage_dir: Path) -> None:
    """If bug_context isn't a dict, load it as None rather than crashing."""
    write_draft(triage_dir, 1, bug_context="not a dict")
    assert data.load_drafts(triage_dir)[0].bug_context is None


# ─── bug_context.is_crash (rail tag heuristic) ─────────────────────

def test_is_crash_empty_context_is_false() -> None:
    assert data.BugContext().is_crash is False


def test_is_crash_crash_keyword_case_insensitive() -> None:
    assert data.BugContext(keywords=["crash"]).is_crash is True
    assert data.BugContext(keywords=["Crash"]).is_crash is True
    assert data.BugContext(keywords=["CRASH"]).is_crash is True


def test_is_crash_other_keywords_do_not_trigger() -> None:
    # 'crash-' or 'crashy' must not match; only the exact 'crash' keyword.
    assert data.BugContext(keywords=["regression", "perf"]).is_crash is False
    assert data.BugContext(keywords=["crashy"]).is_crash is False


def test_is_crash_socorro_id_in_description() -> None:
    desc = "Crashed once; bp-12345678-abcd-1234-5678-abcdef012345 above."
    assert data.BugContext(description_excerpt=desc).is_crash is True


def test_is_crash_bare_bp_dash_does_not_match() -> None:
    # 'bp-' must be followed by at least one hex/dash char to match.
    assert data.BugContext(description_excerpt="see bp- log").is_crash is False


# ─── level_class ─────────────────────────────────────────────────────

def test_level_class_recognised_severity() -> None:
    assert data.level_class("S1") == "s1"
    assert data.level_class("S4") == "s4"


def test_level_class_recognised_priority() -> None:
    assert data.level_class("P1") == "p1"
    assert data.level_class("P5") == "p5"


def test_level_class_missing_or_empty_is_unknown() -> None:
    assert data.level_class("") == "unknown"
    assert data.level_class(None) == "unknown"


def test_level_class_legacy_strings_fall_back_to_unknown() -> None:
    assert data.level_class("critical") == "unknown"
    assert data.level_class("normal") == "unknown"
    assert data.level_class("--") == "unknown"


def test_level_class_case_insensitive_and_trimmed() -> None:
    assert data.level_class("s3") == "s3"
    assert data.level_class(" P2 ") == "p2"


# ─── split_see_also ──────────────────────────────────────────────────

def test_split_see_also_empty_or_none() -> None:
    assert data.split_see_also(None) == ([], [])
    assert data.split_see_also([]) == ([], [])


def test_split_see_also_pulls_out_regressors() -> None:
    entries = [
        {"bug_id": 1, "label": "regressor"},
        {"bug_id": 2, "label": "same root cause"},
        {"bug_id": 3, "label": "Regressed by"},
    ]
    regressors, similar = data.split_see_also(entries)
    assert [e["bug_id"] for e in regressors] == [1, 3]
    assert [e["bug_id"] for e in similar] == [2]


def test_split_see_also_no_regressors() -> None:
    entries = [{"bug_id": 1, "label": "same root cause"}]
    regressors, similar = data.split_see_also(entries)
    assert regressors == []
    assert similar == entries


def test_split_see_also_entries_without_label_are_similar() -> None:
    entries = [{"bug_id": 1}, {"bug_id": 2, "label": ""}]
    regressors, similar = data.split_see_also(entries)
    assert regressors == []
    assert len(similar) == 2


def test_split_see_also_progression_is_not_regression() -> None:
    """Word-boundary match — labels that happen to contain 'gress' as
    a substring (progression, etc.) must not be mis-classified."""
    entries = [{"bug_id": 1, "label": "progression"}]
    regressors, similar = data.split_see_also(entries)
    assert regressors == []
    assert similar == entries
