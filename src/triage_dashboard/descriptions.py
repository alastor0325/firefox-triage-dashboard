"""Parsing and rendering helpers for the bug description excerpt.

`description_excerpt` arrives from the /triage skill as a flat string that
collapses what was originally a multi-section Bugzilla description (Steps to
reproduce / Actual results / Expected results, often with a leading User
Agent line). This module pulls those sections back apart so the dashboard
can render them as a clear listing instead of a wall of text.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape


# Matches a leading "User Agent: ... Firefox/NNN.0." sentence at the very
# start of a description. We drop it because the dashboard already shows the
# Firefox version + platform in the byline.
_UA_RE = re.compile(
    r"^User Agent:.*?Firefox/\d[\d.]*\s*\.\s*",
    re.IGNORECASE | re.DOTALL,
)

# Section labels we know about. Order matters for display.
_SECTION_LABELS = [
    ("steps", r"steps?\s+to\s+reproduce"),
    ("actual", r"actual\s+results?"),
    ("expected", r"expected\s+results?"),
]

# One alternation to find any label.
_ANY_LABEL_RE = re.compile(
    r"\b(" + "|".join(p for _, p in _SECTION_LABELS) + r")\s*[:\-]\s*",
    re.IGNORECASE,
)

# A URL good enough for typical bug descriptions. Trailing punctuation
# (period, comma, closing paren) is excluded so it doesn't become part of
# the anchor.
_URL_RE = re.compile(r"(https?://[^\s<>\"']+?)([.,!?)\]]?)(?=\s|$)")


def parse_description(text: str) -> dict[str, str]:
    """Split a Bugzilla-style description into known sections.

    Returns a dict with any of the keys: `intro`, `steps`, `actual`,
    `expected`, `notes`. Empty input → empty dict.
    """
    if not text:
        return {}

    text = _UA_RE.sub("", text).strip()
    if not text:
        return {}

    # Find every label position.
    label_hits: list[tuple[int, int, str]] = []
    for m in _ANY_LABEL_RE.finditer(text):
        key = _label_to_key(m.group(1))
        label_hits.append((m.start(), m.end(), key))

    if not label_hits:
        return {"notes": text.strip()}

    out: dict[str, str] = {}
    pre = text[: label_hits[0][0]].strip()
    if pre:
        out["intro"] = pre

    for i, (_start, end, key) in enumerate(label_hits):
        section_end = label_hits[i + 1][0] if i + 1 < len(label_hits) else len(text)
        content = text[end:section_end].strip()
        if content:
            out[key] = content

    return out


def _label_to_key(matched_label: str) -> str:
    lo = matched_label.lower()
    if "step" in lo:
        return "steps"
    if "actual" in lo:
        return "actual"
    if "expected" in lo:
        return "expected"
    return "notes"


def linkify(text: str) -> Markup:
    """HTML-escape `text` and turn URLs into anchor tags.

    Trailing punctuation after a URL stays outside the anchor so the link
    target doesn't accidentally include a sentence-ending period.
    """
    if not text:
        return Markup("")
    escaped = str(escape(text))

    def _repl(m: re.Match[str]) -> str:
        url, trailing = m.group(1), m.group(2)
        return (
            f'<a href="{url}" target="_blank" rel="noopener">{url}</a>{trailing}'
        )

    return Markup(_URL_RE.sub(_repl, escaped))
