# Supplementary data for the current report

This directory retains the saved results needed to reproduce and interpret the
[current report](../report.md):

- [relayed_session.jsonl](relayed_session.jsonl): session markers used to identify
  relayed traffic in the original-server records.
- [replay/20261112-183614-453293/](replay/20261112-183614-453293/): the 222-attempt replay log, its referenced
  attempt reports, and the historical audit, batch summary and content-compatibility summary.

Account names in the retained records and paths have been replaced with stable
anonymous IDs. The reproduction command reads these IDs directly and requires no
username mapping.

Current account records and performance logs are read from `User_Manager/data`.
The report reads relay labels and saved replay evidence from this directory.
The relative report paths inside the replay log resolve within its batch folder.
New live replay runs are written under `rearrangement_emulation/results/` and are
not added to the report automatically. Generated report tables and source digests
are kept in the root-level `complement_result/` directory, and the Markdown
report is `report.md` at the root. Database inputs are read directly.

From the repository root:

```sh
python -m complement_scripts.reproduce
```

This command reads existing records without running experiments. Maintained
scripts and detailed instructions are in [complement_scripts](../complement_scripts/README.md).
