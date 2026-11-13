# Standalone relay emulator

The relay forwards a browser's login, seed, challenge and rendering traffic to
one configured defense server. It serves the upstream pages at a second origin,
retains upstream cookies separately for each browser, and forwards native timing
headers. Each login follows the upstream page's single Canvas/Audio iteration.

This server is separate from report reproduction. Existing session markers are
analyzed by [the offline command](../complement_scripts/reproduce.py); it does not launch the relay.

## Runtime

From the repository root:

```sh
python -m pip install -r relay_emulation/requirements.txt
python -m relay_emulation.run --upstream http://127.0.0.1:5001 --host 127.0.0.1 --port 5002
```

The browser-facing relay URL is `http://127.0.0.1:5002/`. The configured upstream
must be reachable to serve pages and complete logins. Registration occurs on the
direct site. A visible laboratory label identifies the relay page.

For standalone deployment, copy this folder's `__init__.py`, `run.py`, `server.py`,
`settings.py` and `requirements.txt` into a folder named `relay_emulation`, preserving
that package layout. The relay runtime imports
no defense application, account database, image processing package or browser
automation package. Install its requirements and run the same module command from
the package's parent directory. The upstream URL must identify the defense machine;
a loopback address identifies the relay's own machine.

`--host`, `--port`, `--cert` and `--key` configure the listener and TLS. Non-local
browser collection requires trusted HTTPS for equivalent Web Crypto behavior.
Upstream certificates are verified. `--added-rtt-ms` adds half the configured delay
before each upstream request and half after its response; this is a delay model,
not a simulation of bandwidth, jitter or geographic routing.

## Session correlation and existing logs

The relay logs the submitted account ID, original-server session ID, status and
verification result. The default file remains `relay_emulation/logs/sessions.jsonl`
at the repository root; `--session-log PATH` overrides it. This preserves the
location of existing deployment logs after code reorganization. Session IDs can
be joined to original-server `events.jsonl` and `performance/<session_id>.json`.

The current database report reads account, event and performance records from
`User_Manager/data/` and relay session markers from
`complement_data/relayed_session.jsonl`. It does not use local relay compatibility
results or screenshots.

Separately supplied compatibility browser logs can be analyzed without starting
either server:

```sh
python -m complement_scripts.performance.relay_comparison PATH_TO_LOG.jsonl --output complement_result/relay-stage-comparison
```

The command prints client/server tables and saves CSV files with Canvas, Audio and
modality-average means. This optional comparison is separate from the current
database report.
