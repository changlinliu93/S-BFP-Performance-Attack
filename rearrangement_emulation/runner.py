"""Run live-server experiments and compare preparation costs."""

import csv
from datetime import datetime
import getpass
from pathlib import Path
from urllib.parse import urlsplit

from .captures import load_captures, write_json
from .client import run_attempt
from .timing import FIELDS, summarize

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def project_path(path):
    path = Path(path).expanduser()
    return (path if path.is_absolute() else PROJECT_DIR / path).resolve()


def run_experiment(url, username, record_path, mode="comparison", attempts=3,
                   output_dir=None, password=None):
    if mode not in ("comparison", "unchanged", "rearranged"):
        raise ValueError("mode must be comparison, unchanged, or rearranged")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("url must be an HTTP(S) origin without credentials, path, query, or fragment")
    if not username or not username.strip():
        raise ValueError("username is required")
    record_path = project_path(record_path)
    output = (project_path(output_dir) if output_dir is not None else
              PACKAGE_DIR / "results" / f"live-{datetime.now():%Y%m%d-%H%M%S-%f}")
    if output.is_relative_to(record_path.parent):
        raise ValueError("Output must be outside the retained account directory")
    if output.exists():
        raise FileExistsError(f"Choose a new output directory: {output}")
    captures = load_captures(record_path)  # Disk reads are outside the timed path.
    if password is None:
        password = getpass.getpass(f"Password for {username}: ")
    output.mkdir(parents=True)
    report = {
        "output_dir": str(output), "server_url": url, "mode": mode,
        "attempts_per_mode": attempts, "attempts": [],
        "timing_clock": "time.perf_counter_ns",
        "iterations_per_modality": 1,
        "server_timing_source": "Original server's Server-Timing header; total excludes storage",
        "timing_boundary": "Full challenge response body received to immediately before upload HTTP call",
        "comparison_reference": "Unchanged recorded-response replay, not legitimate browser rendering",
        "preloaded_recordings": True,
    }
    used_ids = {capture["config"]["challenge_id"] for capture in captures.values()}
    for trial in range(1, attempts + 1):
        modes = ["unchanged", "rearranged"] if mode == "comparison" else [mode]
        if trial % 2 == 0:
            modes.reverse()  # Alternate order to reduce drift between modes.
        for scenario in modes:
            result = run_attempt(url, username, password, captures,
                                 output / f"trial-{trial:03d}-{scenario}", scenario, used_ids)
            result["trial"] = trial
            report["attempts"].append(result)
            write_json(output / "report.json", report)
            if result.get("client_error"):
                raise RuntimeError(f"Experiment incomplete: {result['client_error']}. Report: {output / 'report.json'}")

    summaries = {}
    for scenario in ("unchanged", "rearranged"):
        selected = [attempt for attempt in report["attempts"] if attempt["mode"] == scenario]
        if not selected:
            continue
        summaries[scenario] = {
            "login_attempts": len(selected),
            "successful_logins": sum(a["login_outcome"].get("result") == "success" for a in selected),
            "modalities": {},
        }
        for modality in ("canvas", "audio"):
            measurements = [m for a in selected for m in a["modalities"][modality]["measurements"]]
            summaries[scenario]["modalities"][modality] = {
                "samples": len(measurements),
                "baseline_matches": sum(m["matches_recorded_baseline"] for m in measurements),
                "timing": summarize(measurements),
            }
    report["summary"] = summaries
    if mode == "comparison":
        field = "challenge_received_to_upload_start_ms"
        report["additional_preparation_vs_unchanged_ms"] = {
            modality: {
                "difference_of_means_ms": summaries["rearranged"]["modalities"][modality]["timing"][field]["mean_ms"]
                    - summaries["unchanged"]["modalities"][modality]["timing"][field]["mean_ms"],
                "difference_of_medians_ms": summaries["rearranged"]["modalities"][modality]["timing"][field]["median_ms"]
                    - summaries["unchanged"]["modalities"][modality]["timing"][field]["median_ms"],
            } for modality in ("canvas", "audio")
        }
    write_json(output / "report.json", report)
    with (output / "timings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trial", "mode", "modality", "iteration",
            "challenge_id", "http_status", "matches_recorded_baseline", *FIELDS])
        writer.writeheader()
        for attempt in report["attempts"]:
            for modality, data in attempt["modalities"].items():
                for measurement in data["measurements"]:
                    writer.writerow({"trial": attempt["trial"], "mode": attempt["mode"], "modality": modality,
                        **{k: measurement[k] for k in ("iteration", "challenge_id", "http_status", "matches_recorded_baseline")},
                        **measurement["timing"]})
    server_fields = sorted({key for attempt in report["attempts"]
                            for response in attempt["requests"] for key in response["server_ms"]})
    with (output / "request_timings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trial", "mode", "endpoint", "status",
            "rpc_wall_ms", "rpc_adjusted_ms", *[f"server_{key}_ms" for key in server_fields]])
        writer.writeheader()
        for attempt in report["attempts"]:
            for response in attempt["requests"]:
                writer.writerow({"trial": attempt["trial"], "mode": attempt["mode"],
                    **{key: response[key] for key in ("endpoint", "status", "rpc_wall_ms", "rpc_adjusted_ms")},
                    **{f"server_{key}_ms": value for key, value in response["server_ms"].items()}})
    print_summary(report)
    return report


def print_summary(report):
    for mode, result in report["summary"].items():
        print(f"{mode}: {result['successful_logins']}/{result['login_attempts']} successful logins")
        for modality, data in result["modalities"].items():
            prep = data["timing"]["challenge_received_to_upload_start_ms"]
            rearrange = data["timing"]["rearrangement_ms"]
            print(f"  {modality}: n={data['samples']}, receipt-to-upload mean={prep['mean_ms']:.3f} ms, "
                  f"p95={prep['p95_ms']:.3f} ms; rearrangement mean={rearrange['mean_ms']:.3f} ms")
    for modality, delta in report.get("additional_preparation_vs_unchanged_ms", {}).items():
        print(f"  {modality} additional preparation vs unchanged (difference of means): "
              f"{delta['difference_of_means_ms']:.3f} ms")
    print(f"Report: {Path(report['output_dir']) / 'report.json'}")
