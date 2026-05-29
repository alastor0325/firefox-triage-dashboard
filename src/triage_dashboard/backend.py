"""Swappable backend for executing apply/skip on triage drafts.

Default is `MockBackend`, which computes the plan but never touches
Bugzilla or local triage state. `BugzillaCLIBackend` is the slot reserved
for real `bugzilla-cli` invocation — its methods raise
`NotImplementedError` until Phase 4.5 is explicitly implemented (see
PLAN.md). Two gates between us and real Bugzilla writes:

  1. `TRIAGE_DASHBOARD_LIVE=1` env var (selects the real backend).
  2. `BugzillaCLIBackend.apply/skip` actually have implementations.

Flipping only gate 1 produces no real action — calls raise.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from . import applier


LIVE_ENV_VAR = "TRIAGE_DASHBOARD_LIVE"


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    actions: list[applier.PlannedAction]
    output: str = ""
    side_effects: list[str] = field(default_factory=list)


class TriageBackend(ABC):
    """Executes apply / skip on a pending triage draft."""

    @abstractmethod
    def apply(self, bug_id: int, pending: dict) -> BackendResult: ...

    @abstractmethod
    def skip(self, bug_id: int, pending: dict) -> BackendResult: ...


class MockBackend(TriageBackend):
    """Computes the plan and returns it. Never touches Bugzilla, never
    writes to disk, never spawns subprocesses. Safe to call freely."""

    _OUTPUT = "(dry run — mock backend; no real action performed)"

    def apply(self, bug_id: int, pending: dict) -> BackendResult:
        return BackendResult(
            ok=True,
            actions=applier.plan_apply(pending),
            output=self._OUTPUT,
            side_effects=[],
        )

    def skip(self, bug_id: int, pending: dict) -> BackendResult:
        return BackendResult(
            ok=True,
            actions=applier.plan_skip(pending),
            output=self._OUTPUT,
            side_effects=[],
        )


class BugzillaCLIBackend(TriageBackend):
    """Real `bugzilla-cli`-backed apply / skip — NOT YET IMPLEMENTED.

    Both methods raise NotImplementedError. Implementing them requires
    explicit user approval. The class exists so `get_backend()` can
    return it when `TRIAGE_DASHBOARD_LIVE=1` — that way, flipping the
    env var alone is not enough to produce a real Bugzilla write.
    """

    _NOT_IMPL_MSG = (
        "real-mode bugzilla-cli backend is not yet wired up; "
        "implementation requires explicit user approval (PLAN.md Phase 4.5)"
    )

    def apply(self, bug_id: int, pending: dict) -> BackendResult:
        raise NotImplementedError(self._NOT_IMPL_MSG)

    def skip(self, bug_id: int, pending: dict) -> BackendResult:
        raise NotImplementedError(self._NOT_IMPL_MSG)


def get_backend() -> TriageBackend:
    """Pick the backend based on env var. Default = mock; live mode only
    when `TRIAGE_DASHBOARD_LIVE=1` (exact match, no truthy parsing — so
    a forgotten `=0` or stray value can't promote to live by accident).
    """
    if os.environ.get(LIVE_ENV_VAR) == "1":
        return BugzillaCLIBackend()
    return MockBackend()
