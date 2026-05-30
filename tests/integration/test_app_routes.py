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


def test_full_page_includes_htmx_script(triage_dir: Path) -> None:
    """htmx must be loaded on the full page so tab clicks can do partial swaps."""
    assert "htmx.org" in client.get("/").text


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

def test_b1_card_has_bugstart_copy_button(triage_dir: Path) -> None:
    """§1b cards must expose the /bug-start handoff button."""
    write_draft(
        triage_dir, 555, severity="S3", priority="P3",
        title="root cause found", blocks_add=[12345],
    )
    body = client.get("/").text
    assert "/bug-start 555" in body
    assert "navigator.clipboard.writeText" in body


def test_a1_card_has_no_bugstart_button(triage_dir: Path) -> None:
    """§1a cards (NI without P/S) must NOT show the /bug-start button."""
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


# ─── Apply / Skip buttons wired up ──────────────────────────────────

def test_apply_button_posts_to_apply_endpoint(triage_dir: Path) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'hx-post="/draft/5551/apply"' in body


def test_skip_button_posts_to_skip_endpoint(triage_dir: Path) -> None:
    write_draft(triage_dir, 5551, severity="S3", priority="P3")
    body = client.get("/").text
    assert 'hx-post="/draft/5551/skip"' in body


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


# ─── Queue tab (queue inspector) ────────────────────────────────────

def test_queue_tab_present_in_topbar(triage_dir: Path) -> None:
    """The Queue tab is registered alongside the other tabs."""
    body = client.get("/").text
    assert 'href="?tab=queue"' in body


def test_queue_tab_empty_state(triage_dir: Path) -> None:
    body = client.get("/?tab=queue").text
    assert "Nothing queued" in body
    # No queue rows should render.
    assert 'class="queue-row' not in body


def test_queue_tab_lists_all_action_types(triage_dir: Path) -> None:
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    write_draft(triage_dir, 2, ni_targets=["x@y"])
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"shorten it","ts":"2026-05-30T14:30:00+00:00"}\n'
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T14:35:00+00:00"}\n'
        '{"action":"bug-start","bug_id":2,"ts":"2026-05-30T15:00:00+00:00"}\n'
    )
    body = client.get("/?tab=queue").text
    # All three action badges render.
    assert "queue-badge--refine" in body
    assert "queue-badge--apply" in body
    assert "queue-badge--bug-start" in body
    # Refine row shows the feedback text.
    assert "shorten it" in body


def test_queue_tab_bug_id_links_to_correct_section(triage_dir: Path) -> None:
    """Clicking a bug id in the queue tab jumps to that bug in its
    own section tab."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")  # §1b → "triaged"
    write_draft(triage_dir, 2, ni_targets=["x@y"])             # §1a → "needs-info"
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"x","ts":"2026-05-30T00:00:00+00:00"}\n'
        '{"action":"refine","bug_id":2,"feedback":"y","ts":"2026-05-30T00:01:00+00:00"}\n'
    )
    body = client.get("/?tab=queue").text
    # Jinja's autoescape in attribute context emits raw `&` here
    # (matches existing deck-nav links elsewhere in the templates).
    assert 'href="?tab=triaged&bug=1"' in body
    assert 'href="?tab=needs-info&bug=2"' in body


def test_queue_tab_renders_orphan_row_without_link(triage_dir: Path) -> None:
    """Queue entries for bugs that no longer have a pending JSON
    still render but the bug id isn't a link."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"apply","bug_id":9999,"ts":"2026-05-30T00:00:00+00:00"}\n'
    )
    body = client.get("/?tab=queue").text
    assert "9999" in body
    assert "queue-bug-link--orphan" in body


def test_queue_tab_remove_button_carries_action_bug_ts(
    triage_dir: Path,
) -> None:
    """The ✕ button must include action + bug_id + ts so the server
    can identify the exact entry."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"apply","bug_id":555,"ts":"2026-05-30T14:35:00+00:00"}\n'
    )
    body = client.get("/?tab=queue").text
    assert 'hx-post="/queue/remove"' in body
    # The hx-vals payload must include all three discriminators.
    assert '"action": "apply"' in body
    assert '"bug_id": 555' in body
    assert "2026-05-30T14:35:00+00:00" in body


def test_queue_tab_count_in_tab_badge(triage_dir: Path) -> None:
    """The Queue tab's count badge reflects the JSONL length."""
    queue_path = triage_dir / "claude-queue.jsonl"
    queue_path.write_text(
        '{"action":"refine","bug_id":1,"feedback":"a","ts":"2026-05-30T00:00:00+00:00"}\n'
        '{"action":"apply","bug_id":1,"ts":"2026-05-30T00:01:00+00:00"}\n'
        '{"action":"bug-start","bug_id":2,"ts":"2026-05-30T00:02:00+00:00"}\n'
    )
    body = client.get("/").text
    import re
    # Find the queue tab link and check its trailing count is 3.
    m = re.search(
        r'href="\?tab=queue"[^>]*>.*?<span class="count">(\d+)</span>',
        body, re.DOTALL,
    )
    assert m is not None
    assert m.group(1) == "3"
