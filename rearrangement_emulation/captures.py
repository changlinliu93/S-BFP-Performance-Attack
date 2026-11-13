"""Load recorded responses."""

import json
from pathlib import Path


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_capture(record, record_path, modality):
    config_key = "drawConfig" if modality == "canvas" else "audioConfig"
    extension = "png" if modality == "canvas" else "pcm"
    baseline = record.get(f"{modality}_baseline")
    if isinstance(baseline, dict):
        baseline_seed = record.get(f"{modality}_baseline_seed") or next(
            (
                h["seed"]
                for h in record.get(f"{modality}_stability", [])
                if h.get("seed")
            ),
            None,
        )
        baseline = baseline.get(baseline_seed) or baseline.get("__default__")
    if not isinstance(baseline, str):
        raise ValueError(f"No enrolled {modality} baseline")
    for history in record.get(f"{modality}_stability", []):
        for run in history.get("runs", []):
            config = run[config_key]
            cid = config["challenge_id"]
            if len(cid) != 32 or any(c not in "0123456789abcdef" for c in cid):
                continue
            path = record_path.parent / modality / f"{cid}.{extension}"
            if path.is_file() and run.get("waveformHash", run.get("hash")) == baseline:
                config = json.loads(json.dumps(config))
                if modality == "canvas":
                    config["strings"] = [
                        x["text"] if isinstance(x, dict) else x
                        for x in config["strings"]
                    ]
                return {
                    "config": config,
                    "raw": path.read_bytes(),
                    "seed": history["seed"],
                    "baseline": baseline,
                }
    raise ValueError(f"No retained {modality} response matching its baseline was found")


def load_captures(record_path):
    record_path = Path(record_path).resolve()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    captures = {m: load_capture(record, record_path, m) for m in ("canvas", "audio")}
    if captures["canvas"]["seed"] != captures["audio"]["seed"]:
        raise ValueError("The selected captures use different seeds")
    return captures
