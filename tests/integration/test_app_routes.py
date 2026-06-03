"""Integration tests for the FastAPI routes — full-page rendering paths."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)


# ─── full-page rendering ────────────────────────────────────────────

def test_get_root_returns_200(triage_dir: Path) -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_get_root_returns_full_html_document(triage_dir: Path) -> None:
    body = client.get("/").text
    assert "<!doctype" in body.lower()
    assert "<html" in body
    assert "<title>Triage" in body
    # Topbar is part of every full-page render.
    assert 'class="topbar"' in body


def test_brand_title_links_to_repo(triage_dir: Path) -> None:
    """The "Triage" brand title in the topbar is a link to the project's
    GitHub repo — useful entry point for anyone wanting to inspect or
    contribute to the dashboard itself."""
    body = client.get("/").text
    import re
    # An <a> with the repo href must wrap the visible "Triage" text inside
    # the brand-title h1.
    m = re.search(
        r'<h1 class="brand-title">\s*'
        r'<a [^>]*href="https://github\.com/alastor0325/firefox-triage-dashboard"'
        r'[^>]*>\s*Triage\s*</a>\s*'
        r'</h1>',
        body, re.DOTALL,
    )
    assert m is not None, "brand title should be a link to the repo"
    # External link best practice: opens in a new tab, no referrer leak.
    snippet = m.group(0)
    assert 'target="_blank"' in snippet
    assert 'rel="noopener"' in snippet


def test_full_page_includes_htmx_script(triage_dir: Path) -> None:
    """htmx must be loaded on the full page so tab clicks can do partial swaps."""
    assert "htmx.org" in client.get("/").text


def test_full_page_links_favicon(triage_dir: Path) -> None:
    """Browser tab icon — SVG favicon served from /static/."""
    body = client.get("/").text
    assert '<link rel="icon"' in body
    assert "/static/favicon.svg" in body


def test_favicon_link_carries_cache_bust_version(
    triage_dir: Path,
) -> None:
    """Favicons cache aggressively in browsers. The <link rel=icon> tag
    must carry a ?v=<version> query string (reusing the css_version) so
    a favicon change forces a refetch without manual cache-clearing."""
    import re
    body = client.get("/").text
    m = re.search(
        r'<link rel="icon"[^>]*href="/static/favicon\.svg\?v=([^"]+)"',
        body,
    )
    assert m is not None, "favicon link missing ?v= cache buster"
    assert m.group(1), "cache-bust version is empty"


def test_favicon_endpoint_served(triage_dir: Path) -> None:
    """The favicon file must actually be served from the /static mount."""
    response = client.get("/static/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg")
    assert "<svg" in response.text


def test_left_right_arrow_keys_switch_topbar_tabs(triage_dir: Path) -> None:
    """Bare ArrowLeft / ArrowRight switch the active topbar tab. The
    existing typing-gate (input/textarea/select active) suppresses
    the handler so the cursor still moves normally inside the
    composer or the rail search."""
    body = client.get("/").text
    # The keydown switch arms for ArrowLeft and ArrowRight must be
    # present in the keyboard handler block.
    assert "'ArrowLeft'" in body or "case 'ArrowLeft'" in body
    assert "'ArrowRight'" in body or "case 'ArrowRight'" in body
    # The handler navigates to the prev/next .tab — the active-tab
    # finder must look it up via the .tab--active class.
    assert "tab--active" in body


def test_dynamic_html_is_not_cached(triage_dir: Path) -> None:
    """Tab switches are htmx GETs; the browser must not cache the dynamic
    HTML or it serves a stale partial after the markup changes (a tab
    losing its 'New' tag). The HTML responses must carry Cache-Control:
    no-store."""
    resp = client.get("/")
    assert resp.headers.get("cache-control") == "no-store"
    # htmx partial too
    resp2 = client.get("/?tab=analyzed", headers={"HX-Request": "true"})
    assert resp2.headers.get("cache-control") == "no-store"


def test_tabs_have_hx_sync_to_prevent_out_of_order_swaps(triage_dir: Path) -> None:
    """Rapid arrow/click tab switching fires several htmx GETs; without
    hx-sync an earlier response can settle after a later one and overwrite
    the content+highlight with a stale tab (selection 'skips'). Each tab
    must carry hx-sync with the replace strategy so a new request aborts
    the in-flight one and only the latest selection wins."""
    body = client.get("/").text
    assert 'hx-sync="closest nav:replace"' in body


def test_keyboard_shortcut_letters_present_in_handler(triage_dir: Path) -> None:
    """The deck-nav title attribute advertises j/k/a/Esc/`/` as keyboard
    shortcuts. The keydown handler in base.html must actually wire
    each one — otherwise the user-facing docs in the title attribute
    diverge from real behavior."""
    body = client.get("/").text
    # Each documented shortcut appears as a `case 'X':` arm in the JS.
    assert "case 'j':" in body
    assert "case 'k':" in body
    assert "case 'a':" in body
    assert "case 'Escape':" in body
    # `/` focuses the rail search.
    assert "case '/':" in body
    # And the title attribute on .deck-nav advertises them so the user
    # knows what to press.
    import re
    write_draft(triage_dir, 1, ni_targets=["x"])
    write_draft(triage_dir, 2, ni_targets=["x"])
    multi = client.get("/?tab=needs-info").text
    m = re.search(r'<nav class="deck-nav"[^>]*title="([^"]+)"', multi)
    assert m is not None, "deck-nav must carry a title= with the keyboard shortcuts"
    title = m.group(1)
    # All documented shortcuts surface to the user.
    assert "j" in title.lower() or "↓" in title
    assert "/" in title
    assert "Esc" in title or "esc" in title


def test_stylesheet_link_carries_cache_bust_version(
    triage_dir: Path,
) -> None:
    """The <link> tag must include ?v=<version> so browsers refetch
    style.css when it changes on disk."""
    import re
    body = client.get("/").text
    m = re.search(
        r'<link rel="stylesheet" href="/static/style\.css\?v=([^"]+)"',
        body,
    )
    assert m is not None, "stylesheet link missing ?v= cache buster"
    assert m.group(1), "cache-bust version is empty"


def test_css_version_is_stable_across_requests(triage_dir: Path) -> None:
    """The version is read once at startup, so two requests with no
    file change must return the same query string."""
    import re
    pattern = re.compile(
        r'<link rel="stylesheet" href="/static/style\.css\?v=([^"]+)"'
    )
    v1 = pattern.search(client.get("/").text).group(1)
    v2 = pattern.search(client.get("/").text).group(1)
    assert v1 == v2


def test_full_page_opens_sse_event_source(triage_dir: Path) -> None:
    """The page wires a browser EventSource('/events') for live updates."""
    body = client.get("/").text
    assert "new EventSource('/events')" in body
    # And subscribes to the four event types the backend emits.
    assert "draft-changed" in body
    assert "draft-deleted" in body
    assert "watch-changed" in body
    assert "queue-changed" in body


def test_topbar_has_process_queue_dropdown(triage_dir: Path) -> None:
    """The Process queue control is a <details> dropdown in the topbar."""
    body = client.get("/").text
    # The <details> wrapper and the <summary> button that triggers it.
    assert 'id="process-queue-dropdown"' in body
    assert 'id="btn-process-queue"' in body
    # The dropdown contains the panel that holds the queued rows + Copy.
    assert 'id="queue-dropdown-panel"' in body
    # Copy button wired to fetch /queue/prepare on click (the only path
    # that calls /queue/prepare now — auto-copy on toggle is gone).
    assert "/queue/prepare" in body
    assert "copyDrainPrompt" in body


def test_queue_tab_is_removed_from_topbar(triage_dir: Path) -> None:
    """The standalone Queue tab is gone — the dropdown replaced it."""
    body = client.get("/").text
    assert 'href="?tab=queue"' not in body


def test_queue_tab_url_does_not_render_inspector(triage_dir: Path) -> None:
    """An old bookmark to ?tab=queue should fall back to a draft tab,
    not 500 and not render an inspector page."""
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    response = client.get("/?tab=queue")
    assert response.status_code == 200
    # No queue-inspector content (those classes are gone).
    assert 'class="queue-inspector"' not in response.text
    assert 'class="queue-list"' not in response.text


def test_tab_labels_use_action_oriented_names(triage_dir: Path) -> None:
    """The four tabs render their renamed labels and no § markers."""
    body = client.get("/").text
    # New labels are present.
    assert ">Analyzed<" in body
    assert ">Needs Info<" in body
    assert ">Close / Reassign<" in body
    assert ">Awaiting reply<" in body
    # Old user-facing labels are gone.
    assert ">Triaged<" not in body
    assert ">Close<" not in body or ">Close / Reassign<" in body  # only the new form
    assert ">Watching<" not in body


def test_tab_strip_does_not_render_section_markers(triage_dir: Path) -> None:
    """The skill-internal §1a/§1b/§1c markers are not user-facing — the
    <span class="marker"> in the tab strip is removed."""
    body = client.get("/").text
    # Find the tabs nav element and confirm no .marker span lives inside.
    import re
    m = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', body, re.DOTALL)
    assert m is not None
    assert 'class="marker"' not in m.group(1)
    assert "§1a" not in m.group(1)
    assert "§1b" not in m.group(1)
    assert "§1c" not in m.group(1)


def test_topbar_right_groups_stats_and_dropdown(triage_dir: Path) -> None:
    """Stats and the Process queue dropdown live in .topbar-right so they
    stay anchored to the right edge at any viewport width."""
    body = client.get("/").text
    import re
    m = re.search(
        r'<div\s+class="topbar-right"[^>]*>(.*?)</details>\s*</div>\s*</header>',
        body, re.DOTALL,
    )
    assert m is not None, "missing .topbar-right wrapper around the dropdown"
    inner = m.group(1)
    assert 'class="stats"' in inner
    assert 'id="process-queue-dropdown"' in inner


def test_process_queue_button_shows_count_zero_when_empty(
    triage_dir: Path,
) -> None:
    body = client.get("/").text
    import re
    # The summary acts as the button now.
    m = re.search(
        r'id="btn-process-queue"[^>]*>(.*?)</summary>', body, re.DOTALL,
    )
    assert m is not None
    summary_html = m.group(0)
    assert 'class="queue-count"' in summary_html
    assert ">0<" in summary_html


def test_process_queue_button_shows_live_count(triage_dir: Path) -> None:
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-29T00:00:00+00:00"}\n'
        '{"action":"refine","bug_id":2,"feedback":"b","ts":"2026-05-29T00:01:00+00:00"}\n'
        '{"action":"refine","bug_id":3,"feedback":"c","ts":"2026-05-29T00:02:00+00:00"}\n'
    )
    body = client.get("/").text
    # The topbar queue-count span (the one inside the <summary>) shows 3.
    import re
    m = re.search(
        r'id="btn-process-queue"[^>]*>.*?<span class="queue-count">(\d+)</span>',
        body, re.DOTALL,
    )
    assert m is not None
    assert m.group(1) == "3"


def test_healthz_endpoint() -> None:
    assert client.get("/healthz").json() == {"ok": True}


# ─── tab routing ────────────────────────────────────────────────────

def test_default_tab_is_first_nonempty_section(triage_dir: Path) -> None:
    """With only §1c drafts, the default landing tab should be 'close'."""
    write_draft(triage_dir, 1, resolution="INCOMPLETE")
    body = client.get("/").text
    # The active tab carries an aria-selected="true" attribute.
    assert 'href="?tab=close"' in body
    # ... and the bug-1 card is rendered (the §1c bucket is shown).
    assert 'id="card-1"' in body


def test_default_tab_prefers_triaged_when_available(triage_dir: Path) -> None:
    """§1b takes precedence over §1a and §1c when all are non-empty."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")     # §1b
    write_draft(triage_dir, 2, ni_targets=["x@y"])               # §1a
    write_draft(triage_dir, 3, resolution="INCOMPLETE")          # §1c
    body = client.get("/").text
    # Only the §1b bug-1 card should be in the body; the others are off-tab.
    assert 'id="card-1"' in body
    assert 'id="card-2"' not in body
    assert 'id="card-3"' not in body


def test_tab_query_param_filters_to_section(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, severity="S3", priority="P3")     # §1b
    write_draft(triage_dir, 2, ni_targets=["x@y"])               # §1a

    body_a = client.get("/?tab=needs-info").text
    assert 'id="card-2"' in body_a
    assert 'id="card-1"' not in body_a

    body_b = client.get("/?tab=triaged").text
    assert 'id="card-1"' in body_b
    assert 'id="card-2"' not in body_b


def test_invalid_tab_falls_back_to_first_nonempty(triage_dir: Path) -> None:
    write_draft(triage_dir, 99, ni_targets=["x@y"])  # only a §1a bug
    body = client.get("/?tab=garbage-not-a-tab").text
    # garbage tab → falls back to first non-empty (§1a "needs-info")
    assert 'id="card-99"' in body


def test_watching_tab_renders_watch_list(
    triage_dir: Path, monkeypatch
) -> None:
    import json
    (triage_dir / "ni-watch.json").write_text(
        json.dumps([
            {"bug_id": 12345, "title": "Tracked bug",
             "ni_targets": ["alwu@mozilla.com"], "added_at": "2026-05-28"}
        ])
    )
    body = client.get("/?tab=watching").text
    assert "watch-item" in body
    assert "12345" in body
    assert "Tracked bug" in body


def test_empty_state_when_no_drafts(triage_dir: Path) -> None:
    body = client.get("/").text
    assert "No pending drafts" in body


# ─── card rendering content ─────────────────────────────────────────

def test_b1_card_does_not_show_bugstart_copy_button(
    triage_dir: Path,
) -> None:
    """§1b cards no longer show the /bug-start copy button — investigation
    is auto-queued by /triage at draft time (and fallback-queued by
    Apply via Phase 5), so the manual clipboard copy is redundant."""
    write_draft(
        triage_dir, 555, severity="S3", priority="P3",
        title="root cause found", blocks_add=[12345],
    )
    body = client.get("/").text
    # Neither the clipboard-writeText handler nor the button label remains.
    # (`btn-copy-prompt` is a different class used in the queue dropdown —
    # don't match it.)
    assert "/bug-start 555" not in body
    assert 'class="btn btn-copy"' not in body
    assert "navigator.clipboard.writeText('/bug-start" not in body


def test_a1_card_has_no_bugstart_button(triage_dir: Path) -> None:
    """§1a cards never had the button; still don't."""
    write_draft(triage_dir, 555, ni_targets=["reporter@example.com"])
    body = client.get("/?tab=needs-info").text
    assert "/bug-start" not in body


def test_c1_reassign_button_replaces_apply_when_component_set(
    triage_dir: Path,
) -> None:
    write_draft(triage_dir, 555, component="Widget: Gtk", product="Core")
    body = client.get("/?tab=close").text
    assert "Reassign" in body
    # The plain "Apply" primary button should not appear when reassigning.
    assert ">Apply<" not in body


def test_c1_apply_close_button_when_resolving(triage_dir: Path) -> None:
    write_draft(triage_dir, 555, resolution="INCOMPLETE")
    body = client.get("/?tab=close").text
    assert "Apply &amp; close" in body or "Apply &amp; Close" in body


def test_duplicate_resolution_links_dupe_of_bug(triage_dir: Path) -> None:
    """For DUPLICATE drafts, the Will Apply footer shows `of bug N` linked to Bugzilla."""
    write_draft(triage_dir, 2042320, resolution="DUPLICATE", dupe_of=1711812)
    body = client.get("/?tab=close").text
    assert (
        '<a class="meta" '
        'href="https://bugzilla.mozilla.org/show_bug.cgi?id=1711812"'
    ) in body
    assert ">bug 1711812</a>" in body


def test_duplicate_without_dupe_of_renders_no_link(triage_dir: Path) -> None:
    """A DUPLICATE draft that doesn't yet carry dupe_of still renders cleanly,
    with the pill but no `of bug ...` link."""
    write_draft(triage_dir, 9991, resolution="DUPLICATE")
    body = client.get("/?tab=close").text
    assert ">DUPLICATE<" in body
    assert " of <a class=\"meta\"" not in body


def test_non_duplicate_with_dupe_of_does_not_link(triage_dir: Path) -> None:
    """If the JSON has a stray dupe_of but resolution isn't DUPLICATE, ignore it —
    the link only makes sense when the resolution is actually DUPLICATE."""
    write_draft(
        triage_dir, 9992, resolution="INCOMPLETE", dupe_of=1711812,
    )
    body = client.get("/?tab=close").text
    assert "bug 1711812" not in body


# ─── Apply / Skip buttons wired up ──────────────────────────────────

def test_apply_button_posts_to_apply_endpoint(triage_dir: Path) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'hx-post="/draft/5551/apply"' in body


def test_blocks_add_renders_each_id_as_bugzilla_link(
    triage_dir: Path,
) -> None:
    """The Will Apply `+blocks` list must hyperlink each bug id, not show
    plain text, so the user can jump straight to the referenced bug."""
    write_draft(
        triage_dir, 5551, severity="S3", priority="P3",
        blocks_add=[12345, 67890],
    )
    body = client.get("/").text
    assert (
        '<a class="meta" '
        'href="https://bugzilla.mozilla.org/show_bug.cgi?id=12345"'
    ) in body
    assert (
        '<a class="meta" '
        'href="https://bugzilla.mozilla.org/show_bug.cgi?id=67890"'
    ) in body
    # Open in a new tab and harden the rel attribute, matching the other
    # bugzilla anchors in card.html.
    import re
    for bug_id in (12345, 67890):
        m = re.search(
            rf'<a class="meta"[^>]*id={bug_id}"[^>]*>',
            body,
        )
        assert m is not None, f"missing anchor for blocks bug {bug_id}"
        assert 'target="_blank"' in m.group(0)
        assert 'rel="noopener"' in m.group(0)


def test_card_does_not_render_skip_button(triage_dir: Path) -> None:
    """The Skip button is gone from the card action row; the /skip
    endpoint stays callable but no longer has a UI affordance."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'hx-post="/draft/5551/skip"' not in body
    assert ">Skip<" not in body
    # The Apply button is still rendered — sanity check that the action
    # row hasn't been gutted entirely.
    assert 'hx-post="/draft/5551/apply"' in body


def test_skip_endpoint_still_callable_without_button(
    triage_dir: Path,
) -> None:
    """Removing the button must not break the endpoint — scripts and
    cleanup tooling still POST to /draft/{id}/skip directly."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    response = client.post("/draft/5551/skip")
    assert response.status_code == 200


def test_apply_button_not_disabled(triage_dir: Path) -> None:
    """The Apply button must be clickable now — no `disabled` attribute."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    # Find the apply button line and ensure it has no `disabled`.
    import re
    apply_btn = re.search(
        r'<button[^>]*hx-post="/draft/5551/apply"[^>]*>', body
    )
    assert apply_btn is not None
    assert "disabled" not in apply_btn.group(0)


def test_card_has_apply_status_target(triage_dir: Path) -> None:
    """Each card has its own status target so apply/skip results land
    next to the right card, not bleeding across cards."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'id="apply-status-5551"' in body


def test_c1_reassign_button_wired_to_apply(triage_dir: Path) -> None:
    """Reassign and Apply & close are still semantically `apply` — they
    post to the same endpoint; the backend uses pending.product /
    pending.resolution to decide what happens."""
    write_draft(triage_dir, 5551, component="Widget: Gtk", product="Core")
    body = client.get("/?tab=close").text
    assert 'hx-post="/draft/5551/apply"' in body


# ─── feedback (AI revise) UI ────────────────────────────────────────

def test_card_has_feedback_form(triage_dir: Path) -> None:
    """Every card must expose the feedback-and-revise form."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'hx-post="/draft/5551/refine"' in body
    assert 'name="feedback"' in body
    # The Revise button submits the form.
    assert "Revise" in body


def test_card_feedback_form_targets_per_card_status(triage_dir: Path) -> None:
    """Each card has its own status div so submissions don't bleed across cards."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'id="fbstatus-1"' in body
    assert 'hx-target="#fbstatus-1"' in body


# ─── inline pending-feedback list (Phase 3.5 Q1) ────────────────────

def test_card_has_no_pending_feedback_section_when_queue_empty(
    triage_dir: Path,
) -> None:
    """No queued items → no .pending-feedback block on the card."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    body = client.get("/").text
    assert "pending-feedback" not in body


def test_card_renders_pending_feedback_for_active_bug(
    triage_dir: Path,
) -> None:
    """Queued items for the active bug show under the composer with
    each feedback's text and a remove (✕) control."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"shorten it",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
        '{"action":"refine","bug_id":1,"feedback":"drop bisect",'
        '"ts":"2026-05-29T15:12:00+00:00"}\n'
    )
    body = client.get("/").text
    assert 'class="pending-feedback"' in body
    assert "Pending feedback" in body
    assert "shorten it" in body
    assert "drop bisect" in body
    # Each item has a remove control wired to the new endpoint.
    assert 'hx-post="/draft/1/refine/remove"' in body


def test_card_remove_button_carries_entry_ts_via_hx_vals(
    triage_dir: Path,
) -> None:
    """The ✕ button must POST `ts` so the server can identify the entry."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"x",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
    )
    body = client.get("/").text
    # The exact ts must be in the hx-vals payload (so the server can
    # remove the right entry).
    assert "2026-05-29T14:30:00+00:00" in body


def test_index_renders_when_investigation_file_present(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """The index endpoint must load and use an investigation file for
    the active draft without crashing — wires data.load_investigation
    into the template context."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "investigations"
    inv_dir.mkdir()
    (inv_dir / "bug-5551-investigation.md").write_text(
        "---\n"
        "bug_id: 5551\n"
        "status: investigated\n"
        "root_cause: HEVC mapping table mis-identifies missing MFT\n"
        "---\n"
        "# Bug 5551 Investigation\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    response = client.get("/")
    assert response.status_code == 200
    # Sanity: the card for the active draft is rendered.
    assert 'id="card-5551"' in response.text


def test_index_renders_when_no_investigation_file(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """No investigation file for the active draft → load returns None
    and the page still renders cleanly."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "investigations"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="card-5551"' in response.text


def _write_inv(inv_dir: Path, bug_id: int, frontmatter: str) -> None:
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / f"bug-{bug_id}-investigation.md").write_text(
        f"---\n{frontmatter}---\n# body\n", encoding="utf-8",
    )


def test_card_renders_findings_root_cause(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\n"
        "root_cause: HEVC mapping table mis-identifies missing MFT\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings"' in body
    assert "Root cause:" in body
    assert "HEVC mapping table mis-identifies missing MFT" in body


def test_card_findings_links_affected_files_to_searchfox(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\n"
        "affected_files:\n"
        "  - dom/media/platforms/VideoUtils.cpp\n"
        "  - dom/media/platforms/wmf/WMFDecoderModule.cpp\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    # Searchfox migrated mozilla-central → firefox-main (the old tree
    # now 301-redirects). Always link to the canonical URL.
    assert (
        '<a href="https://searchfox.org/firefox-main/source/'
        'dom/media/platforms/VideoUtils.cpp"' in body
    )
    assert (
        '<a href="https://searchfox.org/firefox-main/source/'
        'dom/media/platforms/wmf/WMFDecoderModule.cpp"' in body
    )
    # Each path wrapped in <code> for monospace rendering.
    assert "<code>dom/media/platforms/VideoUtils.cpp</code>" in body
    # Regression guard: the legacy mozilla-central tree must not appear.
    assert "searchfox.org/mozilla-central/" not in body


def test_card_findings_affected_files_with_line_anchor(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """affected_files entries can carry a #L<n> suffix that the dashboard
    translates into a searchfox line anchor + `file:line` display text."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\n"
        "affected_files:\n"
        "  - dom/media/autoplay/AutoplayPolicy.cpp#L297\n"
        "  - dom/media/MediaDecoder.cpp\n"
        "  - dom/media/foo.cpp#L42-L50\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    # Single-line anchor: href ends with #297, display reads "...:297".
    assert (
        'href="https://searchfox.org/firefox-main/source/'
        'dom/media/autoplay/AutoplayPolicy.cpp#297"' in body
    )
    assert "<code>dom/media/autoplay/AutoplayPolicy.cpp:297</code>" in body
    # Bare path: still works (whole-file URL, plain display).
    assert (
        'href="https://searchfox.org/firefox-main/source/'
        'dom/media/MediaDecoder.cpp"' in body
    )
    assert "<code>dom/media/MediaDecoder.cpp</code>" in body
    # Range: anchor uses the start line; display shows the full range.
    assert (
        'href="https://searchfox.org/firefox-main/source/'
        'dom/media/foo.cpp#42"' in body
    )
    assert "<code>dom/media/foo.cpp:42-50</code>" in body


def test_card_findings_no_regression_line_when_null(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\nregression_range: null\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert "Regression:" not in body


def test_card_findings_status_pill_blocked_modifier(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: blocked\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-status findings-status--blocked"' in body


def test_card_no_findings_element_without_investigation(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings"' not in body


def test_card_findings_shell_renders_when_file_has_no_frontmatter(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """A pre-schema investigation file (no `---` frontmatter) still
    exists on disk, so render a minimal Findings block with just the
    GitHub link. The per-section `{% if %}` guards short-circuit on
    empty fields so no status pill, no root-cause, no affected files
    appear."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    inv_dir.mkdir()
    (inv_dir / "bug-5551-investigation.md").write_text(
        "# Bug 5551 Investigation\n\nOld-style file without frontmatter.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    # Findings block still rendered.
    assert 'class="findings"' in body
    # GitHub link present and templated with the bug id.
    assert (
        'href="https://github.com/alastor0325/firefox-bug-investigation/'
        'blob/main/bug-5551-investigation.md"'
    ) in body
    # No status pill, no root-cause / affected-file / regression /
    # related / complexity / notes blocks rendered (their values are
    # empty defaults so the `{% if %}` guards skip them).
    assert "findings-status" not in body
    assert "Root cause:" not in body
    assert "findings-files" not in body
    assert "Regression:" not in body
    assert "Related:" not in body
    assert "Complexity:" not in body


def test_card_findings_related_bugs_render_as_links(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nrelated_bugs: [1992187, 2038494]\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert (
        '<a href="https://bugzilla.mozilla.org/show_bug.cgi?id=1992187"'
        in body
    )
    assert (
        '<a href="https://bugzilla.mozilla.org/show_bug.cgi?id=2038494"'
        in body
    )


def test_card_findings_open_link_points_at_github(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Chrome (and other browsers) block http→file:// navigation entirely,
    so the "Open full investigation →" link must point at the canonical
    GitHub copy of the investigation file instead."""
    write_draft(triage_dir, 2042320, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(inv_dir, 2042320, "bug_id: 2042320\n")
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert (
        'href="https://github.com/alastor0325/firefox-bug-investigation/'
        'blob/main/bug-2042320-investigation.md"'
    ) in body
    # Regression guard: no stale file:// links remain.
    assert "file://" not in body


# ─── is_stale flag (investigation older than bug activity) ────────────

def test_findings_stale_pill_when_bug_activity_newer(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(
        triage_dir, 5551, severity="S3", priority="P3",
        bug_context={
            "last_activity": "2026-05-30T14:08:00Z",
        },
    )
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\n"
        "investigated_at: 2026-05-29T14:08:00Z\n"
        "status: investigated\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-stale"' in body
    assert ">stale<" in body


def test_findings_not_stale_when_investigation_newer(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(
        triage_dir, 5551, severity="S3", priority="P3",
        bug_context={
            "last_activity": "2026-05-29T14:08:00Z",
        },
    )
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\n"
        "investigated_at: 2026-05-30T14:08:00Z\n"
        "status: investigated\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-stale"' not in body


def test_findings_not_stale_on_malformed_iso_strings(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Unparsable timestamps default to not-stale rather than crashing."""
    write_draft(
        triage_dir, 5551, severity="S3", priority="P3",
        bug_context={"last_activity": "yesterday-ish"},
    )
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\n"
        'investigated_at: "not a date at all"\n',
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    response = client.get("/")
    assert response.status_code == 200
    assert 'class="findings-stale"' not in response.text


def test_findings_not_stale_when_no_bug_context(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Without bug_context we can't compare timestamps — default to not stale."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\n"
        "investigated_at: 2026-05-29T14:08:00Z\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-stale"' not in body


def test_findings_not_stale_when_only_one_side_is_tz_aware(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Mixing naive and aware ISO strings would raise TypeError on
    comparison — defensively report not-stale instead."""
    write_draft(
        triage_dir, 5551, severity="S3", priority="P3",
        bug_context={"last_activity": "2026-05-30T14:08:00Z"},
    )
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\n"
        "investigated_at: 2026-05-29T14:08:00\n",  # naive (no tz)
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    response = client.get("/")
    assert response.status_code == 200
    assert 'class="findings-stale"' not in response.text


# ─── Investigating / stalled / depth pills (lock-file aware) ──────────


def _touch_lock_path(inv_dir: Path, bug_id: int) -> Path:
    inv_dir.mkdir(parents=True, exist_ok=True)
    path = inv_dir / f"bug-{bug_id}-investigating.lock"
    path.write_text("", encoding="utf-8")
    return path


def test_findings_investigating_pill_when_lock_fresh(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Fresh lock → investigating pill renders; root-cause body absent."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _touch_lock_path(inv_dir, 5551)
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-status findings-status--investigating"' in body
    # No root-cause / affected-files section even if a stale md were present.
    assert "Root cause:" not in body
    assert "findings-files" not in body


def test_findings_investigating_overrides_md_status(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Fresh lock present alongside an md with status=investigated →
    investigating wins; the stale `investigated` pill is not rendered."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\n"
        "root_cause: stale data from a previous run\n",
    )
    _touch_lock_path(inv_dir, 5551)
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'findings-status--investigating' in body
    assert 'findings-status--investigated' not in body
    assert "stale data from a previous run" not in body


def test_findings_investigation_stalled_pill_when_lock_old(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """Lock mtime > 30 min → stalled pill + a "re-run /bug-start" affordance."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    lock = _touch_lock_path(inv_dir, 5551)
    import os, time
    old = time.time() - (31 * 60)
    os.utime(lock, (old, old))
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert (
        'class="findings-status findings-status--investigation-stalled"'
        in body
    )
    # User-facing affordance to re-run.
    assert "/bug-start" in body


def test_findings_depth_triage_badge_renders(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    """`depth: triage` frontmatter → "shallow · re-run for deep" pill
    appears next to the GitHub link."""
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\ndepth: triage\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'class="findings-depth-triage"' in body
    assert "shallow" in body


def test_findings_no_depth_badge_when_deep(
    triage_dir: Path, tmp_path: Path, monkeypatch,
) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    inv_dir = tmp_path / "inv"
    _write_inv(
        inv_dir, 5551,
        "bug_id: 5551\nstatus: investigated\n",
    )
    monkeypatch.setenv("FIREFOX_INVESTIGATION_DIR", str(inv_dir))
    body = client.get("/").text
    assert 'findings-depth-triage' not in body


def test_card_pending_feedback_escapes_html(triage_dir: Path) -> None:
    """Refine feedback is user input. When rendered in the pending-feedback
    list (card.html), it must be HTML-escaped — otherwise a crafted
    feedback could inject scripts into the page on subsequent loads.

    The refine endpoint's htmx fragment escape is already tested elsewhere;
    this guards the same input flowing through pending_feedback_for →
    card.html on every page render."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"refine","bug_id":1,"feedback":"<script>alert(1)</script>",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
    )
    body = client.get("/").text
    # The pending-feedback section renders, but the raw <script> must not be live.
    assert 'class="pending-feedback"' in body
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_queue_dropdown_feedback_escapes_html(triage_dir: Path) -> None:
    """The Process-queue dropdown also renders refine feedback. Same XSS
    invariant as the card's pending-feedback list — escape user input."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    (triage_dir / "claude-queue.jsonl").write_text(
        '{"action":"refine","bug_id":1,"feedback":"<img src=x onerror=alert(1)>",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
    )
    body = client.get("/queue/dropdown").text
    assert "<img src=x onerror=alert(1)>" not in body
    # The Jinja autoescape replaces `<` and `>` with their entity forms.
    assert "&lt;img" in body


def test_card_does_not_show_other_bugs_pending_feedback(
    triage_dir: Path,
) -> None:
    """Only the active bug's feedback shows in the per-card pending list.
    (The topbar dropdown legitimately surfaces every bug's queue — the
    test is scoped to the card's pending-feedback section.)"""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    write_draft(triage_dir, 2, severity="S3", priority="P3")
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":2,"feedback":"for-other-bug",'
        '"ts":"2026-05-29T14:30:00+00:00"}\n'
    )
    body = client.get("/?bug=1").text
    import re
    # Isolate the per-card pending-feedback section (if any) and check
    # bug 2's feedback isn't there.
    m = re.search(
        r'<section class="pending-feedback".*?</section>', body, re.DOTALL,
    )
    pending_section = m.group(0) if m else ""
    assert "for-other-bug" not in pending_section


# ─── rail tags: regression / emergency replace P/S / crash ───────────

def _rail_row_for(body: str, bug_id: int) -> str:
    """Return the inner HTML of the rail <li> for the given bug_id."""
    import re
    m = re.search(
        rf'<li>\s*<a class="rail-item[^>]*data-bug-id="{bug_id}"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )
    assert m is not None, f"no rail item for bug {bug_id}"
    return m.group(1)


def test_rail_tag_regression_when_keyword_present(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["regression"]},
    )
    body = client.get("/?tab=needs-info").text
    row = _rail_row_for(body, 1)
    assert "rail-tag--regression" in row
    assert ">regression<" in row


def test_rail_tag_emergency_when_sec_critical(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["sec-critical"]},
    )
    body = client.get("/?tab=needs-info").text
    row = _rail_row_for(body, 1)
    assert "rail-tag--emergency" in row
    assert ">emergency<" in row


def test_rail_tag_emergency_when_sec_high(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["sec-high"]},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--emergency" in row


def test_rail_tag_emergency_when_topcrash(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["topcrash"]},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--emergency" in row


def test_rail_tag_both_emergency_and_regression_render_in_order(
    triage_dir: Path,
) -> None:
    """When both apply, emergency renders BEFORE regression so the more
    severe signal is read first."""
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["sec-critical", "regression"]},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--emergency" in row
    assert "rail-tag--regression" in row
    assert row.index("rail-tag--emergency") < row.index("rail-tag--regression")


def test_rail_no_tags_when_bug_context_missing(triage_dir: Path) -> None:
    """Legacy pending JSON with no bug_context renders cleanly — no tags,
    no crash."""
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--emergency" not in row
    assert "rail-tag--regression" not in row


def test_rail_no_tags_when_keywords_empty(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"], bug_context={"keywords": []},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--emergency" not in row
    assert "rail-tag--regression" not in row


def test_rail_dropped_tags_no_longer_render(triage_dir: Path) -> None:
    """The old P/S, no-P/S, and crash rail tags are gone — they're noise
    within a tab (every §1a has no-P/S, every §1b has P/S)."""
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"current_severity": "S3", "current_priority": "P2",
                     "keywords": ["crash"]},
    )
    body = client.get("/?tab=needs-info").text
    assert 'class="rail-tag rail-tag--ps"' not in body
    assert 'class="rail-tag rail-tag--no-ps"' not in body
    assert 'class="rail-tag rail-tag--crash"' not in body


# ─── taken: rail tag + Assigned chip when bug is assigned ────────────

def test_rail_tag_taken_when_assigned(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"assigned_to": "dev@mozilla.com",
                     "assigned_to_name": "Dev Person"},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--taken" in row
    assert ">Assigned<" in row


def test_rail_tag_taken_renders_after_other_tags(triage_dir: Path) -> None:
    """The taken tag sits at the END of the tag group, after New /
    emergency / regression."""
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"keywords": ["sec-critical", "regression"],
                     "assigned_to": "dev@mozilla.com"},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert row.index("rail-tag--regression") < row.index("rail-tag--taken")
    assert row.index("rail-tag--emergency") < row.index("rail-tag--taken")


def test_rail_no_taken_tag_when_unassigned(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"assigned_to": "nobody@mozilla.org"},
    )
    row = _rail_row_for(client.get("/?tab=needs-info").text, 1)
    assert "rail-tag--taken" not in row


def test_card_shows_assigned_chip_when_assigned(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"assigned_to": "dev@mozilla.com",
                     "assigned_to_name": "Dev Person"},
    )
    body = client.get("/?tab=needs-info&bug=1").text
    assert "ver-chip--taken" in body
    assert "Assigned" in body
    assert "Dev Person" in body


def test_card_assigned_chip_falls_back_to_email(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"assigned_to": "dev@mozilla.com"},
    )
    body = client.get("/?tab=needs-info&bug=1").text
    assert "ver-chip--taken" in body
    assert "dev@mozilla.com" in body


def test_card_no_assigned_chip_when_unassigned(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, ni_targets=["x@y"],
        bug_context={"assigned_to": "nobody@mozilla.org"},
    )
    body = client.get("/?tab=needs-info&bug=1").text
    assert "ver-chip--taken" not in body


# ─── watching tab: stalled indicator on old entries ──────────────────

def _write_watch(triage_dir: Path, entries: list[dict]) -> None:
    import json
    (triage_dir / "ni-watch.json").write_text(json.dumps(entries))


def _watch_item_for(body: str, bug_id: int) -> str:
    """Return the inner HTML of the watch-item <li> for the given bug_id."""
    import re
    m = re.search(
        rf'<li class="watch-item">(.*?(?:>{bug_id}<).*?)</li>',
        body, re.DOTALL,
    )
    assert m is not None, f"no watch item for bug {bug_id}"
    return m.group(1)


def test_watching_stalled_badge_when_added_15_days_ago(
    triage_dir: Path,
) -> None:
    from datetime import datetime, timedelta, timezone
    fifteen_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=15)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_watch(triage_dir, [{
        "bug_id": 12345, "title": "Old bug",
        "ni_targets": ["alwu@mozilla.com"], "added_at": fifteen_days_ago,
    }])
    body = client.get("/?tab=watching").text
    item = _watch_item_for(body, 12345)
    assert "watch-stalled" in item
    assert "stalled" in item.lower()


def test_watching_no_stalled_badge_when_added_1_day_ago(
    triage_dir: Path,
) -> None:
    from datetime import datetime, timedelta, timezone
    one_day_ago = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_watch(triage_dir, [{
        "bug_id": 12345, "title": "Fresh bug",
        "ni_targets": ["alwu@mozilla.com"], "added_at": one_day_ago,
    }])
    body = client.get("/?tab=watching").text
    item = _watch_item_for(body, 12345)
    assert "watch-stalled" not in item


def test_watching_no_stalled_badge_when_added_at_missing(
    triage_dir: Path,
) -> None:
    _write_watch(triage_dir, [{
        "bug_id": 12345, "title": "No timestamp",
        "ni_targets": ["alwu@mozilla.com"], "added_at": "",
    }])
    body = client.get("/?tab=watching").text
    item = _watch_item_for(body, 12345)
    assert "watch-stalled" not in item


# ─── will-apply diff: regressed_by ──────────────────────────────────

def test_will_apply_diff_shows_regressed_by(triage_dir: Path) -> None:
    """A draft with regressed_by_add renders a '+regressed by bug N' line
    linking to the regressor on Bugzilla."""
    write_draft(triage_dir, 1, ni_targets=["x@y"], regressed_by_add=[2033628])
    body = client.get("/").text
    assert "regressed by" in body
    assert "id=2033628" in body


def test_will_apply_diff_omits_regressed_by_when_empty(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, ni_targets=["x@y"])
    body = client.get("/").text
    assert "regressed by" not in body


# ─── will-apply diff: assignee ──────────────────────────────────────

def test_will_apply_diff_shows_assignee_and_status(triage_dir: Path) -> None:
    write_draft(
        triage_dir, 1, severity="S2", priority="P2",
        status="ASSIGNED", assigned_to="alwu@mozilla.com",
    )
    body = client.get("/?tab=triaged&bug=1").text
    assert "assign to" in body
    assert "alwu@mozilla.com" in body
    assert "ASSIGNED" in body


def test_will_apply_diff_omits_assignee_when_absent(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, severity="S2", priority="P2")
    body = client.get("/?tab=triaged&bug=1").text
    assert "assign to" not in body
