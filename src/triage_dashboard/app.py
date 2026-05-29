"""FastAPI app: serves the read-only triage dashboard."""

from __future__ import annotations

import asyncio
import html as _html
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import applier, backend, claude_queue, data, descriptions, watch as watch_mod

PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["parse_description"] = descriptions.parse_description
templates.env.filters["linkify"] = descriptions.linkify
templates.env.filters["render_markdown"] = descriptions.render_markdown
templates.env.filters["filter_key_comments"] = descriptions.filter_key_comments

# Module-level broker so tests can `broker.emit(...)` to drive the SSE endpoint
# without going through the actual filesystem watcher.
broker = watch_mod.FileWatchBroker()
_watcher: watch_mod.TriageDirWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bind the broker to the running event loop and start the filesystem
    watcher on the configured triage_dir at startup; stop it on shutdown."""
    global _watcher
    broker.bind_loop(asyncio.get_running_loop())
    _watcher = watch_mod.TriageDirWatcher(data.triage_dir_from_env(), broker)
    _watcher.start()
    try:
        yield
    finally:
        if _watcher is not None:
            _watcher.stop()
            _watcher = None


app = FastAPI(title="Triage Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def format_sse_event(event: watch_mod.WatchEvent) -> str:
    """Format a `WatchEvent` as a single SSE message:

        event: <type>
        data: <json payload>
        (blank line)

    `bug_id` is omitted from the payload when None so log/watch events
    don't carry a stray null field.
    """
    payload = {"type": event.type}
    if event.bug_id is not None:
        payload["bug_id"] = event.bug_id
    return f"event: {event.type}\ndata: {json.dumps(payload)}\n\n"


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


_SSE_KEEPALIVE_SECONDS = 3


async def sse_event_stream(queue, is_disconnected, *, keepalive_seconds: float = _SSE_KEEPALIVE_SECONDS):
    """Pure SSE event generator. Pulls events off `queue` until
    `is_disconnected()` returns True, emitting keepalive comments when
    idle. Decoupled from `Request` so it's directly testable: pass an
    `asyncio.Queue` and an async-callable that returns the disconnect state."""
    while True:
        if await is_disconnected():
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        yield format_sse_event(event)


@app.get("/events")
async def sse_events(request: Request) -> StreamingResponse:
    """Stream filesystem-change events as Server-Sent Events.

    The browser keeps this connection open; the broker pushes
    `WatchEvent`s when ~/firefox-triage/ files change (whether from
    terminal `/triage` runs, `/process-queue`, or any other tool).
    """
    queue = await broker.subscribe()

    async def stream():
        try:
            async for chunk in sse_event_stream(queue, request.is_disconnected):
                yield chunk
        finally:
            await broker.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _load_pending_or_404(bug_id: int) -> dict:
    triage_dir = data.triage_dir_from_env()
    path = triage_dir / "pending" / f"bug-{bug_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no pending draft for that bug")
    return json.loads(path.read_text())


def _backend_result_response(
    request: Request,
    action: str,
    bug_id: int,
    result: backend.BackendResult,
):
    """HTML fragment for htmx swaps; JSON for everything else."""
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="_plan.html",
            context={
                "action": action,
                "bug_id": bug_id,
                "result": result,
                "is_live": os.environ.get(backend.LIVE_ENV_VAR) == "1",
            },
        )
    return JSONResponse({
        "action": action,
        "bug_id": bug_id,
        "ok": result.ok,
        "output": result.output,
        "side_effects": result.side_effects,
        "actions": [
            {"kind": a.kind, "description": a.description}
            for a in result.actions
        ],
    })


@app.post("/draft/{bug_id}/apply")
def apply_draft(request: Request, bug_id: int):
    """Apply the pending draft via the configured backend.

    Default backend is the mock, which only computes the plan. Real
    Bugzilla writes require `TRIAGE_DASHBOARD_LIVE=1` AND a real
    implementation of `BugzillaCLIBackend` (see PLAN.md Phase 4.5).
    If LIVE=1 but the implementation isn't there, this returns 501.
    """
    pending = _load_pending_or_404(bug_id)
    try:
        result = backend.get_backend().apply(bug_id, pending)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return _backend_result_response(request, "apply", bug_id, result)


@app.post("/draft/{bug_id}/skip")
def skip_draft(request: Request, bug_id: int):
    """Skip the pending draft via the configured backend.

    Default is the mock; real impl is gated, see PLAN.md Phase 4.5.
    """
    pending = _load_pending_or_404(bug_id)
    try:
        result = backend.get_backend().skip(bug_id, pending)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    return _backend_result_response(request, "skip", bug_id, result)


def _count_queue(triage_dir: Path) -> int:
    """Number of `refine` entries currently in the queue file."""
    path = triage_dir / claude_queue.QUEUE_FILE
    if not path.is_file():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action") == "refine":
            n += 1
    return n


@app.get("/queue/count")
def queue_count() -> dict:
    return {"count": _count_queue(data.triage_dir_from_env())}


@app.post("/queue/prepare")
def queue_prepare() -> dict:
    """Build the Claude drain prompt: write CLAUDE_QUEUE_PROMPT.md and
    return a short string suitable for copying into a Claude session."""
    return claude_queue.prepare_queue_drain(data.triage_dir_from_env())


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
