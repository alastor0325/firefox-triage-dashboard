"""Unit tests for triage_dashboard.data — pure JSON loaders + classification."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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

def _expected_from_mtime(mtime: float) -> str:
    from datetime import datetime as _dt
    return _dt.fromtimestamp(mtime).astimezone().strftime(
        "%b %-d, %Y %H:%M %Z").strip()


def test_last_updated_dateline_no_pending_dir(tmp_path: Path) -> None:
    # tmp_path has no pending/ subdir.
    assert data.last_updated_dateline(tmp_path) == "no drafts yet"


def test_last_updated_dateline_empty_pending(triage_dir: Path) -> None:
    # triage_dir fixture creates an empty pending/.
    assert data.last_updated_dateline(triage_dir) == "no drafts yet"


def test_last_updated_dateline_uses_newest_file_mtime(triage_dir: Path) -> None:
    import os
    p1 = write_draft(triage_dir, 1)
    p2 = write_draft(triage_dir, 2)
    # Force known, distinct mtimes: p1 older, p2 newer.
    old = 1_700_000_000.0   # 2023-11-14
    new = 1_780_000_000.0   # 2026-05-29
    os.utime(p1, (old, old))
    os.utime(p2, (new, new))
    result = data.last_updated_dateline(triage_dir)
    assert result == _expected_from_mtime(new)
    assert result != _expected_from_mtime(old)


def test_last_updated_dateline_ignores_created_at_field(triage_dir: Path) -> None:
    import os
    # A fabricated midnight created_at must NOT be what's shown — mtime wins.
    p = write_draft(triage_dir, 7, created_at="2020-01-01T00:00:00Z")
    mt = 1_780_000_000.0
    os.utime(p, (mt, mt))
    result = data.last_updated_dateline(triage_dir)
    assert result == _expected_from_mtime(mt)
    assert "2020" not in result   # the bogus created_at year never appears


def test_last_updated_dateline_is_precise(triage_dir: Path) -> None:
    write_draft(triage_dir, 1)
    result = data.last_updated_dateline(triage_dir)
    # A real timestamp: has a year and an HH:MM time, not 'Weekday, Month Day'.
    assert ":" in result
    assert any(ch.isdigit() for ch in result)


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


def test_group_by_section_sorts_newest_filed_first() -> None:
    def mk(bug_id, filed):
        return data.Draft(
            bug_id=bug_id, title="", comment="", ni_targets=[], priority="P3",
            severity="S3", blocks_add=[], cc_add=[], resolution=None,
            keywords_add=[], product=None, component=None, created_at="",
            section="§1b", bug_context=data.BugContext(filed=filed),
        )
    # deliberately out of order; newest filed should come first
    drafts = [
        mk(1, "2026-05-10T00:00:00Z"),   # oldest
        mk(2, "2026-05-31T00:00:00Z"),   # newest
        mk(3, "2026-05-20T00:00:00Z"),   # middle
    ]
    groups = data.group_by_section(drafts)
    assert [d.bug_id for d in groups["§1b"]] == [2, 3, 1]


def test_group_by_section_missing_filed_sorts_last() -> None:
    def mk(bug_id, filed):
        return data.Draft(
            bug_id=bug_id, title="", comment="", ni_targets=[], priority="P3",
            severity="S3", blocks_add=[], cc_add=[], resolution=None,
            keywords_add=[], product=None, component=None, created_at="",
            section="§1b", bug_context=data.BugContext(filed=filed),
        )
    drafts = [
        mk(10, ""),                       # no filed → last
        mk(11, "2026-05-15T00:00:00Z"),   # has filed → first
        mk(12, ""),                       # no filed → after 11, by bug_id desc
    ]
    groups = data.group_by_section(drafts)
    assert [d.bug_id for d in groups["§1b"]] == [11, 12, 10]


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


def test_load_watch_list_with_non_dict_entries_skipped(triage_dir: Path) -> None:
    """List entries that aren't dicts (e.g. a bare int or a string left
    over from a hand-edit) are silently skipped — the dashboard must
    never 500 because of a stray entry."""
    payload = [
        {"bug_id": 9999, "title": "x", "ni_targets": [], "added_at": ""},
        42,
        "stringly typed",
        None,
        {"bug_id": 8888, "title": "y", "ni_targets": [], "added_at": ""},
    ]
    (triage_dir / "ni-watch.json").write_text(json.dumps(payload))
    watch = data.load_watch(triage_dir)
    assert [w.bug_id for w in watch] == [9999, 8888]


def test_load_watch_dict_with_non_dict_values_skipped(triage_dir: Path) -> None:
    """Dict format: values that aren't dicts (truncated writes, hand-edits)
    are silently skipped — same robustness as the list path."""
    payload = {
        "9999": {"title": "good", "ni_targets": [], "added_at": ""},
        "8888": "not a dict — bad hand-edit",
        "7777": {"title": "also good", "ni_targets": [], "added_at": ""},
    }
    (triage_dir / "ni-watch.json").write_text(json.dumps(payload))
    watch = data.load_watch(triage_dir)
    bug_ids = sorted(w.bug_id for w in watch)
    assert bug_ids == [7777, 9999]


def test_load_watch_top_level_scalar_returns_empty(triage_dir: Path) -> None:
    """A scalar root (e.g. a number or a bare string) is neither a list
    nor a dict — returning empty rather than crashing keeps the page up."""
    (triage_dir / "ni-watch.json").write_text(json.dumps(42))
    assert data.load_watch(triage_dir) == []


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


# ─── dupe_of (DUPLICATE resolution target) ─────────────────────────


def test_draft_dupe_of_absent_is_none(triage_dir: Path) -> None:
    """Drafts written before dupe_of existed (or non-DUPLICATE drafts) load with None."""
    write_draft(triage_dir, 1, resolution="INCOMPLETE")
    assert data.load_drafts(triage_dir)[0].dupe_of is None


def test_draft_dupe_of_parses_int(triage_dir: Path) -> None:
    write_draft(triage_dir, 2042320, resolution="DUPLICATE", dupe_of=1711812)
    assert data.load_drafts(triage_dir)[0].dupe_of == 1711812


def test_draft_dupe_of_parses_string_digits(triage_dir: Path) -> None:
    """Some skill writers may stringify the bug number — accept either."""
    write_draft(triage_dir, 1, resolution="DUPLICATE", dupe_of="1711812")
    assert data.load_drafts(triage_dir)[0].dupe_of == 1711812


def test_draft_dupe_of_malformed_falls_back_to_none(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, resolution="DUPLICATE", dupe_of="not-a-bug")
    assert data.load_drafts(triage_dir)[0].dupe_of is None


def test_draft_dupe_of_zero_or_negative_is_none(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, resolution="DUPLICATE", dupe_of=0)
    assert data.load_drafts(triage_dir)[0].dupe_of is None


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


# ─── load_investigation (bug-start YAML frontmatter) ───────────────────

def _write_investigation(
    investigation_dir: Path, bug_id: int, content: str
) -> Path:
    """Helper: write a bug-N-investigation.md and return its path."""
    investigation_dir.mkdir(parents=True, exist_ok=True)
    path = investigation_dir / f"bug-{bug_id}-investigation.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_investigation_missing_file_returns_none(tmp_path: Path) -> None:
    assert data.load_investigation(2042320, investigation_dir=tmp_path) is None


def test_load_investigation_no_frontmatter_returns_shell(tmp_path: Path) -> None:
    """File present but no `---` frontmatter → return a shell Investigation
    with bug_id and file_path populated so the card can still link to it."""
    path = _write_investigation(
        tmp_path, 2042320, "# Bug 2042320 Investigation\n\nSome notes.\n"
    )
    inv = data.load_investigation(2042320, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 2042320
    assert inv.file_path == str(path.resolve())
    # All other fields default to their empty values.
    assert inv.investigated_at == ""
    assert inv.status == ""
    assert inv.root_cause == ""
    assert inv.affected_files == []
    assert inv.regression_range is None
    assert inv.related_bugs == []
    assert inv.complexity == ""
    assert inv.notes == ""


def test_load_investigation_full_frontmatter(tmp_path: Path) -> None:
    path = _write_investigation(
        tmp_path, 2042320,
        "---\n"
        "bug_id: 2042320\n"
        "investigated_at: 2026-05-30T20:15:00Z\n"
        "status: investigated\n"
        "root_cause: VideoUtils mis-maps HEVC lack-of-extension state\n"
        "affected_files:\n"
        "  - dom/media/platforms/VideoUtils.cpp\n"
        "  - dom/media/platforms/wmf/WMFDecoderModule.cpp\n"
        "regression_range: abc12345-def67890\n"
        "related_bugs: [1992187, 2038494]\n"
        "complexity: medium\n"
        'notes: "needs WPT update"\n'
        "---\n"
        "# Bug 2042320 Investigation\n"
        "Body content here.\n"
    )
    inv = data.load_investigation(2042320, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 2042320
    assert inv.investigated_at == "2026-05-30T20:15:00Z"
    assert inv.status == "investigated"
    assert inv.root_cause == "VideoUtils mis-maps HEVC lack-of-extension state"
    assert inv.affected_files == [
        "dom/media/platforms/VideoUtils.cpp",
        "dom/media/platforms/wmf/WMFDecoderModule.cpp",
    ]
    assert inv.regression_range == "abc12345-def67890"
    assert inv.related_bugs == [1992187, 2038494]
    assert inv.complexity == "medium"
    assert inv.notes == "needs WPT update"
    assert inv.file_path == str(path.resolve())


def test_load_investigation_partial_frontmatter_defaults(tmp_path: Path) -> None:
    """Frontmatter missing most fields still loads — defaults apply."""
    _write_investigation(
        tmp_path, 42,
        "---\n"
        "bug_id: 42\n"
        "status: blocked\n"
        "---\n"
        "# body\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.status == "blocked"
    assert inv.root_cause == ""
    assert inv.affected_files == []
    assert inv.related_bugs == []
    assert inv.regression_range is None
    assert inv.complexity == ""
    assert inv.notes == ""


def test_load_investigation_malformed_yaml_returns_shell(tmp_path: Path) -> None:
    """Malformed YAML between `---` markers is treated like missing
    frontmatter: return a shell so we can still link to the file."""
    path = _write_investigation(
        tmp_path, 42,
        "---\n"
        "bug_id: 42\n"
        "  not: valid: yaml: at: all: : :\n"
        " - broken\n"
        "---\n"
        "# body\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.file_path == str(path.resolve())
    assert inv.status == ""
    assert inv.affected_files == []


def test_load_investigation_empty_lists_parse(tmp_path: Path) -> None:
    _write_investigation(
        tmp_path, 42,
        "---\n"
        "bug_id: 42\n"
        "affected_files: []\n"
        "related_bugs: []\n"
        "---\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.affected_files == []
    assert inv.related_bugs == []


def test_load_investigation_regression_null_is_none(tmp_path: Path) -> None:
    _write_investigation(
        tmp_path, 42,
        "---\n"
        "bug_id: 42\n"
        "regression_range: null\n"
        "---\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.regression_range is None


def test_load_investigation_file_path_is_absolute(tmp_path: Path) -> None:
    path = _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\n---\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.file_path == str(path.resolve())
    assert Path(inv.file_path).is_absolute()


def test_load_investigation_unclosed_frontmatter_returns_shell(
    tmp_path: Path,
) -> None:
    """A `---` opener without a closing fence is treated like missing
    frontmatter: return a shell so the GitHub link still renders."""
    path = _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\nstatus: investigated\n# never closed\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.file_path == str(path.resolve())
    assert inv.status == ""


def test_load_investigation_no_opening_delim_returns_shell(
    tmp_path: Path,
) -> None:
    """File doesn't start with `---\\n` → no parseable frontmatter, but
    the file still exists, so return a shell."""
    path = _write_investigation(
        tmp_path, 42,
        "\n---\nbug_id: 42\n---\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.file_path == str(path.resolve())


def test_load_investigation_env_var_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIREFOX_INVESTIGATION_DIR overrides the default location when no
    explicit directory is passed."""
    _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\nstatus: investigated\n---\n"
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(tmp_path))
    inv = data.load_investigation(42)
    assert inv is not None
    assert inv.status == "investigated"


def test_load_investigation_yaml_not_dict_returns_shell(tmp_path: Path) -> None:
    """If the frontmatter parses to a scalar/list instead of a dict, treat
    it the same as malformed — return a shell."""
    path = _write_investigation(
        tmp_path, 42,
        "---\njust a string\n---\n"
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.file_path == str(path.resolve())


def test_investigation_dir_from_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FIREFOX_INVESTIGATION_DIR", raising=False)
    assert data.investigation_dir_from_env() == data.DEFAULT_INVESTIGATION_DIR


# ─── load_investigation lock-file handling (bug-start in-flight) ──────


def _touch_lock(investigation_dir: Path, bug_id: int) -> Path:
    """Helper: create a fresh `bug-<id>-investigating.lock` file."""
    investigation_dir.mkdir(parents=True, exist_ok=True)
    path = investigation_dir / f"bug-{bug_id}-investigating.lock"
    path.write_text("", encoding="utf-8")
    return path


def test_load_investigation_lock_fresh_no_md_returns_investigating(
    tmp_path: Path,
) -> None:
    """Lock file present, no md → status='investigating', file_path=''."""
    _touch_lock(tmp_path, 42)
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.status == "investigating"
    assert inv.file_path == ""


def test_load_investigation_lock_fresh_with_md_overrides_status(
    tmp_path: Path,
) -> None:
    """Fresh lock overrides whatever frontmatter says; md presence ignored
    for status, but file_path can point at the md."""
    _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\nstatus: investigated\n---\n# body\n",
    )
    _touch_lock(tmp_path, 42)
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.status == "investigating"
    assert inv.bug_id == 42
    # The actual root_cause / affected_files from the (possibly stale) md
    # are not surfaced — we only know we're "investigating".
    assert inv.root_cause == ""


def test_load_investigation_lock_stale_returns_investigation_stalled(
    tmp_path: Path,
) -> None:
    """Lock mtime > 30 min ago → status='investigation-stalled'."""
    lock = _touch_lock(tmp_path, 42)
    # Set mtime to 31 minutes ago.
    old_ts = __import__("time").time() - (31 * 60)
    import os as _os
    _os.utime(lock, (old_ts, old_ts))
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.bug_id == 42
    assert inv.status == "investigation-stalled"


def test_load_investigation_lock_stale_file_path_md_when_present(
    tmp_path: Path,
) -> None:
    """Stale lock + md present → file_path points at the md file."""
    md_path = _write_investigation(
        tmp_path, 42, "---\nbug_id: 42\n---\n# body\n",
    )
    lock = _touch_lock(tmp_path, 42)
    old_ts = __import__("time").time() - (31 * 60)
    import os as _os
    _os.utime(lock, (old_ts, old_ts))
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.status == "investigation-stalled"
    assert inv.file_path == str(md_path.resolve())


def test_load_investigation_lock_stale_file_path_lock_when_no_md(
    tmp_path: Path,
) -> None:
    """Stale lock without an md → file_path points at the lock file."""
    lock = _touch_lock(tmp_path, 42)
    old_ts = __import__("time").time() - (31 * 60)
    import os as _os
    _os.utime(lock, (old_ts, old_ts))
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.status == "investigation-stalled"
    assert inv.file_path == str(lock.resolve())


def test_load_investigation_no_lock_existing_behavior_with_md(
    tmp_path: Path,
) -> None:
    """No lock, md present with frontmatter → frontmatter status applies."""
    _write_investigation(
        tmp_path, 42, "---\nbug_id: 42\nstatus: blocked\n---\n# body\n",
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.status == "blocked"


def test_load_investigation_no_lock_no_md_returns_none(tmp_path: Path) -> None:
    assert data.load_investigation(42, investigation_dir=tmp_path) is None


def test_load_investigation_depth_triage_parsed(tmp_path: Path) -> None:
    """Frontmatter `depth: triage` populates Investigation.depth."""
    _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\nstatus: investigated\ndepth: triage\n---\n",
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.depth == "triage"


def test_load_investigation_depth_default_empty(tmp_path: Path) -> None:
    """Frontmatter without `depth` → Investigation.depth defaults to empty
    (i.e. deep / not set)."""
    _write_investigation(
        tmp_path, 42,
        "---\nbug_id: 42\nstatus: investigated\n---\n",
    )
    inv = data.load_investigation(42, investigation_dir=tmp_path)
    assert inv is not None
    assert inv.depth == ""


# ─── parse_affected_file (path#Lnnn line-anchor convention) ───────────

def test_parse_affected_file_bare_path() -> None:
    """Path with no `#L` suffix → bare path; URL points at the whole file."""
    p = data.parse_affected_file("dom/media/MediaDecoder.cpp")
    assert p == {
        "path": "dom/media/MediaDecoder.cpp",
        "line_start": None,
        "line_end": None,
        "display": "dom/media/MediaDecoder.cpp",
        "url_suffix": "",
    }


def test_parse_affected_file_single_line() -> None:
    """`path#L42` → display reads `path:42`, URL anchors at `#42`."""
    p = data.parse_affected_file("dom/media/MediaDecoder.cpp#L42")
    assert p["path"] == "dom/media/MediaDecoder.cpp"
    assert p["line_start"] == 42
    assert p["line_end"] is None
    assert p["display"] == "dom/media/MediaDecoder.cpp:42"
    assert p["url_suffix"] == "#42"


def test_parse_affected_file_range() -> None:
    """`path#L42-L50` → display reads `path:42-50`, URL anchors at the
    start of the range (searchfox doesn't support range anchors)."""
    p = data.parse_affected_file("dom/media/MediaDecoder.cpp#L42-L50")
    assert p["path"] == "dom/media/MediaDecoder.cpp"
    assert p["line_start"] == 42
    assert p["line_end"] == 50
    assert p["display"] == "dom/media/MediaDecoder.cpp:42-50"
    assert p["url_suffix"] == "#42"


def test_parse_affected_file_empty_string() -> None:
    """Empty input is tolerated — returns a benign empty result so the
    template doesn't 500 on bad frontmatter."""
    p = data.parse_affected_file("")
    assert p["path"] == ""
    assert p["line_start"] is None
    assert p["display"] == ""
    assert p["url_suffix"] == ""


def test_parse_affected_file_whitespace_trimmed() -> None:
    """Leading/trailing whitespace is stripped — YAML lists can leak it."""
    p = data.parse_affected_file("  dom/media/MediaDecoder.cpp#L42  ")
    assert p["path"] == "dom/media/MediaDecoder.cpp"
    assert p["line_start"] == 42


# ─── is_regression / is_emergency / is_stalled (rail-tag helpers) ─────

def _draft_with_context(**ctx_kwargs) -> data.Draft:
    """Build a minimal Draft with a BugContext for tag-helper tests."""
    return data.Draft(
        bug_id=1, title="", comment="", ni_targets=[], priority=None,
        severity=None, blocks_add=[], cc_add=[], resolution=None,
        keywords_add=[], product=None, component=None, created_at="",
        section="§1a", bug_context=data.BugContext(**ctx_kwargs),
    )


def _draft_without_context() -> data.Draft:
    return data.Draft(
        bug_id=1, title="", comment="", ni_targets=[], priority=None,
        severity=None, blocks_add=[], cc_add=[], resolution=None,
        keywords_add=[], product=None, component=None, created_at="",
        section="§1a", bug_context=None,
    )


def test_is_regression_true_when_keyword_present() -> None:
    assert data.is_regression(_draft_with_context(keywords=["regression"])) is True


def test_is_regression_case_insensitive() -> None:
    assert data.is_regression(_draft_with_context(keywords=["Regression"])) is True
    assert data.is_regression(_draft_with_context(keywords=["REGRESSION"])) is True


def test_is_regression_false_when_keyword_absent() -> None:
    assert data.is_regression(_draft_with_context(keywords=["crash"])) is False
    assert data.is_regression(_draft_with_context(keywords=[])) is False


def test_is_regression_false_when_no_bug_context() -> None:
    assert data.is_regression(_draft_without_context()) is False


def test_is_emergency_true_for_sec_critical() -> None:
    assert data.is_emergency(_draft_with_context(keywords=["sec-critical"])) is True


def test_is_emergency_true_for_sec_high() -> None:
    assert data.is_emergency(_draft_with_context(keywords=["sec-high"])) is True


def test_is_emergency_true_for_topcrash() -> None:
    assert data.is_emergency(_draft_with_context(keywords=["topcrash"])) is True


def test_is_emergency_case_insensitive() -> None:
    assert data.is_emergency(_draft_with_context(keywords=["Sec-Critical"])) is True
    assert data.is_emergency(_draft_with_context(keywords=["TOPCRASH"])) is True


_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_is_new_this_week_true_when_filed_recently() -> None:
    d = _draft_with_context(filed="2026-05-30T12:00:00Z")  # 2 days ago
    assert data.is_new_this_week(d, now=_NOW) is True


def test_is_new_this_week_true_at_7_day_boundary() -> None:
    d = _draft_with_context(filed="2026-05-25T12:00:00Z")  # exactly 7 days
    assert data.is_new_this_week(d, now=_NOW) is True


def test_is_new_this_week_false_when_older_than_7_days() -> None:
    d = _draft_with_context(filed="2026-05-20T12:00:00Z")  # 12 days ago
    assert data.is_new_this_week(d, now=_NOW) is False


def test_is_new_this_week_false_when_filed_missing() -> None:
    assert data.is_new_this_week(_draft_with_context(filed=""), now=_NOW) is False
    # filed not provided at all → defaults to ""
    assert data.is_new_this_week(_draft_with_context(keywords=[]), now=_NOW) is False


def test_is_new_this_week_false_when_filed_unparseable() -> None:
    d = _draft_with_context(filed="not-a-date")
    assert data.is_new_this_week(d, now=_NOW) is False


def test_is_new_this_week_false_when_no_bug_context() -> None:
    assert data.is_new_this_week(_draft_without_context(), now=_NOW) is False


def test_is_new_this_week_drops_when_now_advances() -> None:
    # Same draft, two different 'now's: the tag is purely render-time —
    # new today, not new two weeks later.
    d = _draft_with_context(filed="2026-05-30T12:00:00Z")
    assert data.is_new_this_week(d, now=_NOW) is True
    later = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    assert data.is_new_this_week(d, now=later) is False


def test_bug_context_parses_filed(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"filed": "2026-05-30T12:00:00Z"})
    drafts = data.load_drafts(triage_dir)
    assert drafts[0].bug_context.filed == "2026-05-30T12:00:00Z"


def test_bug_context_parses_affected_versions(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"affected_versions": "151+"})
    drafts = data.load_drafts(triage_dir)
    assert drafts[0].bug_context.affected_versions == "151+"


def test_bug_context_affected_versions_defaults_empty(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, bug_context={"firefox_version": "150.0"})
    drafts = data.load_drafts(triage_dir)
    assert drafts[0].bug_context.affected_versions == ""


def test_is_emergency_false_for_unrelated_keywords() -> None:
    assert data.is_emergency(_draft_with_context(keywords=["regression"])) is False
    assert data.is_emergency(_draft_with_context(keywords=["crash"])) is False
    assert data.is_emergency(_draft_with_context(keywords=[])) is False


def test_is_emergency_false_when_no_bug_context() -> None:
    assert data.is_emergency(_draft_without_context()) is False


def _watch_entry(added_at: str) -> data.WatchEntry:
    return data.WatchEntry(bug_id=1, title="", ni_targets=[], added_at=added_at)


def test_is_stalled_true_when_added_at_15_days_ago() -> None:
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    fifteen_days_ago = (now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data.is_stalled(_watch_entry(fifteen_days_ago), now=now) is True


def test_is_stalled_false_when_added_at_13_days_ago() -> None:
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    thirteen_days_ago = (now - timedelta(days=13)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data.is_stalled(_watch_entry(thirteen_days_ago), now=now) is False


def test_is_stalled_false_at_exactly_14_days() -> None:
    """Boundary: exactly 14 days is not 'more than 14' — falsy."""
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    fourteen_days_ago = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data.is_stalled(_watch_entry(fourteen_days_ago), now=now) is False


def test_is_stalled_false_when_added_at_empty() -> None:
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert data.is_stalled(_watch_entry(""), now=now) is False


def test_is_stalled_false_when_added_at_malformed() -> None:
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert data.is_stalled(_watch_entry("not-a-date"), now=now) is False


def test_is_stalled_accepts_date_only_format() -> None:
    """`added_at` is sometimes a bare YYYY-MM-DD without a time component."""
    now = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert data.is_stalled(_watch_entry("2026-05-10"), now=now) is True
    assert data.is_stalled(_watch_entry("2026-05-25"), now=now) is False


def test_is_stalled_defaults_now_to_current_utc() -> None:
    """Without an explicit `now`, the helper uses datetime.now(UTC).
    A date 60 days in the past should always be stalled."""
    sixty_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=60)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert data.is_stalled(_watch_entry(sixty_days_ago)) is True
