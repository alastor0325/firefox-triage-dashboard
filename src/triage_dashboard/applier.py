"""Plan and execute Bugzilla writes for a pending draft.

Stays decoupled from the actual `bugzilla-cli` invocation so we can run in
dry-run mode (default) and observe what *would* happen without touching
Bugzilla — production state is irreversible and we don't trust the
dashboard wiring blindly. `plan_apply` / `plan_skip` are pure functions
that produce a structured list of actions; the live executor will be
added behind a flag once the dry-run flow has been exercised.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannedAction:
    kind: str          # comment, severity, priority, resolution, reassign,
                       # blocks, cc, keyword, ni, watch-add,
                       # delete-pending, log-skipped
    description: str   # human-readable one-line description


def plan_apply(pending: dict) -> list[PlannedAction]:
    """Compute the list of actions an `apply {id}` would perform.

    Order is: severity, priority, resolution, reassign, blocks, cc,
    keywords, ni, comment, watch-add — fields first, comment second-to-last,
    watch-add at the very end. So a reviewer reads the Bugzilla-impacting
    metadata before the (often long) comment.
    """
    out: list[PlannedAction] = []
    bug_id = pending.get("bug_id")

    if pending.get("severity"):
        out.append(PlannedAction(
            kind="severity",
            description=f"set severity → {pending['severity']}",
        ))
    if pending.get("priority"):
        out.append(PlannedAction(
            kind="priority",
            description=f"set priority → {pending['priority']}",
        ))
    if pending.get("resolution"):
        out.append(PlannedAction(
            kind="resolution",
            description=f"resolve → {pending['resolution']}",
        ))
    if pending.get("product") or pending.get("component"):
        product = pending.get("product") or "Core"
        component = pending.get("component") or "?"
        out.append(PlannedAction(
            kind="reassign",
            description=f"reassign → {product} :: {component}",
        ))
    for b in pending.get("blocks_add") or []:
        out.append(PlannedAction(
            kind="blocks",
            description=f"add blocks → bug {b}",
        ))
    for cc in pending.get("cc_add") or []:
        out.append(PlannedAction(
            kind="cc",
            description=f"add cc → {cc}",
        ))
    for kw in pending.get("keywords_add") or []:
        out.append(PlannedAction(
            kind="keyword",
            description=f"add keyword → {kw}",
        ))
    for ni in pending.get("ni_targets") or []:
        out.append(PlannedAction(
            kind="ni",
            description=f"needinfo → {ni}",
        ))
    if pending.get("comment"):
        comment_len = len(pending["comment"])
        out.append(PlannedAction(
            kind="comment",
            description=f"post comment ({comment_len} chars)",
        ))
    if pending.get("ni_targets"):
        out.append(PlannedAction(
            kind="watch-add",
            description=f"add bug {bug_id} to watch list",
        ))
    return out


def plan_skip(pending: dict) -> list[PlannedAction]:
    """Compute the actions a `skip {id}` would perform."""
    bug_id = pending.get("bug_id")
    return [
        PlannedAction(
            kind="delete-pending",
            description=f"delete pending/bug-{bug_id}.json",
        ),
        PlannedAction(
            kind="log-skipped",
            description="append `skipped` entry to triage-log.json",
        ),
    ]
