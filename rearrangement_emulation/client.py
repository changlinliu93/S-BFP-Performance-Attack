"""Live HTTP attack client. No Flask imports, local verifier, or baseline writes."""

import base64
import hashlib
from time import perf_counter_ns
from urllib.parse import quote

from .captures import write_json
from .reconstruct import rearrange_audio, rearrange_canvas
from .timing import milliseconds, summarize


from .transport import HttpClient, Response


def run_attempt(url, username, password, captures, output, mode, used_ids, *, match_content=True):
    output.mkdir(parents=True, exist_ok=False)
    client = HttpClient(url)
    seed = captures["canvas"]["seed"]
    results = {"mode": mode, "modalities": {}, "fresh_challenge_ids_only": True,
               "reconstruction_mapping": "content" if match_content else "recorded-position",
               "iterations_per_modality": 1, "requests": client.requests,
               "execution": "sequential Canvas then Audio; no browser seed/render step"}
    acquired = None
    artifacts = []
    try:
        login = client.checked("/login", {"username": username, "password": password, "seed": seed}).body
        results["session_id"] = login["session_id"]
        acquired = client.checked("/session/acquire", {"username": username}).body
        for modality in ("canvas", "audio"):
            capture, runs, measurements = captures[modality], [], []
            old = capture["config"]
            results["modalities"][modality] = {"measurements": measurements}
            if modality == "canvas":
                width, height = old.get("canvasWidth", 1000), old.get("canvasHeight", 1000)
                path = f'/canvas/get_string_config/{quote(seed, safe="")}/{len(old["strings"])}/{width}/{height}'
            else:
                path = (f'/audio/get_snippets_config/{quote(seed, safe="")}/{old["duration"]}'
                        f'/{old["sample_rate"]}/{old["count"]}/20/80/200/2000')
            challenge = client.checked(path)
            config = challenge.body
            if modality == "canvas":
                config.update(canvasWidth=width, canvasHeight=height)
            cid = config["challenge_id"]
            if cid in used_ids:
                results["fresh_challenge_ids_only"] = False
                raise RuntimeError("Server reused a challenge ID")
            used_ids.add(cid)

            transform = rearrange_canvas if modality == "canvas" else rearrange_audio
            before_rearrange = perf_counter_ns()
            raw = transform(capture["raw"], old, config, match_content=match_content) if mode == "rearranged" else capture["raw"]
            after_rearrange = perf_counter_ns()
            if modality == "canvas":
                request = client.prepare(f"/canvas/upload_img/{cid}", data={
                    "data": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")})
            else:
                request = client.prepare(f"/audio/upload_audio/{cid}", raw=raw)
            upload = client.send(request)

            # Hashing, result storage and disk writes happen after upload.
            rearrangement_ms = milliseconds(before_rearrange, after_rearrange) if mode == "rearranged" else 0.0
            preparation_ms = milliseconds(challenge.received_ns, upload.started_ns)
            timing = {
                "rearrangement_ms": rearrangement_ms,
                "challenge_received_to_upload_start_ms": preparation_ms,
            }
            body = upload.body
            accepted = upload.status == 200
            measurements.append({"iteration": 1, "challenge_id": cid,
                "http_status": upload.status, "fingerprint": body.get("hash"), "error": body.get("error"),
                "matches_recorded_baseline": accepted and body.get("hash") == capture["baseline"],
                "raw_differs_from_recording": raw != capture["raw"],
                "raw_sha256": hashlib.sha256(raw).hexdigest(), "timing": timing,
                "server_timing": {"challenge": challenge.server_ms, "upload": upload.server_ms}})
            if accepted:
                runs.append({"hash": body["hash"], "waveformHash": body["hash"],
                             "drawConfig" if modality == "canvas" else "audioConfig": config})
            artifacts.append((modality, config, raw))
            if upload.status >= 500:
                raise RuntimeError(f"Upload failed: HTTP {upload.status}")
            modality_result = results["modalities"][modality]
            modality_result["timing_summary"] = summarize(measurements)
            if runs:
                response = client.request(f"/user/{modality}_stability", {
                    "username": username, "seed": seed, "testRuns": runs})
                modality_result.update(report_status=response.status, report_result=response.body)
                if response.status >= 500:
                    raise RuntimeError(f"Stability report failed: HTTP {response.status}")
    except Exception as exc:
        results["client_error"] = str(exc)
    finally:
        if acquired is not None:
            try:
                response = client.request("/session/release", {
                    "username": username, "token": acquired["token"],
                    "reason": "attack_client_error" if results.get("client_error") else "tests_complete",
                    **({"error": results["client_error"]} if results.get("client_error") else {}),
                })
                results.update(release_status=response.status, login_outcome=response.body)
                if response.status != 200:
                    results["client_error"] = f"Session release failed: HTTP {response.status}"
            except Exception as exc:
                results["release_error"] = str(exc)
                results.setdefault("client_error", "Could not obtain the final login outcome")
        # Persist artifacts only after the login is finalized.
        for modality, config, raw in artifacts:
            write_json(output / f"fresh_{modality}_config.json", config)
            (output / f'{modality}.{"png" if modality == "canvas" else "pcm"}').write_bytes(raw)
        write_json(output / "report.json", results)
    return results
