"""Read-only loaders for the triage data directory (~/firefox-triage/)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

# Socorro crash IDs look like bp-<uuid-ish>, e.g. bp-12345678-abcd-...
_CRASH_ID_RE = re.compile(r"bp-[a-f0-9-]+", re.IGNORECASE)

DEFAULT_TRIAGE_DIR = Path.home() / "firefox-triage"

Section = Literal["§1b", "§1a", "§1c"]

# Recognised Mozilla severity/priority levels. Anything else (legacy
# values like "critical"/"normal", empty strings, garbage) maps to the
# "unknown" warning treatment so card-head pills are never unstyled.
_SEVERITY_LEVELS = {"S1", "S2", "S3", "S4"}
_PRIORITY_LEVELS = {"P1", "P2", "P3", "P4", "P5"}


def level_class(value: str | None) -> str:
    """Map a severity/priority level to a CSS modifier suffix used by
    `.badge-level--<suffix>` in card.html. Unrecognised or missing
    values map to "unknown" so the warning treatment is applied."""
    if not value:
        return "unknown"
    v = str(value).strip().upper()
    if v in _SEVERITY_LEVELS or v in _PRIORITY_LEVELS:
        return v.lower()
    return "unknown"


@dataclass
class BugContext:
    """Rich context snapshot of the bug at /triage draft time.

    Optional — older pending JSON files were written without this block.
    All fields default to empty so partial contexts render gracefully.
    """

    description_excerpt: str = ""
    platform: str = ""
    firefox_version: str = ""
    reporter_email: str = ""
    reporter_name: str = ""
    last_activity: str = ""
    inventory_present: list[str] = field(default_factory=list)
    inventory_missing: list[str] = field(default_factory=list)
    see_also: list[dict] = field(default_factory=list)
    recent_comments: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    ai_reasoning: str = ""
    # Current Bugzilla state of the bug at draft time. Used by the rail
    # to render bug-state tags ("S3·P2", "no P/S", "crash"). The /triage
    # skill is expected to populate these; older pending JSONs without
    # them parse cleanly via the defaults.
    current_severity: str = ""
    current_priority: str = ""
    keywords: list[str] = field(default_factory=list)

    @property
    def is_crash(self) -> bool:
        """True if the bug looks crash-related: a `crash` keyword
        (case-insensitive) or a Socorro `bp-<uuid>` ID in the description."""
        if any(k.lower() == "crash" for k in self.keywords):
            return True
        return bool(_CRASH_ID_RE.search(self.description_excerpt or ""))


def _parse_bug_context(raw: Any) -> BugContext | None:
    if not isinstance(raw, dict):
        return None
    return BugContext(
        description_excerpt=str(raw.get("description_excerpt") or ""),
        platform=str(raw.get("platform") or ""),
        firefox_version=str(raw.get("firefox_version") or ""),
        reporter_email=str(raw.get("reporter_email") or ""),
        reporter_name=str(raw.get("reporter_name") or ""),
        last_activity=str(raw.get("last_activity") or ""),
        inventory_present=list(raw.get("inventory_present") or []),
        inventory_missing=list(raw.get("inventory_missing") or []),
        see_also=[e for e in (raw.get("see_also") or []) if isinstance(e, dict)],
        recent_comments=[e for e in (raw.get("recent_comments") or []) if isinstance(e, dict)],
        attachments=[e for e in (raw.get("attachments") or []) if isinstance(e, dict)],
        ai_reasoning=str(raw.get("ai_reasoning") or ""),
        current_severity=str(raw.get("current_severity") or ""),
        current_priority=str(raw.get("current_priority") or ""),
        keywords=[str(k) for k in (raw.get("keywords") or [])],
    )


@dataclass
class Draft:
    """One pending triage draft from ~/firefox-triage/pending/bug-*.json."""

    bug_id: int
    title: str
    comment: str
    ni_targets: list[str]
    priority: str | None
    severity: str | None
    blocks_add: list[int]
    cc_add: list[str]
    resolution: str | None
    keywords_add: list[str]
    product: str | None
    component: str | None
    created_at: str
    section: Section
    bug_context: BugContext | None = None
    # Legacy enrichment fields (kept for backward-compat with prior code paths)
    bug_component: str | None = None
    bug_reporter: str | None = None


def classify_section(d: dict) -> Section:
    """Infer §1b / §1a / §1c from the pending JSON fields.

    The /triage skill writes pending drafts without an explicit section
    label, but the field shape is sufficient to classify:

    - §1c: setting a resolution (INCOMPLETE/FIXED/etc.) or reassigning
      product/component
    - §1b: setting priority/severity (root-cause triaged, fields fixed)
    - §1a: everything else (needinfo with P/S unchanged)
    """
    if d.get("resolution") or d.get("product") or d.get("component"):
        return "§1c"
    if d.get("priority") or d.get("severity"):
        return "§1b"
    return "§1a"


def load_drafts(triage_dir: Path = DEFAULT_TRIAGE_DIR) -> list[Draft]:
    pending = triage_dir / "pending"
    if not pending.is_dir():
        return []
    drafts: list[Draft] = []
    for path in sorted(pending.glob("bug-*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        drafts.append(
            Draft(
                bug_id=int(data.get("bug_id") or 0),
                title=data.get("title") or "(no title)",
                comment=data.get("comment") or "",
                ni_targets=list(data.get("ni_targets") or []),
                priority=data.get("priority"),
                severity=data.get("severity"),
                blocks_add=list(data.get("blocks_add") or []),
                cc_add=list(data.get("cc_add") or []),
                resolution=data.get("resolution"),
                keywords_add=list(data.get("keywords_add") or []),
                product=data.get("product"),
                component=data.get("component"),
                created_at=data.get("created_at") or "",
                section=classify_section(data),
                bug_context=_parse_bug_context(data.get("bug_context")),
            )
        )
    return drafts


def group_by_section(drafts: Iterable[Draft]) -> dict[Section, list[Draft]]:
    groups: dict[Section, list[Draft]] = {"§1b": [], "§1a": [], "§1c": []}
    for d in drafts:
        groups[d.section].append(d)
    return groups


@dataclass
class LogEntry:
    bug_id: int
    date: str
    component: str
    reporter: str
    decision: str
    reason: str
    priority: str | None
    severity: str | None


def load_log(
    triage_dir: Path = DEFAULT_TRIAGE_DIR, limit: int = 20
) -> list[LogEntry]:
    path = triage_dir / "triage-log.json"
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(entries, list):
        return []
    out: list[LogEntry] = []
    for e in entries[-limit:][::-1]:
        if not isinstance(e, dict):
            continue
        out.append(
            LogEntry(
                bug_id=int(e.get("bug_id") or 0),
                date=e.get("date") or "",
                component=e.get("component") or "",
                reporter=e.get("reporter") or "",
                decision=e.get("decision") or "",
                reason=e.get("reason") or "",
                priority=e.get("priority"),
                severity=e.get("severity"),
            )
        )
    return out


@dataclass
class WatchEntry:
    bug_id: int
    title: str
    ni_targets: list[str]
    added_at: str


def load_watch(triage_dir: Path = DEFAULT_TRIAGE_DIR) -> list[WatchEntry]:
    path = triage_dir / "ni-watch.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    # ni-watch.json may be either a list of entries or a dict keyed by bug_id.
    items: list[dict] = []
    if isinstance(data, dict):
        for bug_id, entry in data.items():
            if isinstance(entry, dict):
                items.append({"bug_id": bug_id, **entry})
    elif isinstance(data, list):
        items = [e for e in data if isinstance(e, dict)]
    out: list[WatchEntry] = []
    for e in items:
        out.append(
            WatchEntry(
                bug_id=int(e.get("bug_id") or 0),
                title=e.get("title") or "",
                ni_targets=list(e.get("ni_targets") or []),
                added_at=e.get("added_at") or "",
            )
        )
    return out


@dataclass
class Stats:
    pending_total: int
    pending_by_section: dict[Section, int]
    watching: int

    @property
    def section_label(self) -> dict[Section, str]:
        return {"§1b": "triaged", "§1a": "needs info", "§1c": "close"}


def compute_stats(
    drafts: list[Draft], watch: list[WatchEntry]
) -> Stats:
    by_section: dict[Section, int] = {"§1b": 0, "§1a": 0, "§1c": 0}
    for d in drafts:
        by_section[d.section] += 1
    return Stats(
        pending_total=len(drafts),
        pending_by_section=by_section,
        watching=len(watch),
    )


def triage_dir_from_env() -> Path:
    """Resolve the triage data directory from $TRIAGE_DIR or the default."""
    override = os.environ.get("TRIAGE_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_TRIAGE_DIR


def now_local_dateline() -> str:
    """Human-friendly date for the top bar (e.g. 'Thursday, May 28')."""
    now = datetime.now()
    return now.strftime("%A, %B %-d")
