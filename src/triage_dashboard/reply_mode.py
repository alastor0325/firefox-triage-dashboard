"""Detect whether triage can write back to Bugzilla ("reply mode").

The dashboard's apply / Process-queue actions require a BMO API key. That key is
the single source of truth for the mode, mirroring `bugzilla-cli`: it is present
when `$BUGZILLA_BOT_API_KEY` is set, or when `~/.config/triage/secrets` defines
it. With no key, triage runs **read-only** — the dashboard shows AI drafts but
offers no apply/write actions.

Pure helpers take their inputs as arguments so they unit-test without I/O;
`detect_reply_mode()` is the thin wrapper that reads the environment and the
secrets file.
"""

from __future__ import annotations

import os
from pathlib import Path

SECRETS_PATH = Path.home() / ".config" / "triage" / "secrets"
_KEY_VAR = "BUGZILLA_BOT_API_KEY"


def secrets_has_key(text: str | None) -> bool:
    """True when the secrets-file contents define a non-empty API key.

    Accepts the `bugzilla-cli` format: `BUGZILLA_BOT_API_KEY=<value>` lines,
    optionally prefixed with `export `.
    """
    if not text:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        kv = stripped[len("export ") :] if stripped.startswith("export ") else stripped
        if kv.startswith(f"{_KEY_VAR}="):
            value = kv[len(_KEY_VAR) + 1 :].strip().strip('"').strip("'")
            if value:
                return True
    return False


def reply_capable(env_key: str | None, secrets_text: str | None) -> bool:
    """Pure decision: is a write key available (env var or secrets file)?"""
    if env_key and env_key.strip():
        return True
    return secrets_has_key(secrets_text)


def detect_reply_mode(secrets_path: Path = SECRETS_PATH) -> bool:
    """Read the environment + secrets file and decide reply (True) vs read-only."""
    secrets_text: str | None = None
    try:
        if secrets_path.is_file():
            secrets_text = secrets_path.read_text(encoding="utf-8")
    except OSError:
        secrets_text = None
    return reply_capable(os.environ.get(_KEY_VAR), secrets_text)
