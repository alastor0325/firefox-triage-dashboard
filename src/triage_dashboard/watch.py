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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


_BUG_FILE_RE = re.compile(r"^bug-(\d+)\.json$")
# Matches the two file shapes the dashboard cares about in the
# investigation dir: the in-flight lock file written by /bug-start
# section 6, and the investigation md file it eventually produces.
_INVESTIGATION_FILE_RE = re.compile(
    r"^bug-(\d+)-(?:investigating\.lock|investigation\.md)$"
)


@dataclass(frozen=True)
class WatchEvent:
    type: str             # "draft-changed" | "draft-deleted" | "log-changed" | "watch-changed" | "investigation-changed"
    bug_id: int | None = None


def event_for_path(
    path: Path,
    *,
    deleted: bool,
    triage_dir: Path,
    investigation_dir: Path | None = None,
) -> Optional[WatchEvent]:
    """Map a filesystem path to a WatchEvent, or None if uninteresting.

    Triage-dir shapes:
      pending/bug-N.json              →  draft-changed / draft-deleted
      triage-log.json                 →  log-changed
      ni-watch.json                   →  watch-changed
      claude-queue.jsonl              →  queue-changed

    Investigation-dir shapes (when `investigation_dir` is provided):
      bug-N-investigating.lock        →  investigation-changed
      bug-N-investigation.md          →  investigation-changed

    Everything else (temp files, watch-tmp queue files, dotfiles, paths
    outside both directories) is ignored.
    """
    if investigation_dir is not None:
        try:
            inv_rel = path.relative_to(investigation_dir)
        except ValueError:
            inv_rel = None
        if inv_rel is not None and len(inv_rel.parts) == 1:
            m = _INVESTIGATION_FILE_RE.match(inv_rel.parts[0])
            if m:
                return WatchEvent(
                    type="investigation-changed",
                    bug_id=int(m.group(1)),
                )
            return None

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
    if parts == ("claude-queue.jsonl",):
        return WatchEvent(type="queue-changed")
    return None


class FileWatchBroker:
    """Pub/sub broker. Thread-safe emit; async subscribe."""

    # How long a self-write suppression lasts. Covers the watchdog latency
    # between the app writing a file and the OS event arriving.
    _SELF_WRITE_WINDOW = 2.0

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[WatchEvent]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # path -> monotonic expiry. Events for these paths are ignored: the
        # app itself just wrote them (e.g. an owner-CC/NI toggle) and already
        # updated the UI via the htmx swap, so a full SSE refresh would be a
        # jarring no-op that wipes scroll/selection.
        self._suppressed: dict[str, float] = {}

    def suppress_path(self, path) -> None:
        """Ignore filesystem events for `path` for a short window — call this
        immediately *before* the app writes the file itself."""
        self._suppressed[str(path)] = time.monotonic() + self._SELF_WRITE_WINDOW

    def suppressed(self, path) -> bool:
        exp = self._suppressed.get(str(path))
        if exp is None:
            return False
        if time.monotonic() > exp:
            self._suppressed.pop(str(path), None)
            return False
        return True

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

    def __init__(
        self,
        triage_dir: Path,
        broker: FileWatchBroker,
        *,
        investigation_dir: Path | None = None,
    ) -> None:
        self.triage_dir = triage_dir
        self.investigation_dir = investigation_dir
        self.broker = broker

    def _emit_for(self, src_path: str, *, deleted: bool) -> None:
        # Skip events the app suppressed (its own writes — UI already updated).
        if not deleted and self.broker.suppressed(src_path):
            return
        ev = event_for_path(
            Path(src_path),
            deleted=deleted,
            triage_dir=self.triage_dir,
            investigation_dir=self.investigation_dir,
        )
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
    """Wraps a watchdog Observer that watches `triage_dir` recursively
    (and, when supplied, the `investigation_dir` non-recursively for
    lock-file + investigation.md changes)."""

    def __init__(
        self,
        triage_dir: Path,
        broker: FileWatchBroker,
        *,
        investigation_dir: Path | None = None,
    ) -> None:
        self.triage_dir = triage_dir
        self.investigation_dir = investigation_dir
        self.broker = broker
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self.triage_dir.mkdir(parents=True, exist_ok=True)
        handler = TriageDirEventHandler(
            self.triage_dir,
            self.broker,
            investigation_dir=self.investigation_dir,
        )
        self._observer = Observer()
        self._observer.schedule(handler, str(self.triage_dir), recursive=True)
        if self.investigation_dir is not None:
            # Investigation files live flat in the directory (no subdirs),
            # so a non-recursive watch is enough. We also create the dir
            # so watchdog doesn't fail on first start before /bug-start
            # has ever run.
            self.investigation_dir.mkdir(parents=True, exist_ok=True)
            self._observer.schedule(
                handler, str(self.investigation_dir), recursive=False,
            )
        self._observer.start()

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._observer = None
