# Saved replay-result analysis

[summarize.py](summarize.py) reads saved attempt logs and reports to reproduce
replay success rates and failure observations. It makes no network requests.

```sh
python -m complement_scripts.reproduce
```

The command prints the report tables and writes CSV files under
the root-level `complement_result/` directory.

Saved replay inputs are kept in [complement_data/replay](../../complement_data/replay/).

The replay implementation, settings, and operating instructions are
in [rearrangement_emulation](../../rearrangement_emulation/README.md) at the
repository root.
