"""Shared statistical and output helpers; no server imports."""

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Session timestamp has no timezone: " + value)
    return parsed.astimezone(timezone.utc)


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "p95": values[max(0, math.ceil(len(values) * .95) - 1)] if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None}


def rate(numerator, denominator):
    return {"accepted": numerator, "attempts": denominator,
            "percent": numerator / denominator * 100 if denominator else None}
