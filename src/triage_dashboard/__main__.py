"""CLI entry: `triage-dashboard` runs the server and opens the browser."""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="triage-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart on file change (dev mode).",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        # Open in a delayed thread so the server has a chance to bind.
        threading.Thread(
            target=lambda: (time.sleep(0.6), webbrowser.open(url)),
            daemon=True,
        ).start()

    uvicorn.run(
        "triage_dashboard.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
