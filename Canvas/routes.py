import base64
import hashlib
import io
import secrets
import string
import struct
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import flask
from PIL import Image
from flask import Blueprint, jsonify, request, session

from config import derive_secret
from drbg import HMACDRBG
from performance_monitor import measure
from User_Manager.user_manager import store_raw_upload

canvas_bp = Blueprint(
    "canvas",
    __name__,
    template_folder="templates",
    static_folder="static",
)

_BASE_DIR = Path(__file__).resolve().parent
entropy = derive_secret("canvas")

db = {}


def map_bytes_to_string(data: bytes, num_emojis: int = 1) -> str:
    """
    Map each byte deterministically to a character.
    The resulting string has the same length as the input bytes.

    The last `num_emojis` characters are emojis,
    the preceding characters are from letters + digits + special chars.
    """
    if num_emojis < 0 or num_emojis > len(data):
        raise ValueError("num_emojis must be between 0 and len(data)")

    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    specials = string.digits + "!@#$%^&*()-_=+[]{};:,.<>/? "
    main_chars = uppercase + lowercase + specials
    emojis = ["😀", "😎", "🚀", "🔥", "✨", "💡", "✅", "🎉", "❤️", "🐍"]

    result = []
    cutoff = len(data) - num_emojis
    for b in data[:cutoff]:
        result.append(main_chars[b % len(main_chars)])
    for b in data[cutoff:]:
        result.append(emojis[b % len(emojis)])
    return "".join(result)


def tighten_image(img: Image.Image, threshold: int = 245) -> Image.Image:
    """
    Remove surrounding whitespace from a cropped canvas row.

    If an alpha channel exists we use it directly. Otherwise fall back to a
    grayscale mask that treats near-white pixels as background.
    """

    if "A" in img.getbands():
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        if bbox and bbox != (0, 0, img.width, img.height):
            return img.crop(bbox)

    rgb_img = img.convert("RGB")
    gray = rgb_img.convert("L")
    # Anything darker than the threshold is considered foreground.
    mask = gray.point(lambda px: 255 if px < threshold else 0)
    bbox = mask.getbbox()
    return rgb_img.crop(bbox) if bbox else rgb_img


@canvas_bp.route("/")
def index():
    return flask.send_file(_BASE_DIR / "index.html")


@canvas_bp.route("/get_string_config/<string:seed>/<int:n>/<int:width>/<int:height>")
def get_string_config(seed: str, n: int, width: int, height: int):
    if not (1 <= n <= 128):
        return jsonify({"error": "n must be between 1 and 128"}), 400
    if width < 962 or height < 128 or width > 8192 or height > 8192:
        return jsonify({"error": "unsupported canvas dimensions"}), 400

    with measure("challenge"):
        seed_bytes = seed.encode("utf-8")
        drbg_positions = HMACDRBG(
            entropy_input=entropy,
            nonce=struct.pack("d", float(datetime.now().timestamp())),
            personalization_string=seed_bytes,
        )
        drbg_strings = HMACDRBG(
            entropy_input=entropy,
            nonce=datetime.now().strftime("%Y-%m").encode("utf-8"),
            personalization_string=seed_bytes,
        )

        strings: List[str] = []
        xs: List[int] = []
        ys: List[int] = []
        y_cursor = 25

        for _ in range(n):
            x = drbg_positions.randint(2, width - 32 * 30)
            delta_y = drbg_positions.randint(40, 100)
            y_cursor += delta_y
            xs.append(x)
            ys.append(y_cursor)
            strings.append(map_bytes_to_string(drbg_strings.generate(32), 1))

        challenge_id = secrets.token_hex(16)
        db[challenge_id] = {"seed": seed, "config": (strings, xs, ys)}
    response = jsonify(
        {
            "challenge_id": challenge_id,
            "strings": strings,
            "xs": xs,
            "ys": ys,
            "font": "20px Arial",
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@canvas_bp.route("/upload_img/<string:challenge_id>", methods=["POST"])
def upload_img(challenge_id: str):
    username = session.get("pending_user") or session.get("authenticated_user")
    if not username:
        return jsonify(error="Authentication is required"), 401
    challenge = db.get(challenge_id)
    if challenge is None:
        return jsonify({"error": "Canvas challenge not found"}), 404

    with measure("body_read"):
        payload = request.get_json(silent=True) or {}
    data_url = payload.get("data")
    if not data_url:
        return jsonify({"error": "No image data provided"}), 400
    try:
        _, encoded = data_url.split(",", 1)
    except ValueError:
        return jsonify({"error": "Invalid data URL"}), 400

    try:
        with measure("decode"):
            raw_bytes = base64.b64decode(encoded, validate=True)
            img = Image.open(io.BytesIO(raw_bytes))
            if img.format != "PNG":
                return jsonify(error="Expected PNG image data"), 400
            img.load()

    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Invalid image data: {exc}"}), 400

    strings, xs, ys = challenge["config"]
    segments = []
    FONT_SIZE = 20
    TOP_PADDING = 4
    BOTTOM_PADDING = 10
    for string_val, x_pos, y_pos in zip(strings, xs, ys):
        with measure("primitive"):
            top = max(0, y_pos - TOP_PADDING)
            bottom = min(img.height, y_pos + FONT_SIZE + BOTTOM_PADDING)
            cropped_img = img.crop((0, top, img.width, bottom))
            tightened_img = tighten_image(cropped_img)
            primitive_bytes = tightened_img.tobytes()
        with measure("hash"):
            hash_value = hashlib.sha256(primitive_bytes).hexdigest()
        segments.append({"text": string_val, "hash": hash_value})
    with measure("hash"):
        final_hash = hashlib.sha256(
            "".join(sorted(segment["hash"] for segment in segments)).encode()
        ).hexdigest()
    try:
        store_raw_upload(username, "canvas", challenge_id, raw_bytes)
    except FileNotFoundError:
        return jsonify(error="User not found"), 404
    except FileExistsError as exc:
        return jsonify(error=str(exc)), 409
    except OSError:
        return jsonify(error="Failed to save canvas upload"), 500
    challenge.update(hash=final_hash, username=username, session_id=session.get("session_id"))
    return jsonify(
        {
            "message": "Image uploaded successfully",
            "hash": final_hash,
            "segments": segments,
        }
    )
