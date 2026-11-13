"""Recompute replay outcomes exclusively from saved attempt logs and reports."""
from collections import Counter, defaultdict
import hashlib
import json
import re
from pathlib import Path
from complement_scripts.common import write_csv, write_json


def summarize_saved(sources, output):
    attempts, evidence, digests, seen = [], [], {}, set()
    for experiment, directory in sources:
        log = directory / "replay_results.jsonl"
        raw = log.read_bytes()
        digests[log] = hashlib.sha256(raw).hexdigest()
        for line in raw.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            sid = row["session_id"]
            if sid in seen:
                raise ValueError("Duplicate replay session across input batches")
            seen.add(sid)
            name = row["username"]
            if not isinstance(name, str) or not re.fullmatch(r"user_\d{4,}", name):
                raise ValueError("Saved replay records must contain anonymous account IDs")
            # Saved Windows logs remain readable on other operating systems.
            report_path = (directory / Path(row["report_path"].replace("\\", "/"))).resolve()
            if not report_path.is_relative_to(directory.resolve()):
                raise ValueError("Replay report path escapes its input directory")
            raw_report = report_path.read_bytes()
            digests[report_path] = hashlib.sha256(raw_report).hexdigest()
            report = json.loads(raw_report)
            owner = report.get("username")
            if owner is None:
                # Older reports identify their account through the storage path.
                parts = report_path.relative_to(directory.resolve()).parts
                owner = parts[1] if len(parts) >= 3 and parts[0] == "replays" else None
            if report.get("session_id") != sid or owner != name:
                raise ValueError("Replay log and report session or account IDs disagree")
            final = report.get("login_outcome", {})
            if "completed_decision" in row:
                completed, accepted = row["completed_decision"], row["success"]
            else:
                completed = row.get("classification") in {"accepted", "rejected"}
                accepted = row.get("classification") == "accepted"
            if accepted != (completed and final.get("result") == "success"):
                raise ValueError("Replay log and final authentication outcome disagree")
            reasons = set()
            if not accepted:
                if not completed:
                    reasons.add("client/setup error")
                for modality in ("canvas", "audio"):
                    for sample in report.get("modalities", {}).get(modality, {}).get("measurements", []):
                        if sample.get("http_status") != 200:
                            reasons.add(f"{modality} upload rejected: {sample.get('error') or sample.get('http_status')}")
                        elif sample.get("matches_recorded_baseline") is False:
                            reasons.add(f"{modality} baseline hash mismatch")
                if final.get("result") == "error":
                    reasons.add("final verification incomplete")
                if not reasons:
                    reasons.add("other final rejection")
            item = {"experiment": experiment, "username": name, "session_id": sid,
                    "trial": row["trial"], "mode": row["mode"], "completed_decision": completed,
                    "success": accepted, "result": final.get("result"), "failure_observations": "; ".join(sorted(reasons))}
            attempts.append(item)
            evidence.append({**item, "failure_reasons": sorted(reasons), "source_report_sha256": digests[report_path],
                             "historical_server_event_matched": row.get("server_event_matched"),
                             "mapping": report.get("reconstruction_mapping")})
    write_json(output / "replay_evidence.json", evidence)
    aggregate_evidence(evidence, output)
    return digests


def aggregate_evidence(evidence, output):
    """Rebuild all replay tables from the frozen anonymous per-attempt evidence."""
    attempts = [{k:v for k,v in r.items() if k not in {"failure_reasons", "source_report_sha256", "historical_server_event_matched", "mapping"}}
                for r in evidence]
    summary, failures, users = [], [], []
    for experiment, mode in sorted({(r["experiment"], r["mode"]) for r in attempts}):
        rows = [r for r in attempts if (r["experiment"], r["mode"]) == (experiment, mode)]
        completed = [r for r in rows if r["completed_decision"]]
        successes = sum(r["success"] for r in completed)
        summary.append({"experiment": experiment, "mode": mode, "successes": successes,
            "completed_attempts": len(completed), "success_percent": 100 * successes / len(completed) if completed else None,
            "tested_accounts": len({r["username"] for r in rows}),
            "successful_accounts": len({r["username"] for r in rows if r["success"]}), "setup_errors": len(rows)-len(completed)})
        counts = Counter(reason for r in evidence if (r["experiment"], r["mode"]) == (experiment, mode) for reason in r["failure_reasons"])
        failures.extend({"experiment": experiment, "mode": mode, "observation": k, "attempts": v} for k,v in sorted(counts.items()))
        for username in sorted({r["username"] for r in rows}):
            selected = [r for r in completed if r["username"] == username]
            users.append({"experiment": experiment, "mode": mode, "username": username,
                          "successes": sum(r["success"] for r in selected), "completed_attempts": len(selected)})
    for name, rows in (("replay_results", attempts), ("replay_summary", summary), ("replay_failures", failures), ("replay_users", users)):
        write_json(output / (name + ".json"), rows)
        write_csv(output / (name + ".csv"), rows, list(rows[0]) if rows else ["no_records"])
