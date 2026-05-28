"""FastAPI app: serves the read-only triage dashboard."""

from __future__ import annotations

import html as _html
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import claude_queue, data

PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str | None = None) -> HTMLResponse:
    triage_dir = data.triage_dir_from_env()
    drafts = data.load_drafts(triage_dir)
    groups = data.group_by_section(drafts)
    watch = data.load_watch(triage_dir)
    stats = data.compute_stats(drafts, watch)
    active_tab = _resolve_active_tab(tab, groups)
    active_marker = SLUG_TO_MARKER[active_tab]
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
