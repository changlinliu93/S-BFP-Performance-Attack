"""Edit these settings, then run run.py from your Python IDE."""

from pathlib import Path
import argparse

PROJECT_DIR = Path(__file__).resolve().parents[1]
SERVER_URL = "http://127.0.0.1:5001"
USERNAME = "test"
RECORD_PATH = PROJECT_DIR / "User_Manager/data/users/test/test.json"
MODE = "comparison"  # "comparison", "unchanged", or "rearranged"
ATTEMPTS = 3  # Per mode; each login attempts one Canvas and one Audio challenge.
OUTPUT_DIR = None  # Default: a new directory in rearrangement_emulation/results/.
# Passwords are prompted at runtime, never stored here.


def configuration():
    parser = argparse.ArgumentParser(description="Replay recordings against a running S-BFP server")
    parser.add_argument("--server", default=SERVER_URL)
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--record", type=Path, default=RECORD_PATH)
    parser.add_argument("--attempts", type=int, default=ATTEMPTS)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--mode", choices=("comparison", "unchanged", "rearranged"), default=MODE)
    args = parser.parse_args()
    result = dict(url=args.server, username=args.username, record_path=args.record,
                  attempts=args.attempts, output_dir=args.output, mode=args.mode)
    return result
