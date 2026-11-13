"""HTTP transport only; no rendering, extraction or server imports."""
from dataclasses import dataclass
from http.cookiejar import CookieJar
import json
from time import perf_counter_ns
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request
from .timing import milliseconds, server_timings


@dataclass
class Response:
    status: int
    body: dict
    started_ns: int
    received_ns: int
    server_ms: dict


class HttpClient:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.requests = []

    def prepare(self, path, data=None, raw=None):
        headers, body = {}, None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            body = raw
            headers["Content-Type"] = "application/octet-stream"
        return Request(self.url + path, data=body, headers=headers)

    def send(self, request):
        # This is the upload-start boundary: immediately before the HTTP call.
        started = perf_counter_ns()
        try:
            response = self.opener.open(request, timeout=60)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            received = perf_counter_ns()  # Full response body received, before JSON decoding.
            status = response.code
            native = server_timings(", ".join(response.headers.get_all("Server-Timing", [])))
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            body = {"error": payload.decode("utf-8", errors="replace")[:500]}
        if not isinstance(body, dict):
            body = {"error": "Expected a JSON object from the server"}
        wall = milliseconds(started, received)
        self.requests.append({
            "endpoint": "/".join(urlsplit(request.full_url).path.split("/")[1:3]),
            "status": status, "rpc_wall_ms": wall,
            "rpc_adjusted_ms": max(0, wall - native.get("storage", 0)), "server_ms": native,
        })
        return Response(status, body, started, received, native)

    def request(self, path, data=None):
        return self.send(self.prepare(path, data=data))

    def checked(self, path, data=None):
        response = self.request(path, data)
        if response.status != 200:
            raise RuntimeError(f"{path}: HTTP {response.status}: {response.body.get('error', 'request failed')}")
        return response

