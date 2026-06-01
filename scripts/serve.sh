#!/usr/bin/env bash
# serve.sh — start/stop/restart/status/logs for the triage dashboard.
#
# Usage:
#   scripts/serve.sh start     # launch in the background (no browser pop)
#   scripts/serve.sh stop      # stop the running instance
#   scripts/serve.sh restart   # stop then start
#   scripts/serve.sh status    # is it up? (PID + HTTP health check)
#   scripts/serve.sh logs      # tail the server log (Ctrl-C to exit)
#
# Env overrides: HOST (default 127.0.0.1), PORT (default 8765).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV_BIN="$REPO/.venv/bin"
RUN_DIR="$REPO/.run"
PID_FILE="$RUN_DIR/dashboard.pid"
LOG_FILE="$RUN_DIR/dashboard.log"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"
URL="http://$HOST:$PORT/"

mkdir -p "$RUN_DIR"

die() { echo "error: $*" >&2; exit 1; }

running_pid() {
    # Echo the live PID if the dashboard is running, else nothing.
    if [[ -f "$PID_FILE" ]]; then
        local pid; pid=$(cat "$PID_FILE" 2>/dev/null || true)
        if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"; return 0
        fi
    fi
    return 1
}

health() {
    curl -fsS -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null || echo "000"
}

cmd_start() {
    [[ -x "$VENV_BIN/triage-dashboard" ]] || die \
        "venv not found at $VENV_BIN. Run: python3 -m venv .venv && .venv/bin/pip install -e ."
    if pid=$(running_pid); then
        echo "already running (PID $pid) at $URL"; return 0
    fi
    echo "starting dashboard on $URL ..."
    nohup "$VENV_BIN/triage-dashboard" --no-browser --host "$HOST" --port "$PORT" \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    # Poll for readiness (up to ~6s).
    for _ in $(seq 1 30); do
        [[ "$(health)" == "200" ]] && { echo "up (PID $(cat "$PID_FILE")) — $URL"; return 0; }
        sleep 0.2
    done
    echo "started (PID $(cat "$PID_FILE")) but health check not 200 yet — see: scripts/serve.sh logs" >&2
    return 1
}

cmd_stop() {
    local stopped=0
    if pid=$(running_pid); then
        kill "$pid" 2>/dev/null && stopped=1
    fi
    # Safety net: catch stray instances launched by hand (old nohup runs).
    pkill -f "triage-dashboard|triage_dashboard" 2>/dev/null && stopped=1 || true
    rm -f "$PID_FILE"
    [[ "$stopped" == "1" ]] && echo "stopped." || echo "not running."
}

cmd_status() {
    local code; code=$(health)
    if pid=$(running_pid); then
        echo "running (PID $pid), HTTP $code — $URL"
    elif [[ "$code" == "200" ]]; then
        echo "running (untracked, no PID file), HTTP 200 — $URL  (use 'restart' to manage it)"
    else
        echo "stopped."
    fi
}

cmd_logs() {
    [[ -f "$LOG_FILE" ]] || die "no log yet at $LOG_FILE"
    tail -f "$LOG_FILE"
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_stop; sleep 1; cmd_start ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    *) echo "usage: $0 {start|stop|restart|status|logs}   (env: HOST, PORT)"; exit 2 ;;
esac
