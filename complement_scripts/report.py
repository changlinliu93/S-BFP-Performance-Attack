"""Render third-party review tables to Markdown, the console and CSV files."""
import csv
import json
import os
import re
from pathlib import Path
from .common import write_csv
from .acceptance.classify import fmt_ratio
from .performance.stages import CLIENT_STAGES, SERVER_STAGES, modality_average


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def format_cell(value):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def stage_rows(latency, side):
    lookup = {(v["group"],v["scope"],v["metric"]):v for v in latency
              if v["side"] == side and v["cohort"] == "all_included_sessions"}
    rows = []
    for label, metric in CLIENT_STAGES if side == "client" else SERVER_STAGES:
        row = {"Stage": label}
        counts = {}
        for group in ("direct", "relayed"):
            values = []
            for modality in ("canvas", "audio"):
                item = lookup.get((group,modality,metric), {})
                value, count = item.get("mean"), item.get("n", 0)
                row[f"{group.title()} {modality.title()} (ms)"] = value
                counts[f"{group}_{modality}_n"] = count
                values.append(value)
            row[f"{group.title()} average (ms)"] = modality_average(*values)
        row.update(counts)
        rows.append(row)
    return rows


REPORT_TEMPLATE = Path(__file__).with_name("report_template.md")


def render_report(run_dir, report_file=None, template_file=REPORT_TEMPLATE):
    output = Path(run_dir).resolve()
    report_file = Path(report_file).resolve() if report_file is not None else output / "REPORT.md"
    template = Path(template_file).read_text(encoding="utf-8")
    titles = {name: title for title, name in re.findall(r"(?m)^### (.+)\n\n\{\{table:([a-z_]+)\}\}", template)}
    stats = read(output / "statistics.json")
    latency = read(output / "latency_statistics.json")
    tables = {}

    def table(name, rows, visible=None):
        if not rows:
            write_csv(output / (name + ".csv"), [], ["no_records"])
            text = "No qualifying records."
        else:
            keys = list(dict.fromkeys(key for row in rows for key in row))
            visible = visible or keys
            write_csv(output / (name + ".csv"), rows, keys)
            lines = ["| " + " | ".join(visible) + " |", "| " + " | ".join("---" for _ in visible) + " |"]
            lines += ["| " + " | ".join(format_cell(row.get(key)) for key in visible) + " |" for row in rows]
            text = "\n".join(lines)
        tables[name] = text
        print("\n" + titles.get(name, name) + "\n" + text)

    table("dataset_counts", [{"Route": group,
        "Raw sessions": stats["groups"][group]["sessions"], "Technical errors": stats["groups"][group]["technical_errors"],
        "Wrong passwords": stats["groups"][group]["wrong_passwords"], "Retained sessions": stats["groups"][group]["included_sessions"],
        "Retained logins": stats["groups"][group]["eligible_logins"]}
        for group in ("direct", "relayed", "unclassified") if stats["groups"][group]["sessions"]])
    table("platform_statistics", [{"Platform": key, "Accounts": value} for key, value in sorted(stats["platform_counts"].items())])
    table("technical_error_counts", [{"Reason": key,
        "Direct": stats["groups"]["direct"]["technical_error_reasons"].get(key, 0),
        "Relayed": stats["groups"]["relayed"]["technical_error_reasons"].get(key, 0), "Total": value}
        for key, value in sorted(stats["technical_error_reasons"].items())])
    table("wrong_password_counts", [{"Route": group, "Incorrect passwords": stats["groups"][group]["wrong_passwords"]}
        for group in ("direct", "relayed")])
    table("session_interval", [{"Minutes": stats["interval_mean_minutes"]}])
    measures = [("Same-context acceptance", "same_context_acceptance"), ("Same-context false rejection", "same_context_false_rejection"),
        ("Changed-context correct rejection", "changed_context_correct_rejection"), ("Changed-context false acceptance", "changed_context_false_acceptance"),
        ("Context-policy correctness", "context_policy_correctness"), ("Overall login acceptance", "overall_acceptance")]
    table("acceptance_rates", [{"Measure": label,
        "Direct": fmt_ratio(stats["groups"]["direct"][key]), "Relayed": fmt_ratio(stats["groups"]["relayed"][key]),
        "All": fmt_ratio(stats["overall_decisions"][key])} for label, key in measures])
    changed = [row for row in csv_rows(output / "sessions.csv")
        if row["included"] == "True" and row["event_type"] == "login" and row["context"] == "changed"]
    table("changed_context_sessions", [{"Account ID": row["username"], "Session ID": row["session_id"],
        "Route": row["group"], "Result": row["result"], "Canvas match": row["canvas_matches"], "Audio match": row["audio_matches"]}
        for row in changed])
    table("relay_failure_counts", [{"Reason": key, "Sessions": value}
        for key, value in sorted(stats["groups"]["relayed"]["raw_failure_reasons"].items())])
    for side in ("client", "server"):
        rows = stage_rows(latency, side)
        table(side + "_stages", rows, list(rows[0])[:7])
    replay_path = output / "replay_summary.json"
    replay = read(replay_path) if replay_path.exists() else []
    saved_replay = isinstance(replay, list) and bool(replay)
    table("replay_rates", replay if saved_replay else [])
    table("replay_failure_observations", read(output / "replay_failures.json") if saved_replay else [])
    replay_users = csv_rows(output / "replay_users.csv") if saved_replay else []
    values = {key: stats[key] for key in ("accounts", "sessions", "registration_sessions", "login_sessions",
        "technical_errors", "incorrect_password_logins", "included_sessions", "interval_gaps", "interval_accounts")}
    values.update(relayed_changed_rejections=stats["groups"]["relayed"]["changed_context_correct_rejection"]["numerator"],
        relay_rows=stats["relay_markers"]["rows"], relay_matched=stats["relay_markers"]["matched_ids"],
        relay_unmatched=stats["relay_markers"]["unmatched_ids"], performance_files=stats["performance"]["files"],
        performance_included=stats["performance"]["included_reports"],
        performance_direct=stats["performance"]["included_by_group"].get("direct", 0),
        performance_relayed=stats["performance"]["included_by_group"].get("relayed", 0),
        replay_accounts=len({row["username"] for row in replay_users}) if saved_replay else "N/A")

    def substitute(match):
        token = match.group(1)
        if token.startswith("table:"):
            return tables[token[6:]]
        if token.startswith("result:"):
            return Path(os.path.relpath(output / token[7:], report_file.parent)).as_posix()
        if token not in values:
            raise ValueError("Unknown report-template field: " + token)
        return str(values[token])

    rendered = re.sub(r"\{\{([^{}]+)\}\}", substitute, template)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(rendered, encoding="utf-8")
