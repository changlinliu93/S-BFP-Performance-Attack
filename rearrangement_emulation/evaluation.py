"""Run real HTTP replays with temporary, recoverable password-hash substitution."""

import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from rearrangement_emulation.captures import load_captures
from rearrangement_emulation.client import run_attempt
from complement_scripts.common import distribution, rate, write_csv, write_json


BASELINE_KEYS = ("canvas_baseline", "canvas_baseline_seed", "audio_baseline", "audio_baseline_seed")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="evaluation_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_password(run_dir):
    journal_path = Path(run_dir) / ".private" / "password-recovery.json"
    if not journal_path.exists():
        return False
    journal = read_json(journal_path)
    path = Path(journal["record_path"]).resolve()
    root = Path(journal["data_dir"]).resolve() / "users"
    if not path.is_relative_to(root) or path.name != path.parent.name + ".json":
        raise ValueError("Recovery record path is not an account under the recorded data directory")
    current = read_json(path)
    if current.get("password_hash") == journal["temporary_hash"]:
        current["password_hash"] = journal["original_hash"]
        atomic_json(path, current)  # Preserve all newly written session history.
    elif current.get("password_hash") != journal["original_hash"]:
        raise RuntimeError("Password hash changed independently; recovery refused to overwrite it")
    if read_json(path).get("password_hash") != journal["original_hash"]:
        raise RuntimeError("Password hash restoration could not be verified")
    journal_path.unlink()
    return True


@contextmanager
def temporary_password(record_path, run_dir, data_dir):
    private = Path(run_dir) / ".private"
    private.mkdir(mode=0o700, exist_ok=True)
    journal_path = private / "password-recovery.json"
    if journal_path.exists():
        raise RuntimeError("A recovery journal exists; run the restore command before more tests")
    current = read_json(record_path)
    original = current.get("password_hash")
    if not isinstance(original, str) or not original:
        raise ValueError("Account has no password hash to restore")
    password = secrets.token_urlsafe(32)
    replacement = generate_password_hash(password)
    journal = {"record_path": str(record_path), "data_dir": str(data_dir),
               "original_hash": original, "temporary_hash": replacement}
    # Durable recovery data exists before the account changes. Never print it.
    atomic_json(journal_path, journal)
    try:
        latest = read_json(record_path)
        if latest.get("password_hash") != original:
            raise RuntimeError("Account password changed before the test began")
        latest["password_hash"] = replacement
        atomic_json(record_path, latest)
        yield password, original
    finally:
        restore_password(run_dir)


def summarize(rows, metadata):
    modes = {}
    for mode in ("unchanged", "rearranged"):
        selected = [row for row in rows if row["mode"] == mode]
        decisions = [row for row in selected if row["completed_decision"]]
        stats = {"submitted_attempts": len(selected),
            "decision_acceptance": rate(sum(row["success"] for row in decisions), len(decisions)),
            "submitted_acceptance": rate(sum(row["success"] for row in selected), len(selected)),
            "errors": sum(not row["completed_decision"] for row in selected),
            "tested_accounts": len({row["username"] for row in selected}),
            "successful_accounts": len({row["username"] for row in selected if row["success"]})}
        for modality in ("canvas", "audio"):
            measurements = [row for row in selected if row.get(modality + "_upload_status") is not None]
            stats[modality + "_matches"] = rate(sum(row[modality + "_matches"] is True for row in measurements), len(measurements))
            stats[modality + "_rearrangement_ms"] = distribution([row[modality + "_rearrangement_ms"] for row in measurements])
        modes[mode] = stats
    return {**metadata, "modes": modes,
            "tested_accounts": len({row["username"] for row in rows})}


def run_replays(run_dir, server_url, attempts=3, mapping="content"):
    run_dir = Path(run_dir).resolve()
    snapshot = read_json(run_dir / "snapshot.json")
    data_dir = Path(snapshot["data_dir"]).resolve()
    records_path = run_dir / "replay_results.jsonl"
    if records_path.exists() or (run_dir / "replays").exists():
        raise FileExistsError("This snapshot already has replay output; make a new snapshot for a new experiment")
    # Prevent two evaluation scripts from substituting hashes concurrently.
    lock_path = data_dir / ".database_evaluation.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(lock_fd, json.dumps({"pid": os.getpid(), "run_dir": str(run_dir)}).encode())
    os.close(lock_fd)
    rows, originals, baseline_originals = [], {}, {}
    used_ids = set()
    metadata = {"started_utc": datetime.now(timezone.utc).isoformat(), "server_url": server_url,
        "reconstruction_mapping": mapping,
        "attempts_per_mode": attempts, "eligible_accounts": sum(account["capture_ready"] for account in snapshot["accounts"]),
        "skipped": [{"username": account["username"], "reason": account.get("capture_error")} for account in snapshot["accounts"]
                    if not account["capture_ready"]], "password_hashes_restored": 0, "baselines_unchanged": 0,
        "all_results_matched_server_events": True}
    try:
        for index, account in enumerate(snapshot["accounts"], 1):
            if not account["capture_ready"]:
                continue
            username = account["username"]
            path = data_dir / "users" / username / (username + ".json")
            if not path.resolve().is_relative_to(data_dir / "users"):
                raise ValueError("Account path escaped the data directory")
            captures = load_captures(run_dir / account["record_path"])
            used_ids.update(capture["config"]["challenge_id"] for capture in captures.values())
            before = read_json(path)
            frozen = read_json(run_dir / account["record_path"])
            baseline = {key: frozen.get(key) for key in BASELINE_KEYS}
            if {key: before.get(key) for key in BASELINE_KEYS} != baseline:
                raise RuntimeError("Account baseline changed since the snapshot: " + username)
            originals[username] = before["password_hash"]
            baseline_originals[username] = baseline
            with temporary_password(path, run_dir, data_dir) as (password, _):
                for trial in range(1, attempts + 1):
                    for mode in (("unchanged", "rearranged") if trial % 2 else ("rearranged", "unchanged")):
                        output = run_dir / "replays" / username / f"trial-{trial:03d}-{mode}"
                        result = run_attempt(server_url, username, password, captures, output, mode, used_ids,
                                             match_content=mapping == "content")
                        outcome = result.get("login_outcome", {})
                        completed = (not result.get("client_error") and not result.get("release_error")
                                     and result.get("release_status") == 200
                                     and outcome.get("result") in {"success", "failure", "error"})
                        row = {"username": username, "trial": trial, "mode": mode,
                            "session_id": result.get("session_id"), "completed_decision": completed,
                            "success": completed and outcome.get("result") == "success",
                            "result": outcome.get("result"), "client_error": result.get("client_error"),
                            "report_path": str((output / "report.json").relative_to(run_dir)),
                            "server_event_matched": False}
                        for modality in ("canvas", "audio"):
                            samples = result.get("modalities", {}).get(modality, {}).get("measurements", [])
                            sample = samples[0] if samples else {}
                            row.update({modality + "_upload_status": sample.get("http_status"),
                                modality + "_matches": sample.get("matches_recorded_baseline"),
                                modality + "_rearrangement_ms": sample.get("timing", {}).get("rearrangement_ms"),
                                modality + "_challenge_id": sample.get("challenge_id")})
                        if completed:
                            events = [json.loads(line) for line in (path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                            matching = [event for event in events if event.get("session_id") == row["session_id"]]
                            row["server_event_matched"] = len(matching) == 1 and matching[0].get("result") == row["result"]
                            if not row["server_event_matched"]:
                                metadata["all_results_matched_server_events"] = False
                        rows.append(row)
                        with records_path.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        write_json(run_dir / "replay_summary.json", summarize(rows, metadata))
                        if not completed:
                            raise RuntimeError(f"Stopped on an incomplete replay for {username}: {row['client_error'] or row['result']}")
                        if not row["server_event_matched"]:
                            raise RuntimeError("Live session was not found in the selected database; stopped")
            after = read_json(path)
            if after["password_hash"] != originals[username]:
                raise RuntimeError("Original password hash was not restored: " + username)
            metadata["password_hashes_restored"] += 1
            if {key: after.get(key) for key in BASELINE_KEYS} != baseline:
                raise RuntimeError("Account baseline changed during evaluation: " + username)
            metadata["baselines_unchanged"] += 1
            selected = [row for row in rows if row["username"] == username]
            outcomes = "; ".join(f"{mode} {sum(row['success'] for row in selected if row['mode'] == mode)}/{attempts}"
                                 for mode in ("unchanged", "rearranged"))
            print(f"{index}/{len(snapshot['accounts'])} {username}: {outcomes}; original password hash restored", flush=True)
    except BaseException as error:
        metadata["fatal_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # One last independent read after all server writes have completed.
        metadata["password_hashes_restored"] = sum(
            read_json(data_dir / "users" / username / (username + ".json")).get("password_hash") == original
            for username, original in originals.items())
        metadata["baselines_unchanged"] = sum(
            {key: read_json(data_dir / "users" / username / (username + ".json")).get(key) for key in BASELINE_KEYS} == baseline
            for username, baseline in baseline_originals.items())
        metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run_dir / "replay_summary.json", summarize(rows, metadata))
        write_csv(run_dir / "replay_results.csv", rows, list(rows[0]) if rows else ["username", "mode"])
        if not (run_dir / ".private/password-recovery.json").exists():
            lock_path.unlink()
    return summarize(rows, metadata)
