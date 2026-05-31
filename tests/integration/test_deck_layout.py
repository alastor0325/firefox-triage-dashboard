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


def test_rail_head_shows_info_icon_with_tab_specific_tooltip(
    triage_dir: Path,
) -> None:
    """Each draft tab's rail header carries an info icon. The tooltip
    text (data-tooltip attr) describes what that tab is for and what
    Apply will do."""
    # §1a (Needs Info)
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    body_a = client.get("/?tab=needs-info").text
    assert 'class="info-icon"' in body_a
    assert "needinfo" in body_a.lower()

    # §1b (Analyzed)
    write_draft(triage_dir, 2, severity="S3", priority="P3")
    body_b = client.get("/?tab=triaged&bug=2").text
    assert 'class="info-icon"' in body_b
    # Mentions bug-start since that's a §1b side effect
    assert "/bug-start" in body_b

    # §1c (Close / Reassign)
    write_draft(triage_dir, 3, resolution="INCOMPLETE")
    body_c = client.get("/?tab=close&bug=3").text
    assert 'class="info-icon"' in body_c
    assert "INCOMPLETE" in body_c or "resolution" in body_c.lower()


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

# ─── stable layout: rail / tabs don't re-swap on bug click ──────────

def test_rail_items_target_deck_area_not_full_tab_content(
    triage_dir: Path,
) -> None:
    """Bug-click must swap ONLY the deck-area so the rail stays mounted
    and the page doesn't reflow when cards have different heights."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    # Rail items target #deck-area, not #tab-content.
    import re
    rail_anchor = re.search(r'class="rail-item[^"]*"[^>]*', body)
    assert rail_anchor is not None
    snippet = rail_anchor.group(0)
    assert 'hx-target="#deck-area"' in snippet
    assert 'hx-target="#tab-content"' not in snippet


def test_deck_area_div_present(triage_dir: Path) -> None:
    """The dedicated swap target must exist."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    assert 'id="deck-area"' in body


def test_rail_host_div_present(triage_dir: Path) -> None:
    """The rail is wrapped in its own host so we can swap deck without it."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    assert 'id="rail-host"' in body


def test_rail_list_scroll_container_reserves_scrollbar_gutter(
    triage_dir: Path,
) -> None:
    """Whichever rail element actually scrolls must declare
    `scrollbar-gutter: stable` so list items don't shift left when the
    list grows past its column height.

    The scroll container lives BELOW the .rail-head so the info-icon
    tooltip can escape the rail's box (a non-`visible` overflow axis
    forces the other axis to clip too). We assert the
    .rail-list-scroll wrapper carries both `overflow-y: auto` and
    `scrollbar-gutter: stable`, and that `.rail` itself does NOT
    declare overflow-y."""
    import re
    from pathlib import Path as P
    css = (
        P(__file__).resolve().parent.parent.parent
        / "src" / "triage_dashboard" / "static" / "style.css"
    ).read_text()
    scroll = re.search(r'\.rail-list-scroll\s*\{[^}]*\}', css)
    assert scroll is not None, "no .rail-list-scroll rule found"
    assert "overflow-y: auto" in scroll.group(0)
    assert "scrollbar-gutter: stable" in scroll.group(0)
    rail = re.search(r'\.rail\s*\{[^}]*\}', css)
    assert rail is not None, "no .rail rule found"
    assert "overflow-y" not in rail.group(0), (
        ".rail must not set overflow-y — that would clip the info-icon "
        "tooltip. Put overflow on .rail-list-scroll instead."
    )


def test_rail_list_scroll_wrapper_is_in_dom(triage_dir: Path) -> None:
    """The scroll wrapper must actually wrap the <ol>, not just exist in
    CSS — otherwise the rule does nothing."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    import re
    # The wrapper exists and contains the <ol>.
    m = re.search(
        r'<div class="rail-list-scroll">\s*<ol>', body, re.DOTALL
    )
    assert m is not None, (
        "expected <div class='rail-list-scroll'><ol> wrapping the rail list"
    )


def test_deck_area_reserves_scrollbar_gutter(triage_dir: Path) -> None:
    """#deck-area must declare `scrollbar-gutter: stable` so card content
    doesn't shift sideways when switching between a tall card (scrollbar
    visible) and a short card (no scrollbar)."""
    import re
    from pathlib import Path as P
    css = (
        P(__file__).resolve().parent.parent.parent
        / "src" / "triage_dashboard" / "static" / "style.css"
    ).read_text()
    m = re.search(r'#deck-area\s*\{[^}]*\}', css)
    assert m is not None, "no #deck-area rule found"
    assert "scrollbar-gutter: stable" in m.group(0), (
        "#deck-area must use `scrollbar-gutter: stable`; without it the "
        "card shifts left when the scrollbar appears."
    )


def test_app_shell_locks_body_height(triage_dir: Path) -> None:
    """html and body must be height: 100vh + overflow: hidden so the page
    itself doesn't scroll — only the inner panes do. This is what keeps
    the topbar + tabs + rail anchored regardless of card content."""
    from pathlib import Path as P
    css = (
        P(__file__).resolve().parent.parent.parent
        / "src" / "triage_dashboard" / "static" / "style.css"
    ).read_text()
    assert "height: 100vh" in css and "overflow: hidden" in css, (
        "the app-shell layout requires html/body to be locked to 100vh "
        "with overflow: hidden so the page doesn't scroll as a whole."
    )


def test_prev_next_buttons_target_deck_area(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    write_draft(triage_dir, 3, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=2").text
    import re
    prev = re.search(r'class="[^"]*deck-prev[^"]*"[^>]*', body)
    next_ = re.search(r'class="[^"]*deck-next[^"]*"[^>]*', body)
    assert prev is not None and 'hx-target="#deck-area"' in prev.group(0)
    assert next_ is not None and 'hx-target="#deck-area"' in next_.group(0)


def test_deck_nav_has_exactly_one_pair_of_controls(triage_dir: Path) -> None:
    """The deck-nav used to render two duplicate pairs of prev/next
    controls — the .deck-nav-arrow buttons AND the .deck-kbd-btn pair.
    The arrow buttons are removed; only the kbd pair remains."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=1").text
    # No more arrow buttons.
    assert "deck-nav-arrow" not in body
    assert "← prev" not in body
    assert "next →" not in body
    # Exactly one <button class="… deck-prev …"> and one deck-next button
    # in the rendered HTML (each class also appears once in the keyboard
    # handler JS — those are not buttons, so we count by selector).
    import re
    assert len(re.findall(r'<button[^>]*class="[^"]*deck-prev', body)) == 1
    assert len(re.findall(r'<button[^>]*class="[^"]*deck-next', body)) == 1


def test_deck_nav_kbd_buttons_use_up_down_arrows(triage_dir: Path) -> None:
    """The kbd buttons render ↑ for prev and ↓ for next so they visually
    match the keyboard shortcut (ArrowUp/ArrowDown, also j/k)."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=1").text
    import re
    prev = re.search(r'class="[^"]*deck-prev[^"]*".*?</button>', body, re.DOTALL)
    next_ = re.search(r'class="[^"]*deck-next[^"]*".*?</button>', body, re.DOTALL)
    assert prev is not None and "↑" in prev.group(0)
    assert next_ is not None and "↓" in next_.group(0)


def test_deck_nav_kbd_buttons_target_deck_area(triage_dir: Path) -> None:
    """The kbd buttons (now the only nav control) must htmx-target the
    deck area so navigation is a partial swap, not a full page nav."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=1").text
    import re
    next_ = re.search(r'class="[^"]*deck-next[^"]*"[^>]*', body)
    assert next_ is not None
    assert 'hx-target="#deck-area"' in next_.group(0)
    assert 'hx-swap="outerHTML"' in next_.group(0)


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


# ─── deck-nav (position + prev/next) ────────────────────────────────

def test_deck_nav_shows_position_of_active_bug(triage_dir: Path) -> None:
    write_draft(triage_dir, 10, ni_targets=["x"])
    write_draft(triage_dir, 20, ni_targets=["x"])
    write_draft(triage_dir, 30, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=20").text
    # Sorted bucket [10, 20, 30] — bug 20 is at position 2.
    assert "2 of 3" in body or "2</strong> of <strong>3" in body


def test_deck_nav_prev_disabled_on_first_bug(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=1").text
    # Prev should be present but disabled (so layout stays stable).
    # We look for the prev button with a disabled attribute.
    import re
    assert re.search(r'class="[^"]*deck-prev[^"]*"[^>]*disabled', body)


def test_deck_nav_next_disabled_on_last_bug(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=2").text
    import re
    assert re.search(r'class="[^"]*deck-next[^"]*"[^>]*disabled', body)


def test_deck_nav_middle_bug_links_to_both(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    write_draft(triage_dir, 3, ni_targets=["x"])
    body = client.get("/?tab=needs-info&bug=2").text
    # Prev should link to bug=1, next to bug=3.
    assert "tab=needs-info&amp;bug=1" in body or "tab=needs-info&bug=1" in body
    assert "tab=needs-info&amp;bug=3" in body or "tab=needs-info&bug=3" in body


def test_deck_nav_hidden_when_bucket_is_single(triage_dir: Path) -> None:
    """No point showing prev/next when there's only one bug — skip the whole nav."""
    write_draft(triage_dir, 99, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    assert "deck-nav" not in body


# ─── rail filter input ──────────────────────────────────────────────

def test_rail_filter_input_present_with_multiple_bugs(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    assert 'class="rail-search"' in body


def test_rail_filter_input_hidden_with_single_bug(triage_dir: Path) -> None:
    """No point filtering a single-item list."""
    write_draft(triage_dir, 1, ni_targets=["x"])
    body = client.get("/?tab=needs-info").text
    assert 'class="rail-search"' not in body


# ─── rail bug-state tags ────────────────────────────────────────────

def _rail_row_for(body: str, bug_id: int) -> str:
    """Return the inner HTML of the rail <li> for the given bug_id."""
    import re
    m = re.search(
        rf'<li>\s*<a class="rail-item[^>]*data-bug-id="{bug_id}"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )
    assert m is not None, f"no rail item for bug {bug_id}"
    return m.group(1)


def test_rail_tag_does_not_render_old_ni_tag(triage_dir: Path) -> None:
    """The previous `NI` rail-meta tag was removed — it conflated 'draft adds
    NI' with 'bug already has NI' and confused the user."""
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    body = client.get("/?tab=needs-info").text
    row = _rail_row_for(body, 1)
    assert ">NI<" not in row
    assert "rail-meta" not in row


# ─── info-icon tooltip: ARIA describedby + real tooltip element ─────

def test_info_icon_uses_aria_describedby_to_real_tooltip(
    triage_dir: Path,
) -> None:
    """Screen readers don't read CSS-generated content. The tooltip body
    must be a real DOM node, referenced via aria-describedby."""
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    body = client.get("/?tab=needs-info").text
    import re
    icon = re.search(r'<span class="info-icon"[^>]*>', body)
    assert icon is not None, "info-icon missing from rail head"
    icon_attrs = icon.group(0)
    m = re.search(r'aria-describedby="([^"]+)"', icon_attrs)
    assert m is not None, "info-icon must have aria-describedby"
    tooltip_id = m.group(1)
    # The trigger isn't a button — a tooltip trigger doesn't activate
    # anything. Keep tabindex for keyboard focus and an accessible name.
    assert 'role="button"' not in icon_attrs
    assert 'tabindex="0"' in icon_attrs
    assert 'aria-label="About this tab"' in icon_attrs
    # The referenced element exists, has role="tooltip", and carries the
    # same help text the prior data-tooltip attribute used to carry.
    tip = re.search(
        rf'<span[^>]*role="tooltip"[^>]*id="{re.escape(tooltip_id)}"[^>]*>'
        r'(.*?)</span>',
        body, re.DOTALL,
    )
    assert tip is not None, f"no tooltip element with id={tooltip_id}"
    assert "needinfo" in tip.group(1).lower()


def test_info_icon_tooltip_id_includes_active_tab(triage_dir: Path) -> None:
    """The tooltip id varies per tab so it doesn't collide with other
    tabs' tooltips if they ever appear in the same DOM."""
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    write_draft(triage_dir, 2, severity="S3", priority="P3")
    body_a = client.get("/?tab=needs-info").text
    body_b = client.get("/?tab=triaged&bug=2").text
    assert 'id="tab-info-needs-info"' in body_a
    assert 'id="tab-info-triaged"' in body_b


# ─── tab-label single source of truth ───────────────────────────────

def test_tab_label_matches_rail_head_title(triage_dir: Path) -> None:
    """The tab-strip label and rail-head title come from the same TAB_INFO
    table — they must never drift. For each draft tab, the label shown in
    the tab strip equals the title shown in the rail header."""
    import re
    # Write one draft per draft section so each tab has content to render.
    write_draft(triage_dir, 1, ni_targets=["x@y"])         # §1a needs-info
    write_draft(triage_dir, 2, severity="S3", priority="P3")  # §1b triaged
    write_draft(triage_dir, 3, resolution="INCOMPLETE")    # §1c close
    for slug in ("triaged", "needs-info", "close"):
        body = client.get(f"/?tab={slug}").text
        # Extract the rail-head title (the text node before the info-icon).
        head = re.search(
            r'<span class="rail-head-title">\s*([^<\n]+?)\s*<', body, re.DOTALL,
        )
        assert head is not None, f"no rail-head-title found for tab={slug}"
        rail_title = head.group(1).strip()
        # Extract the active tab's <span class="label">.
        active = re.search(
            r'class="tab tab--active[^"]*"[^>]*>.*?'
            r'<span class="label">([^<]+)</span>',
            body, re.DOTALL,
        )
        assert active is not None, f"no active tab label for tab={slug}"
        tab_label = active.group(1).strip()
        assert rail_title == tab_label, (
            f"tab={slug}: tab strip says {tab_label!r}, rail head says "
            f"{rail_title!r} — TAB_INFO drift"
        )
