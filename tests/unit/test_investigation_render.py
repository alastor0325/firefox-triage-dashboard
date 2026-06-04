"""Unit tests for strip_frontmatter — the pure body extractor the
/investigation/<id> route uses to render the local investigation markdown
(the YAML frontmatter must not leak into the rendered page)."""

from __future__ import annotations

from triage_dashboard import data


def test_strips_frontmatter_returns_body() -> None:
    text = "---\nbug_id: 1\nstatus: investigated\n---\n# Title\n\nbody line\n"
    assert data.strip_frontmatter(text) == "# Title\n\nbody line\n"


def test_no_frontmatter_returns_unchanged() -> None:
    text = "# Title\n\nno frontmatter here\n"
    assert data.strip_frontmatter(text) == text


def test_missing_closing_fence_returns_unchanged() -> None:
    # opens with --- but never closes → malformed; don't silently eat the body
    text = "---\nbug_id: 1\n# Title\nbody\n"
    assert data.strip_frontmatter(text) == text


def test_empty_string_returns_empty() -> None:
    assert data.strip_frontmatter("") == ""


def test_body_preserved_verbatim_including_no_trailing_newline() -> None:
    text = "---\na: 1\n---\nline1\nline2"
    assert data.strip_frontmatter(text) == "line1\nline2"
