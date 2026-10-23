#!/usr/bin/env python3
"""Serve the Adaptive Lighting brightness-curve editor demo."""

from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / "custom_components" / "adaptive_lighting" / "frontend"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[curve-demo] {self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3847)
    args = parser.parse_args()

    if not FRONTEND.exists():
        raise SystemExit(f"Frontend not found at {FRONTEND}")

    # Force demo mode via a tiny wrapper index if needed — editor auto-detects.
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Brightness curve demo: http://{args.host}:{args.port}/index.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
