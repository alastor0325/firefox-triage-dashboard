"""File-system watcher for ~/firefox-triage/, with a pub/sub broker.

Used by the dashboard's SSE endpoint to push updates to the browser when
pending JSONs, the triage-log, or the watch list change underfoot (e.g.
from a terminal `/triage` run).

Two halves:
- `event_for_path` + `TriageDirEventHandler`: map raw watchdog filesystem
  events into our domain events (`WatchEvent`). Pure / synchronous.
- `FileWatchBroker`: async pub/sub. Watchdog fires from a background
  thread; the broker bridges to the asyncio loop via call_soon_threadsafe.
- `TriageDirWatcher`: glues a watchdog `Observer` to the handler + broker.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


_BUG_FILE_RE = re.compile(r"^bug-(\d+)\.json$")


@dataclass(frozen=True)
class WatchEvent:
    type: str             # "draft-changed" | "draft-deleted" | "log-changed" | "watch-changed"
    bug_id: int | None = None


def event_for_path(
    path: Path, *, deleted: bool, triage_dir: Path
) -> Optional[WatchEvent]:
    """Map a filesystem path to a WatchEvent, or None if uninteresting.

    We only care about three shapes:
      pending/bug-N.json     →  draft-changed / draft-deleted
      triage-log.json        →  log-changed
      ni-watch.json          →  watch-changed

    Everything else (temp files, watch-tmp queue files, dotfiles, paths
    outside `triage_dir`) is ignored.
    """
    try:
        rel = path.relative_to(triage_dir)
    except ValueError:
        return None

    parts = rel.parts
    if len(parts) == 2 and parts[0] == "pending":
        m = _BUG_FILE_RE.match(parts[1])
        if not m:
            return None
        bug_id = int(m.group(1))
        return WatchEvent(
            type="draft-deleted" if deleted else "draft-changed",
            bug_id=bug_id,
        )
    if parts == ("triage-log.json",):
        return WatchEvent(type="log-changed")
    if parts == ("ni-watch.json",):
        return WatchEvent(type="watch-changed")
    return None


class FileWatchBroker:
    """Pub/sub broker. Thread-safe emit; async subscribe."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[WatchEvent]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Call once from the asyncio event loop at app startup."""
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue[WatchEvent]:
        q: asyncio.Queue[WatchEvent] = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[WatchEvent]) -> None:
        self._subscribers.discard(q)

    def emit(self, event: WatchEvent) -> None:
        """Safe to call from any thread (typically the watchdog observer)."""
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event: WatchEvent) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # One slow subscriber should not block the broker. Drop and move on.
                pass


class TriageDirEventHandler(FileSystemEventHandler):
    """Translates watchdog FS events → WatchEvents and pushes them on the broker."""

    def __init__(self, triage_dir: Path, broker: FileWatchBroker) -> None:
        self.triage_dir = triage_dir
        self.broker = broker

    def _emit_for(self, src_path: str, *, deleted: bool) -> None:
        ev = event_for_path(Path(src_path), deleted=deleted, triage_dir=self.triage_dir)
        if ev is not None:
            self.broker.emit(ev)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit_for(event.src_path, deleted=False)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit_for(event.src_path, deleted=False)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit_for(event.src_path, deleted=True)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._emit_for(event.src_path, deleted=True)
        if event.dest_path:
            self._emit_for(event.dest_path, deleted=False)


class TriageDirWatcher:
    """Wraps a watchdog Observer that watches `triage_dir` recursively."""

    def __init__(self, triage_dir: Path, broker: FileWatchBroker) -> None:
        self.triage_dir = triage_dir
        self.broker = broker
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self.triage_dir.mkdir(parents=True, exist_ok=True)
        handler = TriageDirEventHandler(self.triage_dir, self.broker)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.triage_dir), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._observer = None
