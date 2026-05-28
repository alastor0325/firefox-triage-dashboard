"""Regression tests for the htmx partial-swap fix.

Background: tab navigation used to trigger a full page reload, which caused
visible flicker (topbar redrew, scroll position reset). The fix is in
commit 5a25a08 — when the request carries the HX-Request header, the
server returns just the tab body fragment (no <html>, no <head>, no
topbar). Anything that brings back the full-page reload would regress
the smoothness of the tab UI.

These tests pin down the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import write_draft
from triage_dashboard.app import app


client = TestClient(app)
HTMX_HEADERS = {"HX-Request": "true"}


def test_hx_request_omits_doctype(triage_dir: Path) -> None:
    """A partial swap must not contain <!doctype> — it's not a full document."""
    body = client.get("/", headers=HTMX_HEADERS).text
    assert "<!doctype" not in body.lower()
    assert "<html" not in body


def test_hx_request_omits_topbar(triage_dir: Path) -> None:
    """The topbar lives outside the swap target; it must not be in the partial."""
    body = client.get("/", headers=HTMX_HEADERS).text
    assert 'class="topbar"' not in body


def test_hx_request_omits_tabs_nav(triage_dir: Path) -> None:
    """The tabs nav lives outside the swap target; it must not be in the partial."""
    body = client.get("/", headers=HTMX_HEADERS).text
    # The tabs <nav class="tabs"> is also outside the hx-target=#tab-content
    # region. If it shows up here we're sending too much over the wire.
    assert 'class="tabs"' not in body


def test_hx_request_returns_card_body(triage_dir: Path) -> None:
    """The partial must contain the cards for the requested section."""
    write_draft(triage_dir, 4242, severity="S3", priority="P3", title="x")
    body = client.get("/?tab=triaged", headers=HTMX_HEADERS).text
    assert 'id="card-4242"' in body


def test_hx_partial_for_watching_returns_watch_list(triage_dir: Path) -> None:
    (triage_dir / "ni-watch.json").write_text(
        json.dumps([
            {"bug_id": 7777, "title": "watched", "ni_targets": ["a@b"],
             "added_at": "2026-05-28"}
        ])
    )
    body = client.get("/?tab=watching", headers=HTMX_HEADERS).text
    assert "watch-item" in body
    assert "7777" in body


def test_full_page_still_works_without_hx_header(triage_dir: Path) -> None:
    """A non-htmx GET still returns the whole page (defence in depth)."""
    body = client.get("/").text
    assert "<!doctype" in body.lower()
    assert 'class="topbar"' in body


def test_hx_partial_is_strictly_smaller_than_full_page(triage_dir: Path) -> None:
    """The partial must be smaller than the full page — otherwise we're
    accidentally sending the whole thing over the wire on every tab click."""
    write_draft(triage_dir, 1, severity="S3", priority="P3")
    full = client.get("/").text
    partial = client.get("/", headers=HTMX_HEADERS).text
    assert len(partial) < len(full)
