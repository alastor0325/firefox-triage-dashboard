"""Unit tests for triage_dashboard.descriptions — bug-description parser."""

from __future__ import annotations

from triage_dashboard import descriptions as desc


# ─── parse_description ──────────────────────────────────────────────

def test_parse_empty_string() -> None:
    assert desc.parse_description("") == {}


def test_parse_pure_intro_no_sections() -> None:
    """Free-form description with no section labels — everything is notes."""
    out = desc.parse_description("Just a description with no structure.")
    assert out == {"notes": "Just a description with no structure."}


def test_parse_standard_three_sections() -> None:
    text = (
        "Steps to reproduce: visit https://example.com and click play. "
        "Actual results: video does not play. "
        "Expected results: video should play."
    )
    out = desc.parse_description(text)
    assert "steps" in out and "Actual results" not in out["steps"]
    assert "visit https://example.com and click play" in out["steps"]
    assert out["actual"] == "video does not play."
    assert out["expected"] == "video should play."


def test_parse_strips_user_agent_prefix() -> None:
    text = (
        "User Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) "
        "Gecko/20100101 Firefox/151.0. "
        "The actual bug description starts here."
    )
    out = desc.parse_description(text)
    # UA should not be in any section
    for v in out.values():
        assert "Mozilla/5.0" not in v
        assert "Gecko/20100101" not in v
    assert "The actual bug description starts here." in out.get("notes", "")


def test_parse_intro_before_steps_kept_as_intro() -> None:
    text = (
        "Some context paragraph. Steps to reproduce: 1. Do thing. "
        "Actual results: bad. Expected results: good."
    )
    out = desc.parse_description(text)
    assert out["intro"] == "Some context paragraph."
    assert "Do thing" in out["steps"]


def test_parse_handles_case_variations() -> None:
    """'STEPS TO REPRODUCE' / 'Actual Result' (no s) should also work."""
    text = "STEPS TO REPRODUCE: open page. Actual Result: crash. Expected Result: no crash."
    out = desc.parse_description(text)
    assert "open page" in out.get("steps", "")
    assert out.get("actual", "").startswith("crash")
    assert out.get("expected", "").startswith("no crash")


def test_parse_handles_only_steps() -> None:
    """Some bugs only have a Steps section, no Actual/Expected."""
    text = "Steps to reproduce: do the thing and observe."
    out = desc.parse_description(text)
    assert "do the thing and observe" in out["steps"]
    assert "actual" not in out
    assert "expected" not in out


# ─── linkify ────────────────────────────────────────────────────────

def test_linkify_url_becomes_anchor() -> None:
    out = desc.linkify("Visit https://example.com/page for details.")
    assert '<a href="https://example.com/page"' in str(out)
    assert 'target="_blank"' in str(out)
    assert 'rel="noopener"' in str(out)


def test_linkify_escapes_html() -> None:
    """HTML in the input must be escaped to prevent XSS."""
    out = str(desc.linkify("Watch out: <script>alert(1)</script>"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_linkify_handles_no_url() -> None:
    assert "https://" not in str(desc.linkify("Plain text only."))


def test_linkify_empty_string() -> None:
    assert str(desc.linkify("")) == ""


def test_linkify_multiple_urls() -> None:
    out = str(desc.linkify(
        "See https://example.com/a and https://example.com/b for details."
    ))
    assert out.count('<a href=') == 2


def test_linkify_url_at_end_no_trailing_punct() -> None:
    """A trailing period after a URL must not become part of the link."""
    out = str(desc.linkify("Repro: https://example.com/page."))
    assert 'href="https://example.com/page"' in out
    # The period should be outside the anchor tag.
    assert "page</a>." in out or "page</a> " in out


# ─── render_markdown ────────────────────────────────────────────────

def test_render_markdown_paragraph() -> None:
    out = str(desc.render_markdown("Plain paragraph text."))
    assert "<p>Plain paragraph text.</p>" in out


def test_render_markdown_inline_code() -> None:
    out = str(desc.render_markdown("Use `foo()` for that."))
    assert "<code>foo()</code>" in out


def test_render_markdown_bold_and_italic() -> None:
    out = str(desc.render_markdown("This is **bold** and *italic*."))
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_render_markdown_bulleted_list() -> None:
    out = str(desc.render_markdown("- one\n- two\n- three"))
    assert "<ul>" in out
    assert "<li>one</li>" in out
    assert "<li>three</li>" in out


def test_render_markdown_numbered_list() -> None:
    out = str(desc.render_markdown("1. first\n2. second"))
    assert "<ol>" in out
    assert "<li>first</li>" in out


def test_render_markdown_inline_link() -> None:
    out = str(desc.render_markdown("See [docs](https://example.com) here."))
    assert 'href="https://example.com"' in out
    assert ">docs</a>" in out


def test_render_markdown_empty_input() -> None:
    assert str(desc.render_markdown("")) == ""


def test_render_markdown_blocks_raw_html() -> None:
    """Raw HTML in the input must be escaped, not passed through."""
    out = str(desc.render_markdown("Watch <script>alert(1)</script> out."))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_markdown_blocks_raw_html_via_attribute() -> None:
    """Even via attributes (e.g. <img onerror=...>), no raw HTML survives."""
    out = str(desc.render_markdown('<img src=x onerror="alert(1)">'))
    assert "<img" not in out.lower() or "onerror" not in out.lower()
