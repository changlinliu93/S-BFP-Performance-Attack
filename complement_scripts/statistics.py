"""Overall statistics from loaded database records; no network operations."""
from collections import Counter, defaultdict
from pathlib import Path
from .common import distribution, rate, timestamp, write_csv, write_json
from .acceptance.classify import exclude, context_evidence, decision_statistics
from .performance.stages import metrics
def analyze(run_dir, dataset):
    run_dir = Path(run_dir)
    marker_rows = dataset["relay_markers"]
    markers, marker_duplicates = {}, 0
    for marker in marker_rows:
        sid = marker.get("session_id")
        if not sid:
            raise ValueError("Relay marker has no session ID")
        if sid in markers:
            marker_duplicates += 1
            if markers[sid].get("username") != marker.get("username"):
                raise ValueError("Conflicting usernames for relay marker " + sid)
        markers[sid] = marker
    platforms = defaultdict(set)
    for account in dataset["accounts"]:
        fingerprint = account.get("fingerprint", {})
        platform = fingerprint.get("details", {}).get("platform")
        if fingerprint.get("hash") and platform:
            platforms[fingerprint["hash"]].add(platform)
    rows, gaps, seen, marker_conflicts = [], [], set(), []
    interval_accounts = 0
    for account in dataset["accounts"]:
        username = account["username"]
        for event in sorted(account["events"], key=lambda item: timestamp(item["timestamp"])):
            sid = event["session_id"]
            if sid in seen:
                raise ValueError("Session ID belongs to more than one account: " + sid)
            seen.add(sid)
            group = "relayed" if sid in markers else "direct"
            if sid in markers and str(markers[sid].get("username", "")).casefold() != username.casefold():
                group = "unclassified"
                marker_conflicts.append(sid)
            exclusion, reason = exclude(event)
            evidence = context_evidence(event, platforms)
            result = event.get("result")
            if exclusion:
                decision_reason = reason
            elif result == "success":
                decision_reason = "unexpected_acceptance_changed_context" if evidence["context"] == "changed" else "accepted_same_context" if evidence["context"] == "same" else "accepted_unknown_context"
            else:
                decision_reason = "correct_rejection_changed_context" if evidence["context"] == "changed" else "unexpected_rejection_same_context" if evidence["context"] == "same" else "rejected_unknown_context"
            rows.append({"username": username, "session_id": sid, "timestamp": event["timestamp"],
                "group": group, "event_type": event.get("event_type"), "result": result,
                "error": event.get("error"), "included": exclusion is None,
                "exclusion": exclusion, "exclusion_reason": reason, "decision_reason": decision_reason, **evidence,
                "canvas_matches": event.get("canvas", {}).get("fingerprint_matches_baseline"),
                "audio_matches": event.get("audio", {}).get("fingerprint_matches_baseline")})
        selected = [row for row in rows if row["username"] == username]
        retained = [row for row in selected if row["included"]]
        interval_accounts += len(retained) > 1
        for previous, current in zip(retained, retained[1:]):
            value = (timestamp(current["timestamp"]) - timestamp(previous["timestamp"])).total_seconds() / 60
            gaps.append({"username": username, "earlier_session_id": previous["session_id"],
                         "later_session_id": current["session_id"], "interval_minutes": value})
    groups = {}
    for group in ("direct", "relayed", "unclassified"):
        selected = [row for row in rows if row["group"] == group]
        groups[group] = {"sessions": len(selected), "accounts": len({row["username"] for row in selected}),
            "included_sessions": sum(row["included"] for row in selected),
            "included_registrations": sum(row["included"] and row["event_type"] == "registration" for row in selected),
            "technical_errors": sum(row["exclusion"] == "technical" for row in selected),
            "wrong_passwords": sum(row["exclusion"] == "wrong_password" for row in selected),
            "technical_error_reasons": dict(Counter(row["exclusion_reason"] for row in selected if row["exclusion"] == "technical")),
            "raw_failure_reasons": dict(Counter(row["error"] or row["result"] for row in selected if row["result"] != "success")),
            **decision_statistics(selected)}
    missing_markers = [marker for sid, marker in markers.items() if sid not in seen]
    orphan_reports, missing_reports, invalid_reports, outcome_disagreements = [], [], [], []
    matched_reports, metric_values, latency_counts = {}, defaultdict(list), Counter()
    by_id = {row["session_id"]: row for row in rows}
    report_count = 0
    for account in dataset["accounts"]:
        for item in account["performance_reports"]:
            report_count += 1
            report = item["report"]
            sid = report.get("session_id")
            if sid not in by_id:
                orphan_reports.append({"username": account["username"], "session_id": sid, "path": item["path"]})
                continue
            if sid in matched_reports:
                raise ValueError("Duplicate performance report for " + sid)
            row = by_id[sid]
            matched_reports[sid] = item
            if report.get("outcome") != row["result"]:
                outcome_disagreements.append({"username": row["username"], "session_id": sid,
                    "event_result": row["result"], "performance_outcome": report.get("outcome"), "exclusion": row["exclusion"]})
            if not row["included"] or row["group"] == "unclassified":
                continue
            if (account["username"] != row["username"] or report.get("schema_version") != 2
                    or not isinstance(report.get("modalities"), dict)):
                invalid_reports.append({"username": row["username"], "session_id": sid, "reason": "schema_or_owner_mismatch"})
                continue
            values = metrics(report)
            cohorts = ["all_included_sessions"]
            if row["event_type"] == "login":
                cohorts.append("logins_only")
                if row["context"] == "same":
                    cohorts.append("same_context_logins")
            for (scope, side, metric), value in values.items():
                for cohort in cohorts:
                    metric_values[(cohort, row["group"], scope, side, metric)].append(value)
            latency_counts[row["group"]] += 1
    for row in rows:
        if row["session_id"] not in matched_reports:
            missing_reports.append({key: row[key] for key in ("username", "session_id", "group", "event_type", "included", "exclusion")})
    latency = [{"cohort": key[0], "group": key[1], "scope": key[2], "side": key[3], "metric": key[4],
                **distribution(values)} for key, values in sorted(metric_values.items())]
    exclusions = [row for row in rows if not row["included"]]
    overall = decision_statistics(rows)
    logins = [row for row in rows if row["event_type"] == "login"]
    summary = {"analysis_version": 3, "analysis_created_utc": dataset["created_utc"],
        "accounts": len(dataset["accounts"]), "sessions": len(rows), "included_sessions": sum(row["included"] for row in rows),
        "accounts_with_included_sessions": len({row["username"] for row in rows if row["included"]}),
        "registration_sessions": sum(row["event_type"] == "registration" for row in rows), "login_sessions": len(logins),
        "technical_errors": sum(row["exclusion"] == "technical" for row in rows),
        "technical_login_errors": sum(row["exclusion"] == "technical" for row in logins),
        "incorrect_password_logins": sum(row["exclusion"] == "wrong_password" for row in logins),
        "changed_context_logins_before_exclusions": sum(row["context"] == "changed" for row in logins),
        "changed_context_technical_exclusions": sum(row["context"] == "changed" and row["exclusion"] == "technical" for row in logins),
        "changed_context_platform_changes_verified": sum(row["included"] and row["context"] == "changed" and row["platform_changed"] is True for row in logins),
        "eligible_login_acceptance": rate(overall["overall_acceptance"]["numerator"], overall["overall_acceptance"]["denominator"]),
        "overall_decisions": overall, "groups": groups,
        "technical_error_reasons": dict(Counter(row["exclusion_reason"] for row in rows if row["exclusion"] == "technical")),
        "platform_counts": dict(Counter(account.get("fingerprint", {}).get("details", {}).get("platform", "unknown")
                                        for account in dataset["accounts"])),
        "interval_mean_minutes": sum(row["interval_minutes"] for row in gaps) / len(gaps) if gaps else None,
        "interval_gaps": len(gaps), "interval_accounts": interval_accounts,
        "relay_markers": {"rows": len(marker_rows), "unique_ids": len(markers), "duplicates": marker_duplicates,
            "matched_ids": len(seen & markers.keys()), "unmatched_ids": len(missing_markers), "username_conflicts": marker_conflicts},
        "performance": {"files": report_count, "matched_events": len(matched_reports), "orphan_reports": orphan_reports,
            "missing_reports": missing_reports, "invalid_reports": invalid_reports, "outcome_disagreements": outcome_disagreements,
            "included_reports": sum(latency_counts.values()),
            "included_by_group": dict(latency_counts)},
        "excluded_previous_evaluation_sessions": dataset.get("excluded_previous_evaluation_sessions", 0),
        "source_warnings": dataset["warnings"]}
    write_json(run_dir / "statistics.json", summary)
    write_json(run_dir / "latency_statistics.json", latency)
    for filename, records in (("sessions.csv", rows), ("excluded_sessions.csv", exclusions),
                              ("intervals.csv", gaps), ("unmatched_relay_markers.csv", missing_markers),
                              ("latency_statistics.csv", latency)):
        fields = list(dict.fromkeys(key for record in records for key in record)) or ["no_records"]
        write_csv(run_dir / filename, records, fields)
    return summary

