"""Fixed-upstream proxy for the prototype's login and rendering endpoints."""

from __future__ import annotations

import http.client
import json
import logging
import queue
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request

GET_PATHS = {"/", "/canvas/", "/audio/", "/static/preliminary_fingerprint.js",
             "/static/performance_monitor.js", "/audio/static/render.js"}
POST_PATHS = {"/login", "/session/acquire", "/session/heartbeat", "/session/release",
              "/user/fingerprint", "/user/audio_stability", "/user/canvas_stability",
              "/user/performance"}
CONFIG_PATH = re.compile(r"^/(?:canvas/get_string_config/[^/]+/\d+/\d+/\d+|"
                         r"audio/get_snippets_config/[^/]+/\d+/\d+/\d+/\d+/\d+/\d+/\d+)$")
UPLOAD_PATH = re.compile(r"^/(?:canvas/upload_img|audio/upload_audio)/[a-f0-9]{32}$")
COOKIE_NAME = "sbfp_relay_lab"
MAX_BODY = 12 * 1024 * 1024
SESSION_LOGGER = logging.getLogger("relay_emulation.sessions")


def log_login(status, payload):
    """Record only correlation fields, never credentials or session cookies."""
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        result = {}
    if not isinstance(result, dict):
        result = {}
    submitted = request.get_json(silent=True)
    submitted = submitted if isinstance(submitted, dict) else {}
    username = result.get("username") or submitted.get("username")
    username = username.strip() if isinstance(username, str) else ""
    if not (3 <= len(username) <= 20 and all(c.isalnum() or c == "_" for c in username)):
        username = None
    session_id = result.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
        session_id = None
    SESSION_LOGGER.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": "relayed_login",
        "username": username,
        "session_id": session_id,
        "http_status": status,
        # Password acceptance precedes the Canvas/Audio verification result.
        "verification": "pending" if status == 200 and result.get("status") == "ok" else "not_started",
    }, ensure_ascii=True))


class Upstream:
    def __init__(self, url, timeout):
        self.url = urlsplit(url)
        if (self.url.scheme not in {"http", "https"} or not self.url.hostname
                or self.url.username or self.url.password or self.url.query
                or self.url.fragment or self.url.path not in {"", "/"}):
            raise ValueError("Upstream must be a single http(s) origin, with no credentials or path")
        self.timeout = timeout
        self.pool = queue.Queue(maxsize=16)

    def send(self, method, path, body, headers):
        try:
            connection = self.pool.get_nowait()
        except queue.Empty:
            cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
            connection = cls(self.url.hostname, self.url.port, timeout=self.timeout)
        try:
            # Never automatically retry mutations or follow redirects.
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            status, response_headers = response.status, response.getheaders()
            result = response.read(MAX_BODY + 1)
            if len(result) > MAX_BODY:
                raise ValueError("Upstream response too large")
            if response.will_close:
                connection.close()
            else:
                try:
                    self.pool.put_nowait(connection)
                except queue.Full:
                    connection.close()
            return status, response_headers, result
        except Exception:
            connection.close()
            raise

    def close(self):
        while True:
            try:
                self.pool.get_nowait().close()
            except queue.Empty:
                return


def create_app(upstream_url, added_rtt_ms=0, timeout=30):
    if not 0 <= added_rtt_ms <= 5000:
        raise ValueError("Added upstream RTT must be between 0 and 5000 ms")
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    upstream = Upstream(upstream_url, timeout)
    sessions = {}
    sessions_lock = threading.Lock()
    app.extensions["relay_lab"] = {"upstream": upstream}

    @app.route("/", methods=["GET"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def proxy(path=""):
        path = "/" + path
        allowed = ((request.method == "GET" and (path in GET_PATHS or CONFIG_PATH.fullmatch(path)))
                   or (request.method == "POST" and (path in POST_PATHS or UPLOAD_PATH.fullmatch(path))))
        if not allowed:
            return jsonify(error="This path is outside the S-BFP relay experiment"), 404
        now = time.monotonic()
        sid = request.cookies.get(COOKIE_NAME)
        with sessions_lock:
            for key in list(sessions):
                if now - sessions[key]["last_used"] > 1800:
                    del sessions[key]
            if sid not in sessions:
                if path != "/" or request.method != "GET":
                    return jsonify(error="Open the relay login page before starting a test"), 401
                if len(sessions) >= 128:
                    return jsonify(error="Too many active lab browsers"), 503
                sid = secrets.token_urlsafe(32)
                sessions[sid] = {"cookies": {}, "last_used": now}
            state = sessions[sid]
            state["last_used"] = now
            cookie = "; ".join(f"{key}={value}" for key, value in state["cookies"].items())
        body = request.get_data()
        headers = {"Accept-Encoding": "identity", "Cache-Control": "no-cache"}
        for key in ("Content-Type", "Accept", "User-Agent", "Origin", "Referer"):
            if key in request.headers:
                headers[key] = request.headers[key]
        if cookie:
            headers["Cookie"] = cookie
        target = path
        if request.query_string:
            target += "?" + request.query_string.decode("ascii")
        if added_rtt_ms:
            time.sleep(added_rtt_ms / 2000)
        try:
            status, response_headers, payload = upstream.send(request.method, target, body, headers)
        except (OSError, ValueError, http.client.HTTPException):
            status, response_headers, payload = 502, [], b'{"error":"Upstream request failed"}'
        if added_rtt_ms:
            time.sleep(added_rtt_ms / 2000)
        content_type = "application/json"
        with sessions_lock:
            for key, value in response_headers:
                if key.lower() == "set-cookie":
                    parsed = SimpleCookie()
                    parsed.load(value)
                    for name, morsel in parsed.items():
                        if morsel["max-age"] == "0" or not morsel.value:
                            state["cookies"].pop(name, None)
                        else:
                            state["cookies"][name] = morsel.coded_value
                elif key.lower() == "content-type":
                    content_type = value
        # The prototype uses relative URLs. Unexpected redirects fail visibly.
        if 300 <= status < 400:
            status, payload, content_type = 502, b'{"error":"Unexpected upstream redirect"}', "application/json"
        if request.method == "POST" and path == "/login":
            log_login(status, payload)
        if path == "/" and status == 200 and content_type.startswith("text/html"):
            # Visual lab identification only: no scripts, timers or fetch wrappers.
            marker = ('<style>#toggleLink{display:none!important}</style>'
                      '<aside id="relay-lab-label" style="position:fixed;bottom:0;left:0;right:0;'
                      'z-index:2147483647;background:#172033;color:white;padding:8px;'
                      'text-align:center;font:13px system-ui">S-BFP relay experiment — '
                      'use an account enrolled directly on the defense server.</aside>')
            payload = payload.replace(b"</body>", marker.encode("utf-8") + b"</body>", 1)
        response = Response(payload, status=status, content_type=content_type)
        response.headers["Cache-Control"] = "no-store"
        for name, value in response_headers:
            if name.lower() == "server-timing":
                response.headers.add("Server-Timing", value)
        # Keep upstream session cookies only in relay memory, never in the browser.
        response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="Lax",
                            secure=request.is_secure, max_age=1800)
        return response

    return app
