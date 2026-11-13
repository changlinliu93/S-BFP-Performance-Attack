"""Snapshot, analyze, and replay the user's running defense server."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complement_scripts.statistics import analyze
from complement_scripts.report import render_report
from complement_scripts.common import write_json
from rearrangement_emulation.evaluation import restore_password, run_replays
from rearrangement_emulation.captures import load_captures

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "rearrangement_emulation"
REPLAY_DIRS = (PROJECT / "complement_data/replay", PACKAGE / "results")


def existing_replay_ids():
    ids = set()
    for directory in REPLAY_DIRS:
        for path in directory.glob("*/replay_results.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    if row.get("session_id"):
                        ids.add(row["session_id"])
    return ids


def snapshot(data_dir, run_dir, relay_markers=None):
    run_dir.mkdir(parents=True, exist_ok=False)
    excluded = existing_replay_ids()
    metadata = {"analysis_version": 2, "created_utc": datetime.now(timezone.utc).isoformat(), "data_dir": str(data_dir),
                "accounts": [], "warnings": [], "excluded_previous_evaluation_sessions": 0,
                "password_hashes_in_snapshot": False}
    relay_markers = Path(relay_markers or PROJECT / "complement_data/relayed_session.jsonl").resolve()
    marker_bytes = relay_markers.read_bytes()
    marker_rows = [json.loads(line) for line in marker_bytes.decode("utf-8").splitlines() if line.strip()]
    write_json(run_dir / "relay_markers.json", marker_rows)
    metadata["relay_markers"] = {"source": str(relay_markers), "sha256": hashlib.sha256(marker_bytes).hexdigest(),
                                  "snapshot_path": "relay_markers.json", "rows": len(marker_rows)}
    for path in sorted((data_dir / "users").glob("*/*.json")):
        if path.name != path.parent.name + ".json":
            continue
        raw = path.read_bytes()
        record = json.loads(raw)
        username = record.get("username")
        if username != path.parent.name or not all(c.isalnum() or c == "_" for c in username):
            raise ValueError("Account filename/username mismatch: " + str(path))
        event_path = path.parent / "events.jsonl"
        event_bytes = event_path.read_bytes() if event_path.exists() else b""
        events, seen = [], set()
        for line in event_bytes.decode("utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            sid = event.get("session_id")
            if not sid:
                raise ValueError("Event is missing a session ID: " + username)
            if sid in excluded:
                metadata["excluded_previous_evaluation_sessions"] += 1
                continue
            if sid in seen:
                metadata["warnings"].append(f"Duplicate event ID excluded for {username}: {sid}")
                continue
            seen.add(sid)
            events.append(event)
        output = run_dir / "captures" / username
        output.mkdir(parents=True)
        record.pop("password_hash", None)
        for sensitive in ("fingerprint_string", "client_ip"):
            record.get("fingerprint", {}).pop(sensitive, None)
        write_json(output / path.name, record)
        account = {"username": username, "record_path": str((output / path.name).relative_to(run_dir)),
                   "source_record_sha256": hashlib.sha256(raw).hexdigest(),
                   "source_events_sha256": hashlib.sha256(event_bytes).hexdigest(), "events": events,
                   "capture_ready": False, "captures": {}, "performance_reports": []}
        for performance_path in sorted((path.parent / "performance").glob("*.json")):
            performance_bytes = performance_path.read_bytes()
            performance = json.loads(performance_bytes)
            if performance.get("session_id") in excluded:
                continue
            destination = run_dir / "performance" / username / performance_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(performance_bytes)
            account["performance_reports"].append({"session_id": performance.get("session_id"),
                "path": str(destination.relative_to(run_dir)), "sha256": hashlib.sha256(performance_bytes).hexdigest()})
        try:
            captures = load_captures(path)
            for modality, capture in captures.items():
                extension = "png" if modality == "canvas" else "pcm"
                destination = output / modality / f"{capture['config']['challenge_id']}.{extension}"
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(capture["raw"])
                account["captures"][modality] = {"challenge_id": capture["config"]["challenge_id"],
                    "bytes": len(capture["raw"]), "sha256": hashlib.sha256(capture["raw"]).hexdigest()}
            load_captures(output / path.name)  # Ensure frozen inputs are sufficient on their own.
            account["capture_ready"] = True
        except (ValueError, KeyError, OSError) as error:
            account["capture_error"] = f"{type(error).__name__}: {error}"
        if path.read_bytes() != raw or (event_path.exists() and event_path.read_bytes() != event_bytes):
            raise RuntimeError("Account changed during snapshot; retry before any replay: " + username)
        metadata["accounts"].append(account)
    if not metadata["accounts"]:
        raise ValueError("No accounts found in " + str(data_dir / "users"))
    if relay_markers.read_bytes() != marker_bytes:
        raise RuntimeError("Relay marker file changed during the snapshot; retry before running replays")
    write_json(run_dir / "snapshot.json", metadata)
    return metadata


def preflight(server):
    parsed = urlsplit(server)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Server must be an HTTP(S) origin without credentials, path, query or fragment")
    opener = build_opener(ProxyHandler({}))
    for path in ("/", "/canvas/", "/audio/"):
        with opener.open(server.rstrip("/") + path, timeout=10) as response:
            text = response.read().decode("utf-8")
            if response.status != 200:
                raise RuntimeError("Defense page unavailable: " + path)
            if path != "/" and not re.search(r"\bTEST_RUNS\s*=\s*1\s*;", text):
                raise RuntimeError("Running defense does not use one iteration: " + path)


def analyze_run(run_dir):
    """Supply this live replay run's retained inputs to the shared calculator."""
    run_dir = Path(run_dir)
    metadata = json.loads((run_dir / "snapshot.json").read_bytes())
    dataset = {**metadata, "accounts": [],
               "relay_markers": json.loads((run_dir / metadata["relay_markers"]["snapshot_path"]).read_bytes())}
    for account in metadata["accounts"]:
        record = json.loads((run_dir / account["record_path"]).read_bytes())
        reports = [{**item, "report": json.loads((run_dir / item["path"]).read_bytes())}
                   for item in account["performance_reports"]]
        dataset["accounts"].append({**account, "fingerprint": record.get("fingerprint", {}),
                                    "performance_reports": reports})
    return analyze(run_dir, dataset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("all", "snapshot", "analyze", "replay", "report", "restore"))
    parser.add_argument("--data-dir", type=Path, default=PROJECT / "User_Manager/data")
    parser.add_argument("--relay-markers", type=Path, default=PROJECT / "complement_data/relayed_session.jsonl")
    parser.add_argument("--server", default="http://127.0.0.1:5001")
    parser.add_argument("--attempts", type=int, default=3, help="Trials per account per replay mode")
    parser.add_argument("--mapping", choices=("content", "recorded-position"), default="content",
                        help="Match old/new content, or assign recorded pieces by index to fresh positions/gaps")
    parser.add_argument("--run-dir", type=Path, help="New folder for all/snapshot; existing folder for other commands")
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    if args.command not in {"all", "snapshot"} and not args.run_dir:
        parser.error("This command requires --run-dir")
    output = (args.run_dir or PACKAGE / "results" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")).resolve()
    if args.command in {"all", "replay"}:
        preflight(args.server)
        for directory in REPLAY_DIRS:
            for journal in directory.glob("*/.private/password-recovery.json"):
                raise RuntimeError("Resolve the pending password recovery before testing: " + str(journal.parent.parent))
    if args.command in {"all", "snapshot"}:
        snapshot(args.data_dir.resolve(), output, args.relay_markers)
        stats = analyze_run(output)
        render_report(output)
        print(f"Snapshot: {stats['accounts']} accounts, {stats['sessions']} historical sessions. {output}", flush=True)
    if args.command == "analyze":
        analyze_run(output)
        render_report(output)
    if args.command in {"all", "replay"}:
        try:
            run_replays(output, args.server, args.attempts, args.mapping)
        finally:
            render_report(output)
    if args.command == "report":
        render_report(output)
    if args.command == "restore":
        restored = restore_password(output)
        metadata = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
        lock = Path(metadata["data_dir"]) / ".database_evaluation.lock"
        if lock.exists() and json.loads(lock.read_text())["run_dir"] == str(output):
            lock.unlink()
        print("Original password hash restored and verified." if restored else "No pending password recovery.")
    print("Report:", output / "REPORT.md")


if __name__ == "__main__":
    main()
