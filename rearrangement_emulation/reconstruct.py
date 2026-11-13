"""Reassemble captured responses using only old/new public challenge configs.

No browser rendering, oscillator synthesis, server secret, private shuffle order,
or expected fingerprint is used here. Responses are submitted by the HTTP client.
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops


def canvas_config(config):
    config = config.get("drawConfig", config)
    strings = [item["text"] if isinstance(item, dict) else item for item in config["strings"]]
    if not strings or len(strings) != len(config["xs"]) or len(strings) != len(config["ys"]):
        raise ValueError("Canvas strings and positions must have equal, nonzero lengths")
    return config, strings


def rearrange_canvas(recorded_png: bytes, old_config: dict, fresh_config: dict, *, match_content=True) -> bytes:
    """Translate captured glyph pixels onto a white canvas without re-rendering."""
    old, old_strings = canvas_config(old_config)
    fresh, fresh_strings = canvas_config(fresh_config)
    if not match_content and len(old_strings) != len(fresh_strings):
        raise ValueError("Position mapping requires the same number of Canvas rows")
    if old.get("font", "20px Arial") != fresh.get("font", "20px Arial"):
        raise ValueError("A different font requires a new recording")
    if old.get("font", "20px Arial") != "20px Arial":
        raise ValueError("This emulator implements the prototype's 20px Arial row geometry")
    with Image.open(io.BytesIO(recorded_png)) as source:
        source.load()
        if source.mode not in ("RGB", "RGBA"):
            raise ValueError("Expected an RGB/RGBA Canvas PNG")
        white = (255,) * len(source.getbands())
        width = fresh.get("canvasWidth", source.width)
        height = fresh.get("canvasHeight", source.height)
        target = Image.new(source.mode, (width, height), white)
        captures = {}
        for index, (text, x, y) in enumerate(zip(old_strings, old["xs"], old["ys"])):
            top, bottom = max(0, y - 4), min(source.height, y + 30)
            if bottom <= top:
                raise ValueError("Recorded Canvas row is outside the image")
            row = source.crop((0, top, source.width, bottom))
            # Keep every non-white pixel, including very light antialiasing.
            bbox = ImageChops.difference(row.convert("RGB"), Image.new("RGB", row.size, "white")).getbbox()
            if bbox is None:
                raise ValueError("Recorded Canvas row is empty")
            captures[text if match_content else index] = (row.crop(bbox), bbox[0] - x, top + bbox[1] - y)
        for index, (text, x, y) in enumerate(zip(fresh_strings, fresh["xs"], fresh["ys"])):
            key = text if match_content else index
            if key not in captures:
                raise ValueError(f"Unseen Canvas text: {text!r}")
            patch, dx, dy = captures[key]
            left, top = x + dx, y + dy
            if left < 0 or top < 0 or left + patch.width > width or top + patch.height > height:
                raise ValueError("Rearranged Canvas pixels would be clipped")
            target.paste(patch, (left, top))
        output = io.BytesIO()
        target.save(output, format="PNG")
        return output.getvalue()


def audio_config(config):
    config = config.get("audioConfig", config)
    count = config["count"]
    if count < 1 or len(config["frequencies"]) != count or len(config["waveforms"]) != count:
        raise ValueError("Invalid audio snippet count")
    if len(config["gap_frames"]) != count - 1 or any(gap < 0 for gap in config["gap_frames"]):
        raise ValueError("Invalid audio gap lengths")
    if config["snippet_frames"] < 1 or config["sample_rate"] < 1:
        raise ValueError("Invalid audio rendering parameters")
    if config["total_frames"] != count * config["snippet_frames"] + sum(config["gap_frames"]):
        raise ValueError("Audio frame counts do not agree")
    return config


def rearrange_audio(recorded_pcm: bytes, old_config: dict, fresh_config: dict, *, match_content=True) -> bytes:
    """Copy float32 sample bytes by sound identity and insert the new silent gaps."""
    old, fresh = audio_config(old_config), audio_config(fresh_config)
    if not match_content and any(old[key] != fresh[key] for key in ("count", "snippet_frames", "sample_rate")):
        raise ValueError("Position mapping requires matching Audio count, snippet length and sample rate")
    if len(recorded_pcm) != old["total_frames"] * 4:
        raise ValueError("Recorded PCM length does not match its old config")
    captures = {}
    offset = 0
    size = old["snippet_frames"] * 4
    for i, (frequency, waveform) in enumerate(zip(old["frequencies"], old["waveforms"])):
        key = (frequency, waveform, old["sample_rate"], old["snippet_frames"]) if match_content else i
        snippet = recorded_pcm[offset:offset + size]
        if key in captures and captures[key] != snippet:
            raise ValueError("Identical sound descriptions have different recorded samples")
        captures[key] = snippet
        offset += size + (old["gap_frames"][i] * 4 if i < old["count"] - 1 else 0)
    pieces = []
    for i, (frequency, waveform) in enumerate(zip(fresh["frequencies"], fresh["waveforms"])):
        key = (frequency, waveform, fresh["sample_rate"], fresh["snippet_frames"]) if match_content else i
        if key not in captures:
            raise ValueError(f"Unseen audio snippet: {key!r}")
        pieces.append(captures[key])
        if i < fresh["count"] - 1:
            pieces.append(b"\x00" * (fresh["gap_frames"][i] * 4))
    return b"".join(pieces)


