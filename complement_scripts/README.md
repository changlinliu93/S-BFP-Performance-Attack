# S-BFP supplementary analysis scripts

These scripts reproduce the current database report from existing records and
saved experiment logs. Report reproduction does not contact a server, launch a
browser, submit a login, change an account, or rerun an experiment.

The [current report](../report.md) contains the dataset, exclusions,
acceptance, session interval, relay outcomes, saved replay outcomes and native
client/server stage latency. Every report table is printed to the console and
saved as a CSV under the root-level `complement_result/` directory.

## Reproduction

Run from the repository root with Python 3.12 or later. The reporting command uses
only the standard library; the original application's environment is sufficient.

```sh
python -m complement_scripts.reproduce
```

Default inputs are `User_Manager/data`, `complement_data/relayed_session.jsonl`,
and the saved batch `complement_data/replay/20261112-183614-453293/`, containing
222 recorded-position/unchanged attempts. The supplementary inputs are described
in [complement_data](../complement_data/README.md).

All retained records and account paths already contain stable anonymous IDs;
reproduction does not use an identity mapping.

[report_template.md](report_template.md) preserves the report's introduction,
reviewer headings and discussion. Edit that file to change wording or layout;
the renderer fills its table and statistic placeholders from the source data.
The participant count and experiment instructions are study descriptions supplied
by the authors, not estimates calculated from account records.

`--data-dir` and `--relay-markers` override the input paths. `--output` selects
the generated-data directory, defaulting to `complement_result/`, and must remain
within it. `--report-file` selects the Markdown destination, defaulting to
`report.md` at the project root. `--template` selects an alternative wording and
layout template. Replacing existing output creates a verified ZIP
containing the report and generated data, and retains the previous data directory
under `complement_scripts/backups/`. Original source logs are read without modification.

Reproduction reads the database and supplementary inputs directly. The `complement_result/`
directory contains calculated outputs and source digests; it does not contain
copies of account records or native performance files. Keep `User_Manager/data/`
and `complement_data/` available when regenerating the report.

## Organization

| Location | Purpose |
| --- | --- |
| [reproduce.py](reproduce.py) | Offline command, input selection and report backups |
| [database.py](database.py) | Read account, event and performance records directly from the database |
| [statistics.py](statistics.py) | Account/platform counts, exclusions, intervals and cohort summaries |
| [report.py](report.py) | Markdown, console tables and CSV exports |
| [report_template.md](report_template.md) | Report wording, reviewer headings and table placement |
| [acceptance/](acceptance/README.md) | Login exclusions and expected-context decision rules |
| [performance/](performance/README.md) | Client/server modality-stage extraction and comparisons |
| [replay_emulation/](replay_emulation/README.md) | Saved replay summarization |
| [relay_emulation/](relay_emulation/README.md) | Saved relay-outcome analysis instructions |

Live implementations and operating instructions are located at the repository
root: [rearrangement_emulation](../rearrangement_emulation/README.md) and
[relay_emulation](../relay_emulation/README.md).

Retained input archives use the same anonymous IDs as the current database.
The old shared-work and end-to-end summary generators
are excluded from the maintained analysis code. Native raw performance files
remain unchanged as source evidence.

## Definitions

Technical/setup errors and incorrect passwords are counted and excluded from
acceptance, intervals and latency. Registrations remain in dataset and latency
counts, but do not enter login acceptance. Changed-seed logins are expected to
fail under the study protocol. Historical platform evidence is incomplete.
Replay outcomes are reported separately from historical user sessions.

Latency tables report milliseconds for Canvas and Audio separately. Each route's
average is `(Canvas mean + Audio mean) / 2`, not a session duration. Both modality
means must be present. CSV files retain numeric means and per-modality sample
counts. Missing values are never imputed as zero. Direct and relayed groups have
different account/session compositions and do not establish causal relay delay.

Pseudonymous IDs are consistent across current records, replay tables and retained
archives. The original username mapping has been removed. Fingerprint records
remain sensitive measurements; replacing usernames does not establish full anonymity.
