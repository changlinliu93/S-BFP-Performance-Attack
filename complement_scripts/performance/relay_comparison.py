"""Compare native performance reports; no extra timers or inferred detection thresholds."""

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path


def nonnegative(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def validate_report(report):
    if report.get("schema_version") != 2 or report.get("outcome") not in {"success", "failure", "error"}:
        raise AssertionError("Expected the original schema-version-2 performance report")
    if report["outcome"] == "success" and set(report["modalities"]) != {"canvas", "audio"}:
        raise AssertionError("Successful report must contain both modalities")
    for modality, data in report["modalities"].items():
        for value in [data["pure_render_ms"],
                      *data["stages_ms"].values(), *data["server_ms"].values()]:
            if not nonnegative(value):
                raise AssertionError("Invalid native modality measurement")
        if report["outcome"] == "success":
            uploads = "image_upload" if modality == "canvas" else "audio_upload"
            for label in ("challenge", uploads, "stability_report"):
                requests = [entry for entry in data["requests"] if entry["label"] == label]
                if len(requests) != 1 or requests[0]["status"] != 200:
                    raise AssertionError(f"Expected exactly one successful {modality}/{label}")
                if not nonnegative(requests[0]["server_ms"].get("total")):
                    raise AssertionError("Native Server-Timing header is missing (or the relay dropped it)")
            if len(data["artifacts"]) != 1:
                raise AssertionError("Expected one rendered artifact per modality")


def summary(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "mean_ms": None, "median_ms": None, "p95_ms": None}
    return {"count": len(values), "mean_ms": statistics.mean(values),
            "median_ms": statistics.median(values), "p95_ms": values[math.ceil(.95 * len(values)) - 1]}


def metrics(report):
    from .stages import metrics as stage_metrics
    return {".".join(key):value for key,value in stage_metrics(report).items()}


def build_report(rows):
    attempts = {}
    for row in rows:
        if row.get("event") != "native_browser_attempt" or row.get("event_type") != "login":
            continue
        validate_report(row["performance"])
        if row.get("mode") not in {"direct", "relay"}:
            raise ValueError("Unknown comparison mode")
        attempts[(row["mode"], row["performance"]["session_id"])] = row
    if not attempts:
        raise ValueError("No native browser reports found; old probe logs are intentionally unsupported")
    successful = [row for row in attempts.values() if row["performance"]["outcome"] == "success"]
    samples = [{"mode": row["mode"], "session_id": row["performance"]["session_id"], **metrics(row["performance"])}
               for row in successful]
    fields = sorted({key for row in samples for key in row} - {"mode", "session_id"})
    comparisons = {}
    for field in fields:
        direct, relay = [summary([row[field] for row in samples if row["mode"] == mode and field in row])
                         for mode in ("direct", "relay")]
        comparisons[field] = {"direct": direct, "relay": relay,
            "extra_median_ms": relay["median_ms"] - direct["median_ms"] if direct["count"] and relay["count"] else None,
            "extra_mean_ms": relay["mean_ms"] - direct["mean_ms"] if direct["count"] and relay["count"] else None}
    return {"schema_version": 2, "iterations_per_modality": 1,
            "measurement_source": "Unmodified native PerformanceMonitor and forwarded Server-Timing",
            "outcomes": {mode: dict(Counter(row["performance"]["outcome"] for row in attempts.values() if row["mode"] == mode))
                         for mode in ("direct", "relay")},
            "excluded_unsuccessful_logins": len(attempts) - len(successful), "metrics": comparisons, "samples": samples,
            "limitations": [
                "Wall time includes persistence; adjusted time uses the original monitor's storage exclusion.",
                "Browser stage durations are client observations, even when stored on the server.",
                "Server-Timing describes individual request handling, not server-issued-challenge to upload arrival.",
                "No server-side relay-detection threshold can be inferred from these browser reports alone.",
                "The Python relay buffers bodies; localhost/simulated delay is not a production network study."]}


def write_report(report, output):
    from complement_scripts.common import write_csv, write_json
    from complement_scripts.report import stage_rows, format_cell
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "report.json", report)
    write_csv(output / "login_timings.csv", report["samples"], ["mode", "session_id", *report["metrics"]])
    latency = []
    for key, comparison in report["metrics"].items():
        scope, side, metric = key.split(".")
        for group in ("direct", "relay"):
            item = comparison[group]
            latency.append({"cohort":"all_included_sessions", "group":"relayed" if group == "relay" else group,
                "scope":scope,"side":side,"metric":metric,"mean":item["mean_ms"],"n":item["count"]})
    lines = ["# Saved relay-stage comparison", "", "Only successful logins enter this compatibility-log comparison. "
             "The main database report applies its separately documented exclusions.", ""]
    for side in ("client", "server"):
        rows = stage_rows(latency, side)
        fields = list(rows[0]); visible = fields[:7]
        write_csv(output / (side + "_stages.csv"), rows, fields)
        table = ["| " + " | ".join(visible) + " |", "| " + " | ".join("---" for _ in visible) + " |"]
        table += ["| " + " | ".join(format_cell(row[k]) for k in visible) + " |" for row in rows]
        print("\n" + side.title() + " stages\n" + "\n".join(table))
        lines.extend(["## " + side.title() + " stages", "", *table, "", "[CSV](" + side + "_stages.csv)", ""])
    lines += ["The average column gives equal weight to the Canvas and Audio means; both must be present. "
              "CSV files include the sample counts. All durations are milliseconds.", "", "## Interpretation", ""]
    lines += ["- " + note for note in report["limitations"]]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows, seen = [], set()
    for path in args.inputs:
        for file in sorted(path.glob("*.jsonl")) if path.is_dir() else [path]:
            if file.resolve() not in seen:
                seen.add(file.resolve())
                rows.extend(json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip())
    write_report(build_report(rows), args.output)
    print(args.output / "report.md")


if __name__ == "__main__":
    main()
