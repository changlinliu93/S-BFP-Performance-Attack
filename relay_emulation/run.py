"""Run this file in an IDE, or: python -m relay_emulation.run --help."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_emulation import settings
from relay_emulation.server import SESSION_LOGGER, create_app
from werkzeug.serving import WSGIRequestHandler


class QuietHandler(WSGIRequestHandler):
    # Keep raw URLs/seeds out of logs; selected login fields are logged separately.
    def log_request(self, code="-", size="-"):
        pass


def main():
    parser = argparse.ArgumentParser(description="S-BFP fixed-upstream relay experiment")
    parser.add_argument("--upstream", default=settings.UPSTREAM_URL)
    parser.add_argument("--host", default=settings.RELAY_HOST)
    parser.add_argument("--port", type=int, default=settings.RELAY_PORT)
    parser.add_argument("--added-rtt-ms", type=float, default=settings.ADDED_UPSTREAM_RTT_MS)
    parser.add_argument("--session-log", type=Path, default=settings.SESSION_LOG,
                        help="Append username and original-server session ID as JSONL (also printed to console)")
    parser.add_argument("--cert", help="TLS certificate file for remote browser testing")
    parser.add_argument("--key", help="TLS private key file")
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error("--cert and --key must be supplied together")
    app = create_app(args.upstream, args.added_rtt_ms, settings.UPSTREAM_TIMEOUT_SECONDS)
    log_path = args.session_log.expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    handlers = (file_handler, logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))
        SESSION_LOGGER.addHandler(handler)
    SESSION_LOGGER.setLevel(logging.INFO)
    SESSION_LOGGER.propagate = False
    print(f"Relay login: {'https' if args.cert else 'http'}://{args.host}:{args.port}/", flush=True)
    print("Using the defense's native performance monitor and report storage.", flush=True)
    print(f"Login correlation log: {log_path}", flush=True)
    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True,
                request_handler=QuietHandler, ssl_context=(args.cert, args.key) if args.cert else None)
    finally:
        app.extensions["relay_lab"]["upstream"].close()
        for handler in handlers:
            SESSION_LOGGER.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    main()
