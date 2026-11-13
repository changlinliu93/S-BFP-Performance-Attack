"""Offline report reproduction from the current database and saved experiment logs."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import zipfile
from .common import write_json
from .database import load_database
from .statistics import analyze
from .replay_emulation.summarize import summarize_saved
from .report import REPORT_TEMPLATE, render_report

PROJECT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT / "complement_data/replay"
RESULTS_DIR = PROJECT / "complement_result"
REPORT_FILE = PROJECT / "report.md"
BACKUPS = PROJECT / "complement_scripts/backups"


def backup_output(output, report_file):
    sources = {source: source.relative_to(PROJECT).as_posix()
               for source in output.rglob("*") if source.is_file()} if output.exists() else {}
    if report_file.is_file():
        sources[report_file] = report_file.relative_to(PROJECT).as_posix()
    if not sources:
        return None
    path = BACKUPS / ("report-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ") + ".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in sources.items():
            archive.write(source, name)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Report backup failed integrity verification")
        for source, name in sources.items():
            if archive.read(name) != source.read_bytes():
                raise RuntimeError("Report backup contents differ")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESULTS_DIR, help="Directory for generated tables and data")
    parser.add_argument("--report-file", type=Path, default=REPORT_FILE, help="Markdown report destination")
    parser.add_argument("--template", type=Path, default=REPORT_TEMPLATE, help="Report wording and layout template")
    parser.add_argument("--data-dir", type=Path, default=PROJECT / "User_Manager/data")
    parser.add_argument("--relay-markers", type=Path, default=PROJECT / "complement_data/relayed_session.jsonl")
    args = parser.parse_args()
    output = args.output.resolve()
    report_file = args.report_file.resolve()
    template_file = args.template.resolve()
    if not template_file.is_file():
        parser.error("Report template does not exist")
    template_digest = hashlib.sha256(template_file.read_bytes()).hexdigest()
    if not output.is_relative_to(RESULTS_DIR):
        parser.error("Output must be complement_result/ or a directory within it")
    if not report_file.is_relative_to(PROJECT) or report_file.suffix.lower() != ".md":
        parser.error("Report file must be a Markdown file within the project")
    previous = backup_output(output, report_file)
    if output.exists():
        # Preserve the prior directory as well as the ZIP; no recursive deletion.
        archived = BACKUPS / (output.name + "-previous-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
        assert archived.resolve().is_relative_to(BACKUPS)
        BACKUPS.mkdir(parents=True, exist_ok=True)
        output.rename(archived)
    output.mkdir(parents=True)
    dataset, source_digests = load_database(args.data_dir, args.relay_markers)
    experiments = [("recorded-position", ARCHIVE / "20261112-183614-453293")]
    source_digests.update(summarize_saved(experiments, output))
    analyze(output, dataset)
    # Anonymous provenance labels avoid exposing names inside old report paths.
    provenance = [{"source_id": f"source_{i:04d}", "sha256": digest,
                   "type": "saved_replay" if "replays" in path.parts else "database_or_log"}
                  for i,(path,digest) in enumerate(sorted(source_digests.items()),1)]
    write_json(output / "provenance.json", {"sources": provenance, "experiments": [d.name for _,d in experiments],
        "method": "Current database and existing replay attempt logs; no source copies, network or experiment execution.",
        "report_template_sha256": template_digest,
        "previous_output_backup": previous.name if previous else None})
    render_report(output, report_file, template_file)
    if hashlib.sha256(template_file.read_bytes()).hexdigest() != template_digest:
        raise RuntimeError("Report template changed during generation")
    for path, digest in source_digests.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Input changed during report generation")
    write_json(output / "verification.json", {"source_files_unchanged": len(source_digests), "experiments_run": 0,
        "replay_attempt_logs_checked_against_saved_outcomes": True, "source_sha256_verified": True})
    print("\nReport:", report_file)


if __name__ == "__main__":
    main()
