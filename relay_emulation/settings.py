"""Python/IDE entry points use these defaults; command-line options override them."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
UPSTREAM_URL = "http://127.0.0.1:5001"
RELAY_HOST = "127.0.0.1"
RELAY_PORT = 5002
SESSION_LOG = PROJECT_DIR / "relay_emulation" / "logs" / "sessions.jsonl"
# Added round-trip delay on the relay <-> defense hop, not measured geography.
ADDED_UPSTREAM_RTT_MS = 0.0
UPSTREAM_TIMEOUT_SECONDS = 30
