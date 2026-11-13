"""Lightweight audio test routes."""

from __future__ import annotations

import hashlib
import math
import secrets
import struct
from pathlib import Path
from datetime import datetime

import flask
from flask import Blueprint, current_app, jsonify, request, session

from config import derive_secret
from drbg import HMACDRBG
from performance_monitor import measure
from User_Manager.user_manager import store_raw_upload

audio_bp = Blueprint(
    "audio",
    __name__,
    template_folder="templates",
    static_folder="static",
)

_BASE_DIR = Path(__file__).resolve().parent
_ENTROPY = derive_secret("audio")
_WAVEFORMS = ("sine", "triangle", "square", "sawtooth")
db = {}


def _build_drbg(seed: str, salt: bytes) -> HMACDRBG:
    seed_bytes = seed.encode("utf-8")
    return HMACDRBG(
        entropy_input=_ENTROPY,
        nonce=salt,
        personalization_string=seed_bytes,
    )


@audio_bp.route("/")
def index() -> flask.Response:
    """Serve the streamlined audio stability UI."""
    return flask.send_file(_BASE_DIR / "index.html")


@audio_bp.route(
    "/get_snippets_config/"
    "<string:seed>/<int:duration>/<int:sample_rate>/<int:n>"
    "/<int:min_length>/<int:max_length>/<int:min_frequency>/<int:max_frequency>"
)
def get_snippets_config(
    seed: str,
    duration: int,
    sample_rate: int,
    n: int,
    min_length: int,
    max_length: int,
    min_frequency: int,
    max_frequency: int,
):
    """Keep sounds seed-stable while varying their placement for each challenge."""
    if not (
        1 <= len(seed) <= 256
        and 1 <= n <= 128
        and 1 <= duration <= 1000
        and 8000 <= sample_rate <= 96000
        and 1 <= min_length <= max_length <= 1000
        and 1 <= min_frequency <= max_frequency < sample_rate / 2
    ):
        return jsonify(error="Unsupported audio configuration"), 400

    with measure("challenge"):
        drbg_sound = _build_drbg(
            seed, salt=datetime.now().strftime("%Y-%m").encode("utf-8")
        )
        snippets = [
            (
                drbg_sound.randint(min_frequency, max_frequency),
                _WAVEFORMS[drbg_sound.randint(0, len(_WAVEFORMS) - 1)],
            )
            for _ in range(n)
        ]
        drbg_placement = _build_drbg(
            seed, salt=struct.pack("d", float(datetime.now().timestamp()))
        )
        order = list(range(n))
        for i in range(n - 1, 0, -1):
            j = drbg_placement.randint(0, i)
            order[i], order[j] = order[j], order[i]
        gaps = [drbg_placement.randint(min_length, max_length) for _ in range(n - 1)]
        snippet_frames = duration * sample_rate // 1000
        gap_frames = [gap * sample_rate // 1000 for gap in gaps]
        total_frames = n * snippet_frames + sum(gap_frames)
        if total_frames * 4 > current_app.config["MAX_CONTENT_LENGTH"]:
            return jsonify(error="Audio configuration exceeds upload limit"), 400

        challenge_id = secrets.token_hex(16)
        db[challenge_id] = {
            "seed": seed,
            "snippets": snippets,
            "order": order,
            "snippet_frames": snippet_frames,
            "gap_frames": gap_frames,
            "total_frames": total_frames,
        }

    response = jsonify(
        challenge_id=challenge_id,
        frequencies=[snippets[i][0] for i in order],
        waveforms=[snippets[i][1] for i in order],
        gaps=gaps,
        duration=duration,
        sample_rate=sample_rate,
        count=n,
        snippet_frames=snippet_frames,
        gap_frames=gap_frames,
        total_frames=total_frames,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@audio_bp.route("/upload_audio/<string:challenge_id>", methods=["POST"])
def upload_audio(challenge_id: str):
    """Restore raw float32 snippets and hash their sequential signed sum."""
    username = session.get("pending_user") or session.get("authenticated_user")
    if not username:
        return jsonify(error="Authentication is required"), 401
    challenge = db.get(challenge_id)
    if challenge is None:
        return jsonify(error="Audio challenge not found"), 404

    with measure("body_read"):
        raw = request.get_data()
    with measure("primitive"):
        if len(raw) != challenge["total_frames"] * 4:
            return jsonify(error="Unexpected audio buffer length"), 400
        samples = [value for (value,) in struct.iter_unpack("<f", raw)]
        if any(not math.isfinite(value) or abs(value) > 1 for value in samples):
            return jsonify(error="Invalid audio samples"), 400

        snippets = [None] * len(challenge["order"])
        offset = 0
        for position, original in enumerate(challenge["order"]):
            end = offset + challenge["snippet_frames"]
            snippets[original] = samples[offset:end]
            if not any(snippets[original]):
                return jsonify(error="Missing audio snippet"), 400
            gap = challenge["gap_frames"][position] if position < len(snippets) - 1 else 0
            if any(samples[end : end + gap]):
                return jsonify(error="Non-silent audio gap"), 400
            offset = end + gap

        total = 0.0
        for snippet in snippets:
            for value in snippet:
                total += value
        fingerprint_bytes = struct.pack("<d", total)
    try:
        store_raw_upload(username, "audio", challenge_id, raw)
    except FileNotFoundError:
        return jsonify(error="User not found"), 404
    except FileExistsError as exc:
        return jsonify(error=str(exc)), 409
    except OSError:
        return jsonify(error="Failed to save audio upload"), 500
    with measure("hash"):
        final_hash = hashlib.sha256(fingerprint_bytes).hexdigest()
    challenge.update(hash=final_hash, username=username, session_id=session.get("session_id"))
    return jsonify(hash=final_hash)
