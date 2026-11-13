"""Read report inputs directly from the database without copying source files."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def load_database(data_dir, markers):
    data_dir, markers = Path(data_dir).resolve(), Path(markers).resolve()
    sources = {}

    def read(path):
        raw = path.read_bytes()
        sources[path] = hashlib.sha256(raw).hexdigest()
        return raw

    dataset = {"created_utc": datetime.now(timezone.utc).isoformat(),
               "accounts": [], "warnings": [], "excluded_previous_evaluation_sessions": 0,
               "relay_markers": [json.loads(line) for line in read(markers).splitlines() if line.strip()]}
    for path in sorted((data_dir / "users").glob("*/*.json")):
        if path.name != path.parent.name + ".json":
            continue
        record = json.loads(read(path))
        username = record["username"]
        if username != path.parent.name or not username.startswith("user_") or not username[5:].isdigit():
            raise ValueError("Current database must use the established anonymous account IDs")
        events_path = path.parent / "events.jsonl"
        events = [json.loads(line) for line in read(events_path).splitlines() if line.strip()] if events_path.exists() else []
        fingerprint = record.get("fingerprint", {})
        account = {"username": username, "events": events,
                   "fingerprint": {"hash": fingerprint.get("hash"), "details": fingerprint.get("details", {})},
                   "performance_reports": []}
        for performance_path in sorted((path.parent / "performance").glob("*.json")):
            account["performance_reports"].append({
                "path": performance_path.relative_to(data_dir).as_posix(),
                "report": json.loads(read(performance_path))})
        dataset["accounts"].append(account)
    if not dataset["accounts"]:
        raise ValueError("No current account records found")
    for path, digest in sources.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Source changed during reading; rerun reproduction")
    return dataset, sources
