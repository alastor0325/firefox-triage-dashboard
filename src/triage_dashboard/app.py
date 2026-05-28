"""FastAPI app: serves the read-only triage dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import data

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
    return templates.TemplateResponse(
        request=request,
        name="index.html",
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
