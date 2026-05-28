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


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    triage_dir = data.triage_dir_from_env()
    drafts = data.load_drafts(triage_dir)
    groups = data.group_by_section(drafts)
    log = data.load_log(triage_dir, limit=20)
    watch = data.load_watch(triage_dir)
    stats = data.compute_stats(drafts, watch)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "drafts": drafts,
            "groups": groups,
            "log": log,
            "watch": watch,
            "stats": stats,
            "dateline": data.now_local_dateline(),
        },
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
