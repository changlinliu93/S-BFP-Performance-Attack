"""Native Server-Timing parsing and the two attack-specific local measurements."""

import math
import statistics
import re

FIELDS = (
    "rearrangement_ms", "challenge_received_to_upload_start_ms",
)


def server_timings(header):
    """Read the original monitor's durations; do not estimate server time."""
    return {name: float(value) for name, value in re.findall(
        r"(?:^|,)\s*sbfp_(\w+)\s*;\s*dur=([0-9]+(?:\.[0-9]+)?)\s*(?=,|$)", header)
        if math.isfinite(float(value))}


def milliseconds(start, end):
    return (end - start) / 1_000_000


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "min_ms": values[0], "median_ms": statistics.median(values),
            "mean_ms": statistics.mean(values), "p95_ms": values[math.ceil(0.95 * len(values)) - 1],
            "max_ms": values[-1]}


def summarize(measurements):
    return {name: distribution([m["timing"][name] for m in measurements]) for name in FIELDS}
