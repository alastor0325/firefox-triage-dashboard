#!/usr/bin/env bash
# Launch the triage dashboard for fx-dev-hub. Resolves the fx-bug-toolkit managed
# venv (where the dashboard is installed) at runtime — falling back to a system
# Python — and runs it on the port the Hub passes as --port, with the browser
# suppressed (the Hub frames the page). No hardcoded versions or absolute tool
# paths, so the Hub can launch it from its minimal, isolated PATH.
set -euo pipefail
VENV="$HOME/.fx-bug-toolkit/venv"
PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
exec "$PY" -m triage_dashboard "$@" --no-browser
