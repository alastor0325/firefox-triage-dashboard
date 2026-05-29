"""FastAPI app: serves the read-only triage dashboard."""

from __future__ import annotations

import html as _html
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import claude_queue, data, descriptions

PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["parse_description"] = descriptions.parse_description
templates.env.filters["linkify"] = descriptions.linkify
templates.env.filters["render_markdown"] = descriptions.render_markdown
templates.env.filters["filter_key_comments"] = descriptions.filter_key_comments

app = FastAPI(title="Triage Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Tabs: (slug, section marker | None, label)
# A None marker means the tab isn't a triage-section bucket (e.g. Watching).
TABS = [
    ("triaged", "§1b", "Triaged"),
    ("needs-info", "§1a", "Needs Info"),
    ("close", "§1c", "Close"),
    ("watching", None, "Watching"),
]
SLUG_TO_MARKER = {slug: marker for slug, marker, _ in TABS}
DRAFT_TAB_SLUGS = {slug for slug, marker, _ in TABS if marker}


def _resolve_active_tab(
    requested: str | None, groups: dict
) -> str:
    """Pick the active tab: requested if valid, else first non-empty draft section."""
    if requested in SLUG_TO_MARKER:
        return requested
    for slug, marker, _ in TABS:
        if marker and groups.get(marker):
            return slug
    return "triaged"


def _resolve_active_draft(
    bucket: list[data.Draft], requested: int | None
) -> data.Draft | None:
    """Pick the focused card: ?bug=<id> if it's in the current bucket,
    else the first item, else None."""
    if not bucket:
        return None
    if requested is not None:
        match = next((d for d in bucket if d.bug_id == requested), None)
        if match is not None:
            return match
    return bucket[0]


def _deck_nav_info(
    bucket: list[data.Draft], active: data.Draft | None
) -> dict:
    """Position, total, and prev/next bug_ids for the deck-nav strip."""
    if not bucket or active is None:
        return {
            "index": 0, "total": 0,
            "prev_bug_id": None, "next_bug_id": None,
        }
    total = len(bucket)
    i = next((j for j, d in enumerate(bucket) if d.bug_id == active.bug_id), 0)
    return {
        "index": i + 1,    # 1-based for display
        "total": total,
        "prev_bug_id": bucket[i - 1].bug_id if i > 0 else None,
        "next_bug_id": bucket[i + 1].bug_id if i < total - 1 else None,
    }


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request, tab: str | None = None, bug: int | None = None
) -> HTMLResponse:
    triage_dir = data.triage_dir_from_env()
    drafts = data.load_drafts(triage_dir)
    groups = data.group_by_section(drafts)
    watch = data.load_watch(triage_dir)
    stats = data.compute_stats(drafts, watch)
    active_tab = _resolve_active_tab(tab, groups)
    active_marker = SLUG_TO_MARKER[active_tab]
    active_bucket = groups.get(active_marker, []) if active_marker else []
    active_draft = _resolve_active_draft(active_bucket, bug)
    deck_nav = _deck_nav_info(active_bucket, active_draft)
    counts_by_slug = {
        slug: (
            len(groups.get(marker, [])) if marker
            else (len(watch) if slug == "watching" else 0)
        )
        for slug, marker, _ in TABS
    }
    # htmx tab clicks request just the content body, not the whole page,
    # so the topbar/tabs/scroll position stay stable.
    is_htmx = request.headers.get("HX-Request") == "true"
    template_name = "_content.html" if is_htmx else "index.html"
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "drafts": drafts,
            "groups": groups,
            "watch": watch,
            "stats": stats,
            "dateline": data.now_local_dateline(),
            "tabs": TABS,
            "active_tab": active_tab,
            "active_marker": active_marker,
            "active_bucket": active_bucket,
            "active_draft": active_draft,
            "deck_nav": deck_nav,
            "counts_by_slug": counts_by_slug,
        },
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/draft/{bug_id}/refine")
def refine_draft(
    request: Request, bug_id: int, feedback: str = Form(default="")
):
    """Queue a refine request for the AI to revise this draft.

    The draft itself isn't touched here; we just append to claude-queue.jsonl.
    The /process-queue skill is what eventually consumes the queue and
    rewrites the pending JSON with a revised draft.

    htmx clients (form submissions on the dashboard) get back a small HTML
    fragment they can swap into a status div. Everything else (curl, scripts,
    tests) gets JSON.
    """
    triage_dir = data.triage_dir_from_env()
    if not (triage_dir / "pending" / f"bug-{bug_id}.json").is_file():
        raise HTTPException(status_code=404, detail="no pending draft for that bug")
    try:
        entry = claude_queue.append_refine(
            triage_dir, bug_id=bug_id, feedback=feedback
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if request.headers.get("HX-Request") == "true":
        safe_feedback = _html.escape(entry["feedback"])
        return HTMLResponse(
            f'<p class="feedback-queued">'
            f'<span class="spinner" aria-hidden="true">⟳</span> '
            f'Revising — feedback queued: <q>{safe_feedback}</q>'
            f'</p>'
        )
    return JSONResponse({"ok": True, "queued_at": entry["ts"]})
