"""Unit tests for triage_dashboard.watch — file-system event mapping + broker."""

from __future__ import annotations

import asyncio
from pathlib import Path

from triage_dashboard import watch


# ─── event_for_path (pure mapping) ──────────────────────────────────

def test_event_for_path_pending_bug_modified(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        tmp_path / "pending" / "bug-2039425.json",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="draft-changed", bug_id=2039425)


def test_event_for_path_pending_bug_deleted(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        tmp_path / "pending" / "bug-1.json",
        deleted=True, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="draft-deleted", bug_id=1)


def test_event_for_path_triage_log(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        tmp_path / "triage-log.json",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="log-changed")


def test_event_for_path_ni_watch(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        tmp_path / "ni-watch.json",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="watch-changed")


def test_event_for_path_claude_queue(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        tmp_path / "claude-queue.jsonl",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="queue-changed")


def test_event_for_path_outside_triage_dir_returns_none(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        Path("/somewhere/else/foo.json"),
        deleted=False, triage_dir=tmp_path,
    )
    assert ev is None


def test_event_for_path_investigating_lock_file(tmp_path: Path) -> None:
    """bug-N-investigating.lock in the investigation dir → investigation-changed."""
    inv_dir = tmp_path / "inv"
    ev = watch.event_for_path(
        inv_dir / "bug-1234-investigating.lock",
        deleted=False,
        triage_dir=tmp_path / "triage",
        investigation_dir=inv_dir,
    )
    assert ev == watch.WatchEvent(type="investigation-changed", bug_id=1234)


def test_event_for_path_investigation_md_file(tmp_path: Path) -> None:
    """bug-N-investigation.md in the investigation dir → investigation-changed."""
    inv_dir = tmp_path / "inv"
    ev = watch.event_for_path(
        inv_dir / "bug-1234-investigation.md",
        deleted=False,
        triage_dir=tmp_path / "triage",
        investigation_dir=inv_dir,
    )
    assert ev == watch.WatchEvent(type="investigation-changed", bug_id=1234)


def test_event_for_path_investigation_dir_lock_deletion(tmp_path: Path) -> None:
    """Lock removal also emits investigation-changed (so the card flips
    back to its post-investigation state)."""
    inv_dir = tmp_path / "inv"
    ev = watch.event_for_path(
        inv_dir / "bug-99-investigating.lock",
        deleted=True,
        triage_dir=tmp_path / "triage",
        investigation_dir=inv_dir,
    )
    assert ev == watch.WatchEvent(type="investigation-changed", bug_id=99)


def test_event_for_path_investigation_dir_unrelated_file_ignored(
    tmp_path: Path,
) -> None:
    """Files in the investigation dir that don't match the bug-N pattern
    (READMEs, scratch files, etc.) emit no event."""
    inv_dir = tmp_path / "inv"
    ev = watch.event_for_path(
        inv_dir / "README.md",
        deleted=False,
        triage_dir=tmp_path / "triage",
        investigation_dir=inv_dir,
    )
    assert ev is None


def test_event_for_path_no_investigation_dir_arg_still_works(
    tmp_path: Path,
) -> None:
    """Backward-compat: callers that don't pass investigation_dir still
    get the triage-dir behaviour."""
    ev = watch.event_for_path(
        tmp_path / "pending" / "bug-7.json",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev == watch.WatchEvent(type="draft-changed", bug_id=7)


def test_event_for_path_unrelated_file_returns_none(tmp_path: Path) -> None:
    """Random files in the triage dir (e.g. a stray .txt) emit no event."""
    ev = watch.event_for_path(
        tmp_path / "pending" / "not-a-bug.txt",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev is None


def test_event_for_path_tmp_files_ignored(tmp_path: Path) -> None:
    """Editors / atomic writes often write .tmp / .swp files we don't care about."""
    ev = watch.event_for_path(
        tmp_path / "pending" / "bug-1.json.tmp",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev is None


def test_event_for_path_watch_tmp_ignored(tmp_path: Path) -> None:
    """watch-tmp-N.json files are queue scratch — don't surface them as events."""
    ev = watch.event_for_path(
        tmp_path / "watch-tmp-2039425.json",
        deleted=False, triage_dir=tmp_path,
    )
    assert ev is None


# ─── FileWatchBroker (pub/sub) ─────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_broker_emit_reaches_subscriber() -> None:
    async def go():
        broker = watch.FileWatchBroker()
        broker.bind_loop(asyncio.get_running_loop())
        q = await broker.subscribe()
        broker.emit(watch.WatchEvent(type="draft-changed", bug_id=1))
        await asyncio.sleep(0)   # let call_soon_threadsafe fire
        return q.get_nowait()
    assert _run(go()) == watch.WatchEvent(type="draft-changed", bug_id=1)


def test_broker_emits_to_all_subscribers() -> None:
    async def go():
        broker = watch.FileWatchBroker()
        broker.bind_loop(asyncio.get_running_loop())
        q1 = await broker.subscribe()
        q2 = await broker.subscribe()
        broker.emit(watch.WatchEvent(type="log-changed"))
        await asyncio.sleep(0)
        return q1.get_nowait(), q2.get_nowait()
    a, b = _run(go())
    assert a == b == watch.WatchEvent(type="log-changed")


def test_broker_unsubscribe_stops_delivery() -> None:
    async def go():
        broker = watch.FileWatchBroker()
        broker.bind_loop(asyncio.get_running_loop())
        q = await broker.subscribe()
        await broker.unsubscribe(q)
        broker.emit(watch.WatchEvent(type="log-changed"))
        await asyncio.sleep(0)
        return q.empty()
    assert _run(go()) is True


def test_broker_emit_without_bound_loop_is_silent_noop() -> None:
    """Emitting before bind_loop() must not raise — happens at app shutdown."""
    broker = watch.FileWatchBroker()
    broker.emit(watch.WatchEvent(type="log-changed"))  # must not raise


def test_broker_handles_full_subscriber_queue() -> None:
    """If one subscriber's queue is full, others still receive the event."""
    async def go():
        broker = watch.FileWatchBroker()
        broker.bind_loop(asyncio.get_running_loop())
        slow_q = asyncio.Queue(maxsize=1)
        broker._subscribers.add(slow_q)        # pre-fill the slow one
        slow_q.put_nowait(watch.WatchEvent(type="log-changed"))
        fast_q = await broker.subscribe()

        broker.emit(watch.WatchEvent(type="draft-changed", bug_id=42))
        await asyncio.sleep(0)
        return fast_q.get_nowait()
    assert _run(go()) == watch.WatchEvent(type="draft-changed", bug_id=42)


# ─── TriageDirEventHandler (watchdog → broker glue) ─────────────────

class _RecordingBroker:
    """Sync stand-in for FileWatchBroker — captures emitted events."""
    def __init__(self):
        self.events: list[watch.WatchEvent] = []
    def emit(self, ev: watch.WatchEvent) -> None:
        self.events.append(ev)


class _FakeFsEvent:
    def __init__(self, src_path: str, is_directory: bool = False,
                 dest_path: str | None = None):
        self.src_path = src_path
        self.is_directory = is_directory
        self.dest_path = dest_path


def test_handler_on_created_emits_draft_changed(tmp_path: Path) -> None:
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_created(_FakeFsEvent(str(tmp_path / "pending" / "bug-7.json")))
    assert broker.events == [watch.WatchEvent(type="draft-changed", bug_id=7)]


def test_handler_on_deleted_emits_draft_deleted(tmp_path: Path) -> None:
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_deleted(_FakeFsEvent(str(tmp_path / "pending" / "bug-9.json")))
    assert broker.events == [watch.WatchEvent(type="draft-deleted", bug_id=9)]


def test_handler_on_moved_emits_both(tmp_path: Path) -> None:
    """File renamed from bug-1.json → bug-1.json.bak should emit delete only
    (the new name doesn't match the bug-N.json pattern)."""
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_moved(_FakeFsEvent(
        src_path=str(tmp_path / "pending" / "bug-1.json"),
        dest_path=str(tmp_path / "pending" / "bug-1.json.bak"),
    ))
    assert broker.events == [watch.WatchEvent(type="draft-deleted", bug_id=1)]


def test_handler_ignores_directory_events(tmp_path: Path) -> None:
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_modified(_FakeFsEvent(str(tmp_path / "pending"), is_directory=True))
    assert broker.events == []


def test_handler_ignores_unrelated_paths(tmp_path: Path) -> None:
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_modified(_FakeFsEvent(str(tmp_path / "pending" / "not-a-bug.txt")))
    assert broker.events == []


def test_handler_emits_investigation_changed_from_investigation_dir(
    tmp_path: Path,
) -> None:
    """Handler constructed with both triage_dir and investigation_dir maps
    lock-file creations under investigation_dir to investigation-changed."""
    inv_dir = tmp_path / "inv"
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(
        tmp_path, broker, investigation_dir=inv_dir,
    )
    h.on_created(_FakeFsEvent(str(inv_dir / "bug-1234-investigating.lock")))
    assert broker.events == [
        watch.WatchEvent(type="investigation-changed", bug_id=1234)
    ]


def test_handler_emits_investigation_changed_on_md_change(
    tmp_path: Path,
) -> None:
    inv_dir = tmp_path / "inv"
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(
        tmp_path, broker, investigation_dir=inv_dir,
    )
    h.on_modified(_FakeFsEvent(str(inv_dir / "bug-9-investigation.md")))
    assert broker.events == [
        watch.WatchEvent(type="investigation-changed", bug_id=9)
    ]


def test_handler_without_investigation_dir_backward_compat(
    tmp_path: Path,
) -> None:
    """Constructor still accepts a single triage_dir (no investigation_dir)
    and behaves exactly as before."""
    broker = _RecordingBroker()
    h = watch.TriageDirEventHandler(tmp_path, broker)
    h.on_created(_FakeFsEvent(str(tmp_path / "pending" / "bug-7.json")))
    assert broker.events == [watch.WatchEvent(type="draft-changed", bug_id=7)]


# ─── TriageDirWatcher (Observer lifecycle) ──────────────────────────

def test_watcher_start_creates_triage_dir_if_missing(tmp_path: Path) -> None:
    """TriageDirWatcher.start() must mkdir the triage_dir so the underlying
    watchdog observer doesn't fail on first run before /triage has ever
    been invoked."""
    triage = tmp_path / "never-existed"
    assert not triage.exists()
    w = watch.TriageDirWatcher(triage, watch.FileWatchBroker())
    try:
        w.start()
        assert triage.is_dir()
    finally:
        w.stop()


def test_watcher_start_creates_investigation_dir_if_missing(
    tmp_path: Path,
) -> None:
    """Same for investigation_dir when it's provided — watchdog can't
    schedule on a path that doesn't exist."""
    triage = tmp_path / "triage"
    inv = tmp_path / "inv"
    assert not inv.exists()
    w = watch.TriageDirWatcher(
        triage, watch.FileWatchBroker(), investigation_dir=inv,
    )
    try:
        w.start()
        assert inv.is_dir()
    finally:
        w.stop()


def test_watcher_start_is_idempotent(tmp_path: Path) -> None:
    """Calling start() twice must not raise and must not stack observers —
    the second call is a no-op when _observer is already set."""
    w = watch.TriageDirWatcher(tmp_path / "triage", watch.FileWatchBroker())
    try:
        w.start()
        first_observer = w._observer
        w.start()
        assert w._observer is first_observer, (
            "start() must be idempotent — repeated calls leak observers"
        )
    finally:
        w.stop()


def test_watcher_stop_before_start_is_noop(tmp_path: Path) -> None:
    """stop() before start() must not raise — covers the "lifespan
    aborts before bind" code path."""
    w = watch.TriageDirWatcher(tmp_path / "triage", watch.FileWatchBroker())
    w.stop()
    assert w._observer is None


def test_watcher_stop_twice_is_noop(tmp_path: Path) -> None:
    """Repeated stop() calls are safe — shutdown can be invoked from
    multiple paths (lifespan exit + signal handler)."""
    w = watch.TriageDirWatcher(tmp_path / "triage", watch.FileWatchBroker())
    w.start()
    w.stop()
    w.stop()
    assert w._observer is None


def test_watcher_emits_on_real_file_create(tmp_path: Path) -> None:
    """End-to-end: a real watchdog Observer scheduled on the triage_dir
    must surface a draft-changed event when a pending/bug-N.json is
    created. This is the smoke test that ties handler + observer +
    broker + threading together."""
    import time as _time
    triage = tmp_path / "triage"
    triage.mkdir()
    (triage / "pending").mkdir()
    events: list[watch.WatchEvent] = []

    class _Recorder:
        def emit(self, ev: watch.WatchEvent) -> None:
            events.append(ev)

    w = watch.TriageDirWatcher(triage, _Recorder())  # type: ignore[arg-type]
    try:
        w.start()
        # Watchdog observer needs a moment to wire up before it sees events.
        _time.sleep(0.1)
        (triage / "pending" / "bug-42.json").write_text("{}")
        # Poll up to 2s for the event to come through the watchdog thread.
        deadline = _time.time() + 2.0
        while _time.time() < deadline and not events:
            _time.sleep(0.05)
    finally:
        w.stop()
    assert any(
        e.type == "draft-changed" and e.bug_id == 42 for e in events
    ), f"expected draft-changed for bug 42; got {events!r}"
