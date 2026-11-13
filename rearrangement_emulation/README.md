# Replay evidence and emulation

The default report uses [the offline summarizer](../complement_scripts/replay_emulation/summarize.py) to recompute outcomes from
saved attempt logs and final response reports. It performs no network requests.

```sh
python -m complement_scripts.reproduce
```

The command prints replay rates and failure tables, and saves `replay_rates.csv`,
`replay_results.csv`, `replay_users.csv` and `replay_failure_observations.csv` in
`complement_result/`. IDs match the current pseudonymous database.
The report uses the saved recorded-position/unchanged batch. Success means final
authentication, not HTTP 200. Failure observations can overlap within one attempt.
Saved inputs are kept in [complement_data/replay](../complement_data/replay/).

## Separately invoked live tools

The retained config-assisted client submits historical Canvas PNG and Audio PCM
recordings to a running defense server. [run.py](run.py) is the live experiment
entry point; it is not called by report reproduction.
Each attempt requests one Canvas and one Audio challenge. Pillow is required.

```sh
python -m rearrangement_emulation.run --server http://127.0.0.1:5001 --username ACCOUNT_ID --record PATH_TO_RECORD.json --attempts 3
```

The account must have completed enrollment and corresponding recordings. The
password is prompted without echo. Three attempts means three per mode.
`--mode unchanged` and `--mode rearranged` select one mode; `comparison` alternates
both. `--output` must name a new directory. The default client matches recorded
text/tone identities to fresh configurations; incompatible content is a setup
error. Recorded-position historical results are explicitly labelled in the report.

The live command makes real login attempts. Default experiment outputs are written
under `rearrangement_emulation/results/`. Report reproduction remains an offline
command under `complement_scripts`.

The maintenance evaluator [evaluate.py](evaluate.py) retains the owner-side batch
workflow, including temporary test-password substitution and recovery. It is not
needed to reproduce any saved result. Its `restore` command uses the existing
private recovery journal; it should only be used for the corresponding interrupted
batch. [cleanup_replays.py](cleanup_replays.py) previews or removes proven replay
records, creating a backup before changes.

Live tools retain attack-specific local preparation timings and consume original
`Server-Timing` headers. These are not trusted server-observed relay measurements.
The report's latency section uses only original client/server modality stages.
