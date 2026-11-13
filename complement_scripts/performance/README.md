# Client and server stage latency

[stages.py](stages.py) reads native Canvas and Audio modality-stage measurements.
[report.py](../report.py) renders the client/server tables and computes the
equal-weight average of the two modality means for each route.

```sh
python -m complement_scripts.reproduce
```

The tables are printed and written to `client_stages.csv` and `server_stages.csv`
under `complement_result/`. `latency_statistics.csv` contains
distributions and sample sizes for all eligible sessions, logins, and same-context
logins. All durations are milliseconds. The table CSVs contain
numeric means and separate sample-count columns.

Only client and server modality stages are aggregated. Shared work and whole-login
end-to-end statistics are not generated. Raw native performance records retain
their original fields for provenance. The application monitor itself is unchanged.

Client stages exclude storage according to the native monitor. Pure rendering is
a subset of generation. Server application request total excludes storage. Missing
values are omitted; the Canvas/Audio average is unavailable if either modality mean
is missing. Parallel or overlapping stages must not be summed as sequential work.

The separate compatibility-log analyzer accepts previously recorded browser logs:

```sh
python -m complement_scripts.performance.relay_comparison PATH_TO_LOG.jsonl --output complement_result/relay-stage-comparison
```

It prints and saves the same client/server stage tables. Its historical comparison
uses successful logins only, unlike the main report's documented retained-session
cohort. These observational comparisons do not isolate geographic relay overhead.
