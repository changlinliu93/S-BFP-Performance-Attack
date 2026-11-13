# Saved relay-result analysis

[statistics.py](../statistics.py) joins saved relay-session markers to database
events and computes the relayed-login outcomes in the report. The markers are
stored in [complement_data/relayed_session.jsonl](../../complement_data/relayed_session.jsonl).
[performance/](../performance/README.md) contains the client/server stage analysis.

```sh
python -m complement_scripts.reproduce
```

The standalone forwarding server, dependencies, and
deployment instructions are in [relay_emulation](../../relay_emulation/README.md)
at the repository root. They are separate from offline report reproduction.
