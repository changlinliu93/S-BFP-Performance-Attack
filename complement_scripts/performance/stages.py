"""Client and server modality-stage measurements, in milliseconds."""
import math
CLIENT_STAGES = (
    ("Static fingerprint / Seed", "static"),
    ("Challenge request / response", "challenge"),
    ("Stochastic fingerprint generation", "dynamic"),
    ("Result upload / server verification", "result"),
    ("Pure rendering (within generation)", "pure_render"),
)


SERVER_STAGES = (
    ("Challenge generation", "challenge"), ("Primitive extraction", "primitive"),
    ("Hash calculation", "hash"), ("Fingerprint verification", "verify"),
    ("Image decoding", "decode"), ("File / log time excluded", "storage"),
    ("Request body read / JSON parse", "body_read"), ("Application request total", "total"),
)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def metrics(report):
    result = {}
    for modality in ("canvas", "audio"):
        part = report.get("modalities", {}).get(modality, {})
        client = {key: part.get("stages_ms", {}).get(key) for key in ("static", "challenge", "dynamic", "result")}
        client.update(pure_render=part.get("pure_render_ms"))
        for side, values in (("client", client), ("server", {key: part.get("server_ms", {}).get(key) for _, key in SERVER_STAGES})):
            for key, value in values.items():
                if finite(value): result[(modality, side, key)] = value
    return result


def modality_average(canvas, audio):
    """Equal weight for the two modality means; missing modality gives no mean."""
    return (canvas + audio) / 2 if finite(canvas) and finite(audio) else None
