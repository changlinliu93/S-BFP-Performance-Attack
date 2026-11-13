"""Request-local timings with nested persistence intervals excluded."""

from contextlib import contextmanager
from functools import wraps
from time import perf_counter_ns

from flask import g, has_request_context, request


_MONITORED_ENDPOINTS = {
    "audio.get_snippets_config", "audio.upload_audio",
    "canvas.get_string_config", "canvas.upload_img",
    "register", "login", "acquire_session", "release_session",
    "capture_fingerprint", "record_audio_stability", "record_canvas_stability",
}


def _active():
    return has_request_context() and "sbfp_timings" in g


@contextmanager
def measure(name):
    if not _active():
        yield
        return
    started = perf_counter_ns()
    excluded_before = g.sbfp_storage_ns
    try:
        yield
    finally:
        elapsed = perf_counter_ns() - started
        elapsed -= g.sbfp_storage_ns - excluded_before
        g.sbfp_timings[name] = g.sbfp_timings.get(name, 0) + max(0, elapsed)


def exclude_storage(function):
    """Count the outer persistence call once, including nested save helpers."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _active():
            return function(*args, **kwargs)
        outermost = g.sbfp_storage_depth == 0
        started = perf_counter_ns()
        g.sbfp_storage_depth += 1
        try:
            return function(*args, **kwargs)
        finally:
            g.sbfp_storage_depth -= 1
            if outermost:
                g.sbfp_storage_ns += perf_counter_ns() - started
    return wrapped


def install_performance_monitor(app):
    @app.before_request
    def start_performance_request():
        if request.endpoint in _MONITORED_ENDPOINTS:
            g.sbfp_started_ns = perf_counter_ns()
            g.sbfp_timings = {}
            g.sbfp_storage_ns = 0
            g.sbfp_storage_depth = 0

    @app.after_request
    def finish_performance_request(response):
        started = g.get("sbfp_started_ns")
        if started is not None:
            wall = perf_counter_ns() - started
            g.sbfp_timings["total"] = max(0, wall - g.sbfp_storage_ns)
            g.sbfp_timings["storage"] = g.sbfp_storage_ns
            metrics = [
                f"sbfp_{name};dur={duration / 1_000_000:.6f}"
                for name, duration in g.sbfp_timings.items()
            ]
            response.headers.add("Server-Timing", ", ".join(metrics))
        return response
