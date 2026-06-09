"""Read-only loaders for the triage data directory (~/firefox-triage/)."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

# Socorro crash IDs look like bp-<uuid-ish>, e.g. bp-12345678-abcd-...
_CRASH_ID_RE = re.compile(r"bp-[a-f0-9-]+", re.IGNORECASE)

DEFAULT_TRIAGE_DIR = Path.home() / "firefox-triage"
DEFAULT_INVESTIGATION_DIR = Path.home() / ".fx-bug-toolkit" / "bug-investigation"

# Frontmatter delimiter for investigation files; we only parse YAML when
# the file opens with `---\n` and we can find a matching closing `---`.
_FRONTMATTER_DELIM = "---"


class _InvestigationYamlLoader(yaml.SafeLoader):
    """SafeLoader variant that does NOT auto-convert ISO-8601 timestamps
    into datetime objects. We want `investigated_at` and similar fields
    to round-trip as the original string (e.g. '2026-05-30T20:15:00Z'),
    so they compare cleanly against other ISO strings server-side."""


# Strip the implicit timestamp resolver so 'YYYY-MM-DDTHH:MM:SSZ' stays a string.
_InvestigationYamlLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

Section = Literal["§1b", "§1a", "§1c"]

# Recognised Mozilla severity/priority levels, most→least severe. These ordered
# tuples are the single source for both the Will-apply override dropdowns and
# the (unordered) validation sets below. Anything else (legacy values like
# "critical"/"normal", empty strings, garbage) maps to the "unknown" warning
# treatment so card-head pills are never unstyled.
SEVERITY_OPTIONS = ("S1", "S2", "S3", "S4")
PRIORITY_OPTIONS = ("P1", "P2", "P3", "P4", "P5")
_SEVERITY_LEVELS = frozenset(SEVERITY_OPTIONS)
_PRIORITY_LEVELS = frozenset(PRIORITY_OPTIONS)


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


# Attachment-type inference. When the attachment carries a `content_type` (the
# MIME type BMO records), we trust it for media — that's exact, and catches
# images/videos attached with a descriptive, extension-less name. Otherwise we
# classify from the filename extension, falling back to the URL host (Firefox
# Profiler share links have no extension). `is_image` drives the in-page
# lightbox; `kind` drives the colour chip; `label` is the human tag.
_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "ico"}
_VIDEO_EXTS = {"mp4", "webm", "mov", "mkv", "avi", "m4v", "ogv"}
_AUDIO_EXTS = {"mp3", "wav", "ogg", "oga", "flac", "m4a", "aac", "opus"}
_ARCHIVE_EXTS = {"zip", "gz", "tgz", "tar", "7z", "rar", "xz", "bz2", "zst"}
_LOG_EXTS = {"log", "moz_log", "txt"}


def _meta_from_content_type(ctype: str) -> dict | None:
    """Classify image/video/audio attachments from their MIME type. Returns None
    for anything else (incl. empty), so the extension heuristic handles it — the
    extension labels for json/html/log/archive are already accurate."""
    if not ctype or "/" not in ctype:
        return None
    major, sub = ctype.split("/", 1)
    sub = sub.split(";", 1)[0].strip()                 # drop ";charset=…"
    label_sub = sub.split("+", 1)[0].rsplit(".", 1)[-1].upper()  # svg+xml→SVG
    if major == "image":
        return {"label": f"{label_sub} image", "kind": "image", "is_image": True}
    if major == "video":
        return {"label": f"{label_sub} video", "kind": "video", "is_image": False}
    if major == "audio":
        return {"label": f"{label_sub} audio", "kind": "audio", "is_image": False}
    return None


def attachment_meta(att: dict) -> dict:
    """Classify a bug attachment for display.

    Returns `{"label", "kind", "is_image"}`. Pure (no I/O) so it's unit-tested
    directly and used as the `attachment_meta` Jinja filter in _bug_report.html.
    """
    att = att or {}
    name = str(att.get("name") or "").strip()
    url = str(att.get("url") or "")

    # Trust the MIME type for media when present (exact; catches extension-less
    # screenshots/recordings); fall back to filename/host heuristics otherwise.
    by_mime = _meta_from_content_type(str(att.get("content_type") or "").strip().lower())
    if by_mime:
        return by_mime

    base = name.split("?", 1)[0].rsplit("/", 1)[-1].lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else ""
    host = url.split("/")[2].lower() if "://" in url else ""

    if ext in _IMAGE_EXTS:
        return {"label": f"{ext.upper()} image", "kind": "image", "is_image": True}
    if ext in _VIDEO_EXTS:
        return {"label": f"{ext.upper()} video", "kind": "video", "is_image": False}
    if ext in _AUDIO_EXTS:
        return {"label": f"{ext.upper()} audio", "kind": "audio", "is_image": False}
    if "profiler" in name.lower() or "share.firefox.dev" in host \
            or "profiler.firefox.com" in host:
        return {"label": "profiler", "kind": "profiler", "is_image": False}
    if ext == "json" or base.endswith(".json.gz"):
        return {"label": "JSON", "kind": "data", "is_image": False}
    if ext in ("html", "htm"):
        return {"label": "HTML", "kind": "html", "is_image": False}
    if ext in _LOG_EXTS:
        return {"label": "log", "kind": "log", "is_image": False}
    if ext in _ARCHIVE_EXTS:
        return {"label": "archive", "kind": "archive", "is_image": False}
    if ext:
        return {"label": ext.upper(), "kind": "file", "is_image": False}
    return {"label": "link", "kind": "link", "is_image": False}


# Whole-word match on "regress" so labels like "regressor" / "regressed
# by" / "regression" count, but a label like "progression" or "egress"
# does not. Anchored at a word boundary on the left only — "regression
# test" still counts, which matches user intent (it's still a regressor
# relationship).
_REGRESSOR_LABEL_RE = re.compile(r"\bregress", re.IGNORECASE)


_AFFECTED_FILE_RE = re.compile(
    r"^(?P<path>.*?)(?:#L(?P<l1>\d+)(?:-L(?P<l2>\d+))?)?$"
)


def parse_affected_file(s: str) -> dict:
    """Split a path#L<n> (or path#L<n>-L<m>) entry from /bug-start's
    frontmatter into its parts.

    Returned shape:
      {
        "path": str,             # bare searchfox-relative path
        "line_start": int|None,  # first line (or None for whole-file)
        "line_end":   int|None,  # last line of a range (None for single)
        "display":    str,       # `path` or `path:42` or `path:42-50`
        "url_suffix": str,       # "" or "#42" — appended to searchfox URL
      }

    Empty / malformed inputs are tolerated — they return an entry with
    `path=""` rather than raising, so a stray entry in YAML can't 500
    the dashboard.
    """
    s = (s or "").strip()
    m = _AFFECTED_FILE_RE.match(s)
    if not m:
        return {
            "path": s, "line_start": None, "line_end": None,
            "display": s, "url_suffix": "",
        }
    path = m.group("path")
    l1 = m.group("l1")
    l2 = m.group("l2")
    if l1 is None:
        return {
            "path": path, "line_start": None, "line_end": None,
            "display": path, "url_suffix": "",
        }
    line_start = int(l1)
    line_end = int(l2) if l2 is not None else None
    if line_end is not None:
        display = f"{path}:{line_start}-{line_end}"
    else:
        display = f"{path}:{line_start}"
    return {
        "path": path,
        "line_start": line_start,
        "line_end": line_end,
        "display": display,
        # Searchfox doesn't support a range anchor — point at the start.
        "url_suffix": f"#{line_start}",
    }


def split_see_also(entries: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Split see_also entries into (regressors, similar).

    A "regressor" is any entry whose label matches `\\bregress`
    (case-insensitive) — e.g. "regressor", "regressed by", "regression".
    Everything else (or entries without a label) is similar.
    """
    regressors: list[dict] = []
    similar: list[dict] = []
    for entry in entries or []:
        label = str(entry.get("label") or "")
        if label and _REGRESSOR_LABEL_RE.search(label):
            regressors.append(entry)
        else:
            similar.append(entry)
    return regressors, similar


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
    filed: str = ""
    last_activity: str = ""
    affected_versions: str = ""  # triage judgment: "all", "151+", a specific version, …
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
    # Assignee at draft time. assigned_to is the email (the reliable
    # signal); assigned_to_name is the real name for display. Both default
    # to "" — an unassigned bug carries "" or "nobody@mozilla.org".
    assigned_to: str = ""
    assigned_to_name: str = ""
    # One-line brief written by /triage when the bug moved OUT of the
    # Awaiting tab into a §-tab on re-triage (e.g. "Reporter attached a
    # media log → re-triaged §1b"). Empty for the common case.
    change_note: str = ""
    # Needinfo flags ALREADY pending on the bug at draft time (set by
    # anyone — the reporter, another dev, an earlier triage). Distinct from
    # the draft's `ni_targets`, which are the NIs *this* draft will request.
    # Each entry: {"requestee": email, "setter": email, "since": "YYYY-MM-DD"}.
    # Surfaced on the card so a draft doesn't re-request an NI that's already
    # outstanding (BMO collapses a duplicate request into a no-op).
    pending_needinfos: list[dict] = field(default_factory=list)

    @property
    def is_crash(self) -> bool:
        """True if the bug looks crash-related: a `crash` keyword
        (case-insensitive) or a Socorro `bp-<uuid>` ID in the description."""
        if any(k.lower() == "crash" for k in self.keywords):
            return True
        return bool(_CRASH_ID_RE.search(self.description_excerpt or ""))


def _parse_dupe_of(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _parse_bug_context(raw: Any) -> BugContext | None:
    if not isinstance(raw, dict):
        return None
    return BugContext(
        description_excerpt=str(raw.get("description_excerpt") or ""),
        platform=str(raw.get("platform") or ""),
        firefox_version=str(raw.get("firefox_version") or ""),
        reporter_email=str(raw.get("reporter_email") or ""),
        reporter_name=str(raw.get("reporter_name") or ""),
        filed=str(raw.get("filed") or ""),
        last_activity=str(raw.get("last_activity") or ""),
        affected_versions=str(raw.get("affected_versions") or ""),
        inventory_present=list(raw.get("inventory_present") or []),
        inventory_missing=list(raw.get("inventory_missing") or []),
        see_also=[e for e in (raw.get("see_also") or []) if isinstance(e, dict)],
        recent_comments=[e for e in (raw.get("recent_comments") or []) if isinstance(e, dict)],
        attachments=[e for e in (raw.get("attachments") or []) if isinstance(e, dict)],
        ai_reasoning=str(raw.get("ai_reasoning") or ""),
        current_severity=str(raw.get("current_severity") or ""),
        current_priority=str(raw.get("current_priority") or ""),
        keywords=[str(k) for k in (raw.get("keywords") or [])],
        assigned_to=str(raw.get("assigned_to") or ""),
        assigned_to_name=str(raw.get("assigned_to_name") or ""),
        change_note=str(raw.get("change_note") or ""),
        pending_needinfos=[
            e for e in (raw.get("pending_needinfos") or []) if isinstance(e, dict)
        ],
    )


def redundant_needinfos(draft: "Draft") -> list[str]:
    """The draft's `ni_targets` that are ALREADY pending on the bug.

    Re-requesting a needinfo that's already outstanding is a no-op on BMO
    (it collapses into the existing flag), so surfacing the overlap lets the
    triager drop the redundant request before applying. Matching is
    case-insensitive on the requestee email. Returns [] when there's no
    bug_context. Preserves the original `ni_targets` spelling in the output."""
    ctx = getattr(draft, "bug_context", None)
    if ctx is None:
        return []
    pending = {
        str(p.get("requestee") or "").strip().lower()
        for p in ctx.pending_needinfos
        if isinstance(p, dict)
    }
    pending.discard("")
    return [t for t in (draft.ni_targets or []) if str(t).strip().lower() in pending]


_EMERGENCY_KEYWORDS = frozenset({"sec-critical", "sec-high", "topcrash"})


def is_regression(draft: "Draft") -> bool:
    """True if triage has classified the draft as a regression, by any signal:
    the bug's current `regression` keyword, a `regressionwindow-wanted` keyword,
    the draft proposing the `regression` keyword (keywords_add), or a regressor
    identified in see_also. The current keyword alone is too narrow — a bug we've
    classified as a regression usually doesn't carry the keyword on Bugzilla yet
    (we propose it via keywords_add). False when no signal is present."""
    if any(str(k).lower() == "regression"
           for k in (getattr(draft, "keywords_add", None) or [])):
        return True
    ctx = getattr(draft, "bug_context", None)
    if ctx is None:
        return False
    kw = [str(k).lower() for k in (ctx.keywords or [])]
    if "regression" in kw or "regressionwindow-wanted" in kw:
        return True
    regressors, _ = split_see_also(ctx.see_also)
    return bool(regressors)


def is_emergency(draft: "Draft") -> bool:
    """True if the draft's bug_context.keywords contains any of
    'sec-critical', 'sec-high', or 'topcrash' (case-insensitive).
    False for drafts without a bug_context."""
    ctx = getattr(draft, "bug_context", None)
    if ctx is None:
        return False
    return any(str(k).lower() in _EMERGENCY_KEYWORDS for k in (ctx.keywords or []))


# A bug is "unassigned" when its assignee email is empty or the Bugzilla
# default-owner sentinel; anything else means a real person owns it.
_UNASSIGNED_EMAILS = frozenset({"", "nobody@mozilla.org"})


def is_taken(arg: "Draft | BugContext | None") -> bool:
    """True if the bug is assigned to a real person — i.e. its
    assigned_to is set and is not the default-unassigned value ('' or
    'nobody@mozilla.org', case-insensitive). Accepts either a Draft (rail)
    or a BugContext directly (the card report partial, which has no Draft
    in scope). False for None or a Draft without a bug_context."""
    if arg is None:
        return False
    ctx = arg if isinstance(arg, BugContext) else getattr(arg, "bug_context", None)
    if ctx is None:
        return False
    return ctx.assigned_to.strip().lower() not in _UNASSIGNED_EMAILS


def assignee_display(ctx: "BugContext | None") -> str:
    """The assignee name to show on the card — real name, falling back to
    the email. Empty when there's no context."""
    if ctx is None:
        return ""
    return ctx.assigned_to_name or ctx.assigned_to


# A bug is "New" on the deck while it was filed within this many days. This
# is recomputed every render from bug_context.filed (never stored), so the
# tag drops automatically once the bug ages out — including on the next
# /triage run.
_NEW_WITHIN_DAYS = 7


def is_new_this_week(draft: "Draft", now: datetime | None = None) -> bool:
    """True if the bug was filed within the last _NEW_WITHIN_DAYS days,
    derived at render time from bug_context.filed. False when there's no
    bug_context, no filed date, or filed is unparseable."""
    ctx = getattr(draft, "bug_context", None)
    if ctx is None:
        return False
    raw = (ctx.filed or "").strip()
    if not raw:
        return False
    try:
        filed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    return timedelta(0) <= (now - filed) <= timedelta(days=_NEW_WITHIN_DAYS)


# How long a watching entry can sit without a reply before it's flagged
# as stalled on the Awaiting reply tab.
_STALLED_AFTER_DAYS = 14


def is_stalled(entry: "WatchEntry", now: datetime | None = None) -> bool:
    """True if the watch entry's added_at is more than 14 days before
    `now` (defaults to datetime.now(UTC)). Missing or unparseable
    added_at returns False — entries with no date are not flagged.

    Accepts both full ISO-8601 timestamps and bare YYYY-MM-DD dates
    (which load_watch may surface from older ni-watch.json formats).
    """
    raw = getattr(entry, "added_at", "") or ""
    if not raw:
        return False
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        return False
    # Normalise both sides to tz-aware UTC so we can subtract them.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - parsed) > timedelta(days=_STALLED_AFTER_DAYS)


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
    dupe_of: int | None = None
    bug_context: BugContext | None = None
    # Legacy enrichment fields (kept for backward-compat with prior code paths)
    bug_component: str | None = None
    bug_reporter: str | None = None
    # Bugzilla `regressed_by` relation: the change(s) that caused this regression
    regressed_by_add: list[int] = field(default_factory=list)
    # Bugzilla `see_also` relation: related bug IDs to add (apply writes them via
    # bugzilla-cli set-fields --see-also-add)
    see_also_add: list[int] = field(default_factory=list)
    # Bugzilla `status` (e.g. ASSIGNED) and `assigned_to` (assignee email) writes
    status: str | None = None
    assigned_to: str | None = None


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


def draft_from_pending(data: dict) -> Draft:
    """Build a Draft from one parsed pending JSON dict."""
    return Draft(
        bug_id=int(data.get("bug_id") or 0),
        title=data.get("title") or "(no title)",
        comment=data.get("comment") or "",
        ni_targets=list(data.get("ni_targets") or []),
        priority=data.get("priority"),
        severity=data.get("severity"),
        blocks_add=list(data.get("blocks_add") or []),
        regressed_by_add=list(data.get("regressed_by_add") or []),
        see_also_add=list(data.get("see_also_add") or []),
        status=data.get("status"),
        assigned_to=data.get("assigned_to"),
        cc_add=list(data.get("cc_add") or []),
        resolution=data.get("resolution"),
        keywords_add=list(data.get("keywords_add") or []),
        product=data.get("product"),
        component=data.get("component"),
        created_at=data.get("created_at") or "",
        section=classify_section(data),
        dupe_of=_parse_dupe_of(data.get("dupe_of")),
        bug_context=_parse_bug_context(data.get("bug_context")),
        bug_component=data.get("bug_component"),
    )


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
        drafts.append(draft_from_pending(data))
    return drafts


def load_applied_draft(
    triage_dir: Path, bug_id: int
) -> Draft | None:
    """Read `applied/bug-<id>.json` (an archived applied draft, same JSON
    shape as a pending draft) and return the parsed Draft. Returns None
    when the archive is missing or corrupt."""
    path = Path(triage_dir) / "applied" / f"bug-{bug_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return draft_from_pending(data)


def _newest_first_key(d: "Draft") -> tuple[str, int]:
    """Sort key: by bug filed-date (creation_time) then bug_id, both
    descending when used with reverse=True. Drafts without a filed date sort
    last (empty string is lowest), tie-broken by bug_id descending."""
    filed = ""
    ctx = getattr(d, "bug_context", None)
    if ctx is not None and ctx.filed:
        filed = ctx.filed
    return (filed, d.bug_id)


def group_by_section(drafts: Iterable[Draft]) -> dict[Section, list[Draft]]:
    """Bucket drafts by section, each bucket sorted newest-filed-first so the
    most recently created bugs appear at the top of the rail and deck."""
    groups: dict[Section, list[Draft]] = {"§1b": [], "§1a": [], "§1c": []}
    for d in drafts:
        groups[d.section].append(d)
    for section in groups:
        groups[section].sort(key=_newest_first_key, reverse=True)
    return groups


def _active_tags(draft: "Draft", now: datetime | None = None) -> list[str]:
    """The render-time tag names a draft currently carries (new / emergency /
    regression) — the same ones shown on the rail. Used so search can match
    them (e.g. 'new' surfaces every New-tagged bug)."""
    tags: list[str] = []
    if is_new_this_week(draft, now):
        tags.append("new")
    if is_emergency(draft):
        tags.append("emergency")
    if is_regression(draft):
        tags.append("regression")
    return tags


def search_drafts(
    drafts: Iterable[Draft], query: str, now: datetime | None = None
) -> list[Draft]:
    """Global search across ALL sections. A draft matches when the query is:
      - a substring of its bug_id, or
      - a (case-insensitive) substring of its title, or
      - a prefix of one of its active tag names (new / emergency /
        regression) — so 'new' surfaces every New-tagged bug.
    Returns newest-filed-first. Empty/whitespace query → empty list."""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    matches = [
        d for d in drafts
        if ql in (d.title or "").lower()
        or q in str(d.bug_id)
        or any(tag.startswith(ql) for tag in _active_tags(d, now))
    ]
    matches.sort(key=_newest_first_key, reverse=True)
    return matches


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
                # bugzilla-cli writes the NI timestamp as `ni_set_date`;
                # older formats used `added_at`. Accept either so the
                # stalled badge and date display work with both.
                added_at=e.get("added_at") or e.get("ni_set_date") or "",
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


@dataclass
class Investigation:
    """Metadata extracted from a /bug-start investigation file's YAML
    frontmatter. All fields default to empty so partial frontmatters
    render gracefully (old files without frontmatter return None from
    load_investigation; callers handle the None case)."""

    bug_id: int
    investigated_at: str = ""
    status: str = ""
    root_cause: str = ""
    affected_files: list[str] = field(default_factory=list)
    regression_range: str | None = None
    related_bugs: list[int] = field(default_factory=list)
    complexity: str = ""
    notes: str = ""
    file_path: str = ""
    # "triage" when /bug-start ran in shallow-triage mode; empty otherwise
    # (treated as deep / not set).
    depth: str = ""


# How long a bug-<id>-investigating.lock file can sit before it's
# considered abandoned (the /bug-start run died, was cancelled, etc.).
_LOCK_STALE_SECONDS = 30 * 60


def _extract_frontmatter(text: str) -> str | None:
    """Return the YAML frontmatter body (without the `---` fences), or
    None when the file doesn't open with `---\\n` or lacks a matching
    closing delimiter."""
    if not text.startswith(_FRONTMATTER_DELIM):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            return "\n".join(lines[1:i])
    return None


def investigation_dir_from_env() -> Path:
    """Resolve the investigation data directory from $FX_BUG_INVESTIGATION_DIR
    or the default ~/.fx-bug-toolkit/bug-investigation/ (shared with the
    fx-bug-toolkit plugin, which writes investigations there)."""
    override = os.environ.get("FX_BUG_INVESTIGATION_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_INVESTIGATION_DIR


def triage_owner() -> str:
    """The triage owner's Bugzilla email from $TRIAGE_OWNER (empty if unset).
    This is the address the per-draft 'CC me' / 'NI me' checkboxes toggle on."""
    return (os.environ.get("TRIAGE_OWNER") or "").strip()


def toggle_in_list(items: list[str], value: str, on: bool) -> list[str]:
    """Pure: return `items` with `value` present iff `on` (de-duplicated,
    order otherwise preserved). Empty `value` is a no-op."""
    out = [x for x in items if x != value]
    if on and value:
        out.append(value)
    return out


def _modify_pending(triage_dir: Path, bug_id: int, mutate) -> bool:
    """Read a pending draft's JSON, apply `mutate(dict)` in place, and persist.
    Returns False (no-op) when the pending file is missing or unreadable. Shared
    read-modify-write mechanics for the pending-draft setters below; callers
    validate the field/value before passing `mutate`."""
    path = triage_dir / "pending" / f"bug-{bug_id}.json"
    if not path.is_file():
        return False
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    mutate(d)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def set_owner_membership(
    triage_dir: Path, bug_id: int, field: str, on: bool
) -> bool:
    """Add/remove the triage owner from a pending draft's `cc_add` or
    `ni_targets` list and persist it. Returns False (no-op) when the owner
    isn't configured, the field is unknown, or the pending file is missing.
    Read-modify-write around the pure `toggle_in_list`."""
    if field not in ("cc_add", "ni_targets"):
        return False
    owner = triage_owner()
    if not owner:
        return False
    return _modify_pending(
        triage_dir, bug_id,
        lambda d: d.update({field: toggle_in_list(list(d.get(field) or []), owner, on)}),
    )


def set_owner_assignment(triage_dir: Path, bug_id: int, on: bool) -> bool:
    """Assign the pending draft to the triage owner (`on`) or clear its
    assignee (`not on`), and persist. Unlike cc/ni this is a scalar field, so
    `on=False` clears `assigned_to` to None. Returns False (no-op) when the
    owner isn't configured or the pending file is missing."""
    owner = triage_owner()
    if not owner:
        return False
    return _modify_pending(
        triage_dir, bug_id,
        lambda d: d.update({"assigned_to": owner if on else None}),
    )


_LEVEL_OPTIONS = {"severity": SEVERITY_OPTIONS, "priority": PRIORITY_OPTIONS}


def level_options(field: str) -> tuple[str, ...]:
    """Ordered selectable values for a draft 'severity' / 'priority' field;
    () for any other field. Pure — backs the Will-apply override dropdowns
    and validates incoming overrides in `set_draft_field`."""
    return _LEVEL_OPTIONS.get(field, ())


def set_draft_field(triage_dir: Path, bug_id: int, field: str, value: str) -> bool:
    """Override a pending draft's 'severity' / 'priority' with an explicit
    value and persist it — the value that will actually be applied this round.
    `value` must be one of the field's allowed levels (case-insensitive).
    Returns False (no-op) on unknown field, invalid value, or missing/unreadable
    pending file. Read-modify-write, mirrors `set_owner_membership`."""
    options = level_options(field)
    if not options:
        return False
    value = str(value or "").strip().upper()
    if value not in options:
        return False
    return _modify_pending(triage_dir, bug_id, lambda d: d.update({field: value}))


def level_change_feedback(field: str, old: str | None, new: str) -> str:
    """Refine-queue feedback for a Will-apply S/P override, so a queued Claude
    refine rewrites the comment's rationale to match the new level. The field
    itself is already persisted by `set_draft_field`; without this the apply
    would set the new level while the posted comment still argues the old one.
    Pure — backs the set_field route's auto-refine enqueue. `field` is
    'severity' or 'priority'."""
    old_disp = old.upper() if old else "unset"
    return (
        f"The Will-apply {field} was changed from {old_disp} to {new} in the "
        f"dashboard. Rewrite the comment so its rationale — and any {field} level "
        f"stated in the text — reflects {new}, keeping the rest of the draft "
        f"intact. The {field} field is already set to {new}; do not change it, "
        f"only make the comment consistent with it."
    )


def strip_frontmatter(text: str) -> str:
    """Return the markdown body after a leading `---`-fenced YAML
    frontmatter block. Returns the text unchanged when it has no
    frontmatter (doesn't open with `---`) or no closing fence (malformed —
    don't silently swallow the whole body). Pure; the /investigation/<id>
    route uses it so the frontmatter never renders into the page."""
    if not text.startswith(_FRONTMATTER_DELIM):
        return text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            return "".join(lines[i + 1:])
    return text


def load_investigation_markdown(
    bug_id: int, investigation_dir: Path | None = None
) -> str | None:
    """Return the raw markdown of `bug-{id}-investigation.md` from the
    investigation dir, or None if it's absent/unreadable. Thin I/O wrapper;
    body extraction (`strip_frontmatter`) and rendering happen on the
    result via pure functions."""
    if investigation_dir is None:
        investigation_dir = investigation_dir_from_env()
    md_path = investigation_dir / f"bug-{bug_id}-investigation.md"
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_investigation(
    bug_id: int, investigation_dir: Path | None = None
) -> Investigation | None:
    """Read `bug-{id}-investigation.md` from the investigation dir and
    parse its YAML frontmatter.

    A `bug-{id}-investigating.lock` file in the same directory short-
    circuits frontmatter parsing: the /bug-start run is in flight, the
    md file may be partial / absent / stale, so we surface "investigating"
    (fresh lock) or "investigation-stalled" (lock mtime > 30 min) instead.

    Returns None only when no lock exists and the md file doesn't exist
    (or can't be read). When the md file exists but its frontmatter is
    missing, malformed, or not a YAML mapping, return a "shell"
    Investigation with `bug_id` and `file_path` populated and all other
    fields at their defaults — that way the card can still link to the
    file on GitHub even for legacy pre-schema investigations.

    The file location defaults to $FX_BUG_INVESTIGATION_DIR (or
    ~/.fx-bug-toolkit/bug-investigation/); callers may pass an explicit
    directory for tests.
    """
    if investigation_dir is None:
        investigation_dir = investigation_dir_from_env()
    md_path = investigation_dir / f"bug-{bug_id}-investigation.md"
    lock_path = investigation_dir / f"bug-{bug_id}-investigating.lock"

    if lock_path.is_file():
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            age = 0.0
        if age > _LOCK_STALE_SECONDS:
            file_path = (
                str(md_path.resolve()) if md_path.is_file()
                else str(lock_path.resolve())
            )
            return Investigation(
                bug_id=bug_id,
                status="investigation-stalled",
                file_path=file_path,
            )
        return Investigation(
            bug_id=bug_id,
            status="investigating",
            file_path="",
        )

    if not md_path.is_file():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    shell = Investigation(bug_id=bug_id, file_path=str(md_path.resolve()))
    body = _extract_frontmatter(text)
    if body is None:
        return shell
    try:
        parsed = yaml.load(body, Loader=_InvestigationYamlLoader)
    except yaml.YAMLError:
        return shell
    if not isinstance(parsed, dict):
        return shell

    raw_regression = parsed.get("regression_range")
    regression_range: str | None
    if raw_regression is None:
        regression_range = None
    else:
        regression_range = str(raw_regression)

    return Investigation(
        bug_id=int(parsed.get("bug_id") or bug_id),
        investigated_at=str(parsed.get("investigated_at") or ""),
        status=str(parsed.get("status") or ""),
        root_cause=str(parsed.get("root_cause") or ""),
        affected_files=[str(f) for f in (parsed.get("affected_files") or [])],
        regression_range=regression_range,
        related_bugs=[
            int(b) for b in (parsed.get("related_bugs") or [])
            if isinstance(b, (int, str)) and str(b).strip().lstrip("-").isdigit()
        ],
        complexity=str(parsed.get("complexity") or ""),
        notes=str(parsed.get("notes") or ""),
        file_path=str(md_path.resolve()),
        depth=str(parsed.get("depth") or ""),
    )


def triage_dir_from_env() -> Path:
    """Resolve the triage data directory from $TRIAGE_DIR or the default."""
    override = os.environ.get("TRIAGE_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_TRIAGE_DIR


_VERSION_RE = re.compile(r"\d+(?:\.\d+)*(?:[ab]\d+)?")


def version_only(raw: str) -> str:
    """Extract just the version number from a freeform reported-version
    string, dropping the channel word and any trailing noise. E.g.
    'Nightly 153.0a1 (2026-05-30); UA shows 152.0' -> '153.0a1',
    'Firefox 150.0' -> '150.0', '150' -> '150'. Returns '' when there is
    no actual version NUMBER (e.g. 'unspecified', 'unknown', '') so the
    caller can omit the 'Found' chip rather than show a non-version."""
    if not raw:
        return ""
    m = _VERSION_RE.search(raw)
    return m.group(0) if m else ""


def now_local_dateline() -> str:
    """Human-friendly date for the top bar (e.g. 'Thursday, May 28')."""
    now = datetime.now()
    return now.strftime("%A, %B %-d")


def last_updated_dateline(triage_dir: Path) -> str:
    """Precise 'last updated' string for the top bar — the newest write time
    across the pending draft files.

    Uses each pending file's filesystem **mtime** (the real moment /triage or
    a refine last wrote it), NOT the draft's ``created_at`` field. created_at
    is written by the triage agent and is often a fabricated placeholder
    (e.g. midnight UTC), so it can't be trusted for a freshness indicator;
    mtime is ground truth.

    Returns a local-time, year-qualified, minute-precise string such as
    ``'Jun 1, 2026 10:22 PDT'``. Falls back to ``'no drafts yet'`` when the
    pending directory is missing or empty.
    """
    pending = Path(triage_dir) / "pending"
    if not pending.is_dir():
        return "no drafts yet"
    mtimes = [p.stat().st_mtime for p in pending.glob("bug-*.json")]
    if not mtimes:
        return "no drafts yet"
    # fromtimestamp(naive local) → .astimezone() makes it aware-local so %Z
    # renders the local tz abbreviation.
    local = datetime.fromtimestamp(max(mtimes)).astimezone()
    return local.strftime("%b %-d, %Y %H:%M %Z").strip()
