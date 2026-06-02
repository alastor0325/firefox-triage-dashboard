"""FastAPI app: serves the read-only triage dashboard."""

from __future__ import annotations

import asyncio
import html as _html
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

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
templates.env.filters["level_class"] = data.level_class
templates.env.filters["split_see_also"] = data.split_see_also
templates.env.filters["parse_affected_file"] = data.parse_affected_file
templates.env.globals["is_regression"] = data.is_regression
templates.env.globals["is_emergency"] = data.is_emergency
templates.env.globals["is_stalled"] = data.is_stalled
templates.env.globals["is_new_this_week"] = data.is_new_this_week
templates.env.filters["version_only"] = data.version_only

# Cache-bust /static/style.css with the file's mtime captured at import
# time. Browsers refetch when the URL changes; on the server, restarting
# (which is how the rest of the app picks up changes anyway) is what
# refreshes this value.
_STYLE_CSS = STATIC_DIR / "style.css"
try:
    CSS_VERSION = str(int(_STYLE_CSS.stat().st_mtime))
except OSError:
    CSS_VERSION = "0"
templates.env.globals["css_version"] = CSS_VERSION

# Module-level broker so tests can `broker.emit(...)` to drive the SSE endpoint
# without going through the actual filesystem watcher.
broker = watch_mod.FileWatchBroker()
_watcher: watch_mod.TriageDirWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bind the broker to the running event loop and start the filesystem
    watcher on the configured triage_dir + investigation_dir at startup;
    stop it on shutdown."""
    global _watcher
    broker.bind_loop(asyncio.get_running_loop())
    _watcher = watch_mod.TriageDirWatcher(
        data.triage_dir_from_env(),
        broker,
        investigation_dir=data.investigation_dir_from_env(),
    )
    _watcher.start()
    try:
        yield
    finally:
        if _watcher is not None:
            _watcher.stop()
            _watcher = None


app = FastAPI(title="Triage Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_store_dynamic_html(request, call_next):
    """Never let the browser cache the dynamic HTML. Tab switches are htmx
    GETs; without this the browser heuristically caches them and serves a
    stale partial after a feature changes (e.g. a tab missing the 'New'
    tag). Static assets (text/css under /static, version-busted with ?v=)
    keep their cacheability — only text/html gets no-store."""
    response = await call_next(request)
    ctype = response.headers.get("content-type", "")
    if ctype.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


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


# Single source of truth for per-tab metadata. The tab strip's label
# (in index.html) and the rail header's title + help text (in rail.html)
# both read from here, so they can't drift apart.
#
# `marker` is the §-section bucket the tab represents; None means the
# tab isn't a draft-section bucket (e.g. Watching).
# `help` is the info-icon tooltip text; tabs without an info-icon
# (Watching) leave it empty.
TAB_INFO = {
    "triaged": {
        "marker": "§1b",
        "title": "Analyzed",
        "help": (
            "Bugs you have triaged. The draft is the public Bugzilla "
            "comment with your analysis, plus the severity/priority you "
            "set. Apply queues bugzilla-cli apply (you confirm [y/N] at "
            "the terminal); click Applied again to undo."
        ),
    },
    "needs-info": {
        "marker": "§1a",
        "title": "Needs Info",
        "help": (
            "Bugs missing critical info from the reporter. The draft is "
            "the needinfo question. Apply queues bugzilla-cli apply "
            "(you confirm [y/N] at the terminal) to set NI on Bugzilla "
            "and post the question."
        ),
    },
    "close": {
        "marker": "§1c",
        "title": "Close / Reassign",
        "help": (
            "Bugs being closed (INCOMPLETE / INVALID / WORKSFORME) or "
            "moved to another component. The draft is the closing or "
            "handoff comment. Apply queues bugzilla-cli apply (you "
            "confirm [y/N] at the terminal) to post the comment and "
            "apply the resolution or reassignment."
        ),
    },
    "watching": {
        "marker": None,
        "title": "Awaiting reply",
        "help": "",
    },
}

# Tabs: (slug, section marker | None, label) — derived from TAB_INFO so
# the labels can't drift from the rail-head titles.
TABS = [
    (slug, info["marker"], info["title"])
    for slug, info in TAB_INFO.items()
]
SLUG_TO_MARKER = {slug: marker for slug, marker, _ in TABS}
DRAFT_TAB_SLUGS = {slug for slug, marker, _ in TABS if marker}
MARKER_TO_SLUG = {marker: slug for slug, marker, _ in TABS if marker}
# §-marker → short label, for the section badge in global search results.
MARKER_LABEL = {marker: label for slug, marker, label in TABS if marker}


def _slug_for_section(marker: str) -> str:
    """Map a §-marker back to the tab slug used in URLs."""
    return MARKER_TO_SLUG.get(marker, "triaged")


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


def _parse_iso8601(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse. Returns None for empty or malformed
    strings; trailing 'Z' is treated as UTC (datetime.fromisoformat
    accepts 'Z' in Python 3.11+)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _compute_is_stale(
    active_draft: data.Draft | None,
    investigation: data.Investigation | None,
) -> bool:
    """True when the bug's last_activity is more recent than the
    investigation's timestamp — i.e. the bug has moved since the
    investigation was written.

    Falsy on any missing field or unparsable timestamp so the card never
    crashes on bad data; "no signal" defaults to "not stale".
    """
    if active_draft is None or investigation is None:
        return False
    ctx = active_draft.bug_context
    if ctx is None or not ctx.last_activity:
        return False
    inv_dt = _parse_iso8601(investigation.investigated_at)
    act_dt = _parse_iso8601(ctx.last_activity)
    if inv_dt is None or act_dt is None:
        return False
    # Comparing naive and aware datetimes raises TypeError; if the two
    # timestamps disagree on tz-awareness, defensively report "not stale"
    # rather than crashing on data we can't safely compare.
    if (inv_dt.tzinfo is None) != (act_dt.tzinfo is None):
        return False
    return inv_dt < act_dt


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request, tab: str | None = None, bug: int | None = None,
    q: str | None = None,
) -> HTMLResponse:
    triage_dir = data.triage_dir_from_env()
    drafts = data.load_drafts(triage_dir)
    groups = data.group_by_section(drafts)
    watch = data.load_watch(triage_dir)
    stats = data.compute_stats(drafts, watch)
    # Per-watched-bug detail map for the Awaiting tab: the archived applied
    # draft's bug_context report and any persisted investigation. Only the
    # small watch list is involved, so unconditional loading is fine.
    watch_reports = {}
    for w in watch:
        applied = data.load_applied_draft(triage_dir, w.bug_id)
        inv = data.load_investigation(w.bug_id)
        watch_reports[w.bug_id] = {
            "draft": applied,
            "report": applied.bug_context if applied else None,
            "investigation": inv,
            "is_stale": _compute_is_stale(applied, inv),
        }
    active_tab = _resolve_active_tab(tab, groups)
    active_marker = SLUG_TO_MARKER[active_tab]
    # Global search (Option B): when q is present the rail+deck show a flat
    # list of matches across ALL sections, each badged with its section.
    query = (q or "").strip()
    search_active = bool(query)
    if search_active:
        active_bucket = data.search_drafts(drafts, query)
        nav_base = "q=" + quote_plus(query)
    else:
        active_bucket = groups.get(active_marker, []) if active_marker else []
        nav_base = "tab=" + active_tab
    active_draft = _resolve_active_draft(active_bucket, bug)
    deck_nav = _deck_nav_info(active_bucket, active_draft)
    pending_feedback = (
        claude_queue.pending_feedback_for(triage_dir, active_draft.bug_id)
        if active_draft is not None else []
    )
    # Only load the investigation for the focused card — disk I/O per
    # pending draft would slow page renders, and the user sees one card
    # at a time anyway.
    investigation = (
        data.load_investigation(active_draft.bug_id)
        if active_draft is not None else None
    )
    is_stale = _compute_is_stale(active_draft, investigation)
    # Whether the focused card's apply is already sitting in the queue —
    # drives the Apply/Applied toggle so the state survives a reload.
    apply_queued = (
        claude_queue.is_apply_queued(triage_dir, active_draft.bug_id)
        if active_draft is not None else False
    )
    # Bug → (section_slug, title) lookup used by the Queue tab to wire
    # each row's bug-id link back to the right card.
    bug_meta_by_id = {
        d.bug_id: {"section_slug": _slug_for_section(d.section), "title": d.title}
        for d in drafts
    }
    queue_rows = claude_queue.all_queued_actions(triage_dir)
    queue_count = len(queue_rows)
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
            "watch_reports": watch_reports,
            "stats": stats,
            "dateline": data.last_updated_dateline(triage_dir),
            "tabs": TABS,
            "tab_info": TAB_INFO,
            "active_tab": active_tab,
            "active_marker": active_marker,
            "active_bucket": active_bucket,
            "active_draft": active_draft,
            "deck_nav": deck_nav,
            "search_active": search_active,
            "search_query": query,
            "nav_base": nav_base,
            "marker_label": MARKER_LABEL,
            "counts_by_slug": counts_by_slug,
            "queue_count": queue_count,
            "pending_feedback": pending_feedback,
            "queue_rows": queue_rows,
            "bug_meta_by_id": bug_meta_by_id,
            "investigation": investigation,
            "is_stale": is_stale,
            "apply_queued": apply_queued,
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
    terminal `/triage` runs, a Claude session draining the queue, or
    any other tool).
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
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"pending draft is unreadable or malformed: {e}",
        )


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


def _apply_toggle_response(
    request: Request,
    *,
    bug_id: int,
    pending: dict,
    queued: bool,
    result: backend.BackendResult | None,
):
    """Render the Apply/Applied toggle for htmx; JSON for everything else.

    The htmx fragment swaps the button into its new state and, out-of-band,
    refreshes the status host (the plan + queued note on apply; empty on
    revert). `result` is None on a revert (no backend plan is computed).
    """
    if request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            request=request,
            name="_apply_toggle.html",
            context={
                "draft": data.draft_from_pending(pending),
                "apply_queued": queued,
                "action": "apply",
                "bug_id": bug_id,
                "result": result,
                "is_live": os.environ.get(backend.LIVE_ENV_VAR) == "1",
            },
        )
    if result is not None:
        return JSONResponse({
            "action": "apply",
            "bug_id": bug_id,
            "ok": result.ok,
            "queued": queued,
            "output": result.output,
            "side_effects": result.side_effects,
            "actions": [
                {"kind": a.kind, "description": a.description}
                for a in result.actions
            ],
        })
    return JSONResponse({
        "action": "apply",
        "bug_id": bug_id,
        "ok": True,
        "queued": False,
        "reverted": True,
        "actions": [],
    })


@app.post("/draft/{bug_id}/apply")
def apply_draft(request: Request, bug_id: int):
    """Toggle the pending draft's apply via the configured backend.

    Apply is reversible. If the draft's `apply` action is already in the
    queue, this click reverts it (removes the queued apply) and the button
    flips back to "Apply". Otherwise it computes the plan and queues the
    apply, and the button flips to "Applied".

    Default backend is the mock, which only computes the plan. Real
    Bugzilla writes require `TRIAGE_DASHBOARD_LIVE=1` AND a real
    implementation of `BugzillaCLIBackend` (see PLAN.md Phase 4.5).
    If LIVE=1 but the implementation isn't there, this returns 501.

    On apply, a single `apply` action is appended to `claude-queue.jsonl`
    (every section). The drain prompt runs `bugzilla-cli apply <id>`; the
    CLI's [y/N] prompt is the production-write gate. `/bug-start` is no
    longer queued here — it runs during triage.
    """
    pending = _load_pending_or_404(bug_id)
    triage_dir = data.triage_dir_from_env()
    if claude_queue.is_apply_queued(triage_dir, bug_id):
        claude_queue.remove_apply(triage_dir, bug_id)
        return _apply_toggle_response(
            request, bug_id=bug_id, pending=pending, queued=False, result=None
        )
    try:
        result = backend.get_backend().apply(bug_id, pending)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    if result.ok:
        claude_queue.append_apply(triage_dir, bug_id=bug_id)
    return _apply_toggle_response(
        request, bug_id=bug_id, pending=pending, queued=result.ok, result=result
    )


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
    """Number of drainable entries currently in the queue file (covers
    every action type the topbar badge represents to the user). Single
    source of truth: `claude_queue.DRAINABLE_ACTIONS`.
    """
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
        if isinstance(obj, dict) and obj.get("action") in claude_queue.DRAINABLE_ACTIONS:
            n += 1
    return n


@app.get("/queue/count")
def queue_count() -> dict:
    return {"count": _count_queue(data.triage_dir_from_env())}


@app.get("/queue/dropdown", response_class=HTMLResponse)
def queue_dropdown(request: Request) -> HTMLResponse:
    """Render the topbar Process queue dropdown content as an HTML
    fragment, fetched on demand (queue-changed SSE) by the client."""
    triage_dir = data.triage_dir_from_env()
    drafts = data.load_drafts(triage_dir)
    bug_meta_by_id = {
        d.bug_id: {"section_slug": _slug_for_section(d.section), "title": d.title}
        for d in drafts
    }
    return templates.TemplateResponse(
        request=request,
        name="_queue_dropdown.html",
        context={
            "queue_rows": claude_queue.all_queued_actions(triage_dir),
            "bug_meta_by_id": bug_meta_by_id,
        },
    )


@app.post("/queue/remove")
def queue_remove(
    request: Request,
    action: str = Form(default=""),
    bug_id: int = Form(default=0),
    ts: str = Form(default=""),
):
    """Remove a single queued entry (any action type).

    Generic counterpart to the per-card refine remove endpoint; powers
    the queue-inspector tab's ✕ buttons. 404 if no entry matches;
    400 on missing/invalid fields.
    """
    if not action.strip() or not ts.strip() or not bug_id:
        raise HTTPException(status_code=400, detail="action, bug_id, and ts are required")
    if action not in claude_queue.DRAINABLE_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")
    triage_dir = data.triage_dir_from_env()
    removed = claude_queue.remove_entry(
        triage_dir, action=action, bug_id=bug_id, ts=ts,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="no matching queue entry")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("")
    return JSONResponse({"ok": True})


@app.post("/queue/prepare")
def queue_prepare() -> dict:
    """Return the clipboard prompt for draining the queue.

    Shape: `{count, prompt, bugs_affected}`. The prompt is a short text
    block (~900B) that tells Claude where the JSONL lives and what to do;
    Claude reads `claude-queue.jsonl` itself rather than the prompt
    embedding the queue contents. Nothing is written to disk.
    """
    return claude_queue.prepare_queue_drain(data.triage_dir_from_env())


@app.post("/draft/{bug_id}/refine")
def refine_draft(
    request: Request, bug_id: int, feedback: str = Form(default="")
):
    """Queue a refine request for the AI to revise this draft.

    The draft itself isn't touched here; we just append to claude-queue.jsonl.
    A separate Claude session drains the queue (invoked via the
    "Process queue" button) and rewrites the pending JSON with a
    revised draft.

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


@app.post("/draft/{bug_id}/refine/remove")
def refine_remove(
    request: Request, bug_id: int, ts: str = Form(default=""),
):
    """Remove one queued refine entry for this bug.

    Identifies the entry by its `ts` (paired with the path bug_id). 404
    when no entry matches; 400 when ts is missing.
    """
    if not ts.strip():
        raise HTTPException(status_code=400, detail="ts is required")
    triage_dir = data.triage_dir_from_env()
    removed = claude_queue.remove_refine(triage_dir, bug_id=bug_id, ts=ts)
    if not removed:
        raise HTTPException(status_code=404, detail="no matching queued feedback")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("")
    return JSONResponse({"ok": True})
