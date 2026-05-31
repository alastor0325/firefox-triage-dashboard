"""Tests for the /events SSE endpoint.

The endpoint is a thin wrapper around `sse_event_stream(queue, is_disconnected)`
— we test that generator directly because it holds all the streaming logic.

The HTTP wrapper (subscribe → run generator → unsubscribe → wrap in
`StreamingResponse(..., media_type="text/event-stream")`) is ~10 lines of
visible glue. We don't test it via TestClient because `client.stream` plus
a long-lived SSE response leaves the connection open and ties up the test
runner; the wiring is verified end-to-end in the browser once the htmx
hookup lands in Commit C.
"""

from __future__ import annotations

import asyncio

from triage_dashboard import app as app_module
from triage_dashboard.watch import WatchEvent


# ─── format_sse_event (pure helper) ─────────────────────────────────

def test_format_sse_event_with_bug_id() -> None:
    out = app_module.format_sse_event(
        WatchEvent(type="draft-changed", bug_id=42)
    )
    assert "event: draft-changed" in out
    assert '"type": "draft-changed"' in out
    assert '"bug_id": 42' in out
    assert out.endswith("\n\n")


def test_format_sse_event_omits_bug_id_when_none() -> None:
    out = app_module.format_sse_event(WatchEvent(type="log-changed"))
    assert "event: log-changed" in out
    assert '"type": "log-changed"' in out
    assert "bug_id" not in out


def test_format_sse_event_shape() -> None:
    """SSE wire format: `event: X\\ndata: {...}\\n\\n` — three lines, one blank."""
    out = app_module.format_sse_event(WatchEvent(type="watch-changed"))
    lines = out.split("\n")
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")
    assert lines[2] == ""
    assert lines[3] == ""


# ─── sse_event_stream (the actual streaming generator) ─────────────

def _run(coro):
    return asyncio.run(coro)


def _make_disconnect_after(n_checks: int):
    """Returns an async callable that yields False (`n_checks - 1`) times then
    True, so the generator processes a bounded number of iterations."""
    counter = {"i": 0}
    async def is_disconnected():
        counter["i"] += 1
        return counter["i"] >= n_checks
    return is_disconnected


def test_stream_yields_emitted_event() -> None:
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(WatchEvent(type="log-changed"))
        chunks = []
        async for c in app_module.sse_event_stream(
            q, _make_disconnect_after(2), keepalive_seconds=0.5
        ):
            chunks.append(c)
            if len(chunks) >= 1:
                break
        return chunks
    chunks = _run(go())
    assert len(chunks) == 1
    assert "event: log-changed" in chunks[0]
    assert '"type": "log-changed"' in chunks[0]


def test_stream_yields_keepalive_on_idle_timeout() -> None:
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        chunks = []
        async for c in app_module.sse_event_stream(
            q, _make_disconnect_after(3), keepalive_seconds=0.05
        ):
            chunks.append(c)
            if len(chunks) >= 1:
                break
        return chunks
    chunks = _run(go())
    assert len(chunks) == 1
    assert chunks[0].startswith(": keepalive")


def test_stream_exits_when_disconnected() -> None:
    """If is_disconnected() returns True before queue.get yields, exit cleanly."""
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        async def always_disconnected():
            return True
        chunks = []
        async for c in app_module.sse_event_stream(
            q, always_disconnected, keepalive_seconds=0.05
        ):
            chunks.append(c)
        return chunks
    assert _run(go()) == []


def test_stream_yields_multiple_events_in_order() -> None:
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(WatchEvent(type="draft-changed", bug_id=1))
        q.put_nowait(WatchEvent(type="draft-changed", bug_id=2))
        q.put_nowait(WatchEvent(type="draft-changed", bug_id=3))
        chunks = []
        async for c in app_module.sse_event_stream(
            q, _make_disconnect_after(10), keepalive_seconds=0.05
        ):
            chunks.append(c)
            if len(chunks) >= 3:
                break
        return chunks
    chunks = _run(go())
    assert len(chunks) == 3
    assert '"bug_id": 1' in chunks[0]
    assert '"bug_id": 2' in chunks[1]
    assert '"bug_id": 3' in chunks[2]


def test_stream_yields_investigation_changed_event() -> None:
    """An investigation-changed WatchEvent flows through sse_event_stream
    as `event: investigation-changed` with a bug_id payload."""
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(WatchEvent(type="investigation-changed", bug_id=1234))
        chunks = []
        async for c in app_module.sse_event_stream(
            q, _make_disconnect_after(2), keepalive_seconds=0.5
        ):
            chunks.append(c)
            if len(chunks) >= 1:
                break
        return chunks
    chunks = _run(go())
    assert len(chunks) == 1
    assert "event: investigation-changed" in chunks[0]
    assert '"type": "investigation-changed"' in chunks[0]
    assert '"bug_id": 1234' in chunks[0]


def test_format_sse_event_investigation_changed() -> None:
    out = app_module.format_sse_event(
        WatchEvent(type="investigation-changed", bug_id=42)
    )
    assert "event: investigation-changed" in out
    assert '"bug_id": 42' in out


def test_stream_keepalive_seconds_is_respected() -> None:
    """Tiny keepalive value means the comment fires quickly; 0.5s would
    timeout the test. We use 0.05s and bound iterations to make this fast."""
    import time
    async def go():
        q: asyncio.Queue = asyncio.Queue()
        start = time.monotonic()
        chunks = []
        async for c in app_module.sse_event_stream(
            q, _make_disconnect_after(2), keepalive_seconds=0.05
        ):
            chunks.append(c)
            if len(chunks) >= 1:
                break
        elapsed = time.monotonic() - start
        return chunks, elapsed
    chunks, elapsed = _run(go())
    assert chunks[0].startswith(": keepalive")
    # Must have waited roughly the keepalive interval, not much more.
    assert elapsed < 0.5


