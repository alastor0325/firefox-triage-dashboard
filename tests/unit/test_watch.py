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


def test_event_for_path_outside_triage_dir_returns_none(tmp_path: Path) -> None:
    ev = watch.event_for_path(
        Path("/somewhere/else/foo.json"),
        deleted=False, triage_dir=tmp_path,
    )
    assert ev is None


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
