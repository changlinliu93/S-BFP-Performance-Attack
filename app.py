import secrets
import string
import threading
import time
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, render_template, request, session
from itsdangerous import BadSignature, URLSafeSerializer
from performance_monitor import install_performance_monitor, measure
from Audio.routes import audio_bp, db as audio_challenges
from Canvas.routes import canvas_bp, db as canvas_challenges
from config import (
    DEBUG,
    FLASK_SESSION_SECRET,
    HOST,
    MAX_CONTENT_LENGTH,
    OPEN_BROWSER,
    PORT,
    STORE_CLIENT_METADATA,
    STORE_RAW_FINGERPRINT,
)
from User_Manager.user_manager import (
    AUDIO_BASELINE_KEY,
    CANVAS_BASELINE_KEY,
    append_session_event,
    register_user,
    authenticate_user,
    store_user_fingerprint,
    store_performance_report,
    get_user_record,
    append_audio_stability,
    set_audio_baseline,
    append_canvas_stability,
    set_canvas_baseline,
)

app = Flask(__name__)
install_performance_monitor(app)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    SECRET_KEY=FLASK_SESSION_SECRET,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
_performance_signer = URLSafeSerializer(app.secret_key, salt="sbfp-performance-v1")
app.register_blueprint(audio_bp, url_prefix="/audio")
app.register_blueprint(canvas_bp, url_prefix="/canvas")

SESSION_TIMEOUT_SECONDS = 300
_session_state = {
    "owner": None,
    "token": None,
    "acquired_at": None,
    "last_heartbeat": None,
    "session_id": None,
    "event_type": None,
    "seed": None,
    "results": {},
}
_session_lock = threading.Lock()
_session_events = {}


def _clear_session_locked():
    """Reset the exclusive session state (call under _session_lock)."""
    for key in _session_state:
        _session_state[key] = {} if key == "results" else None


def _is_session_stale(now_ts):
    last = _session_state["last_heartbeat"]
    if _session_state["owner"] is None or last is None:
        return False
    return (now_ts - last) > SESSION_TIMEOUT_SECONDS


def _expire_session_if_needed(now_ts=None):
    now_ts = now_ts or time.time()
    if _is_session_stale(now_ts):
        _finish_event(_session_state, error="Session timed out")
        _clear_session_locked()


def _validate_session_owner(username: str):
    username = (username or "").strip()
    pending_user = (session.get("pending_user") or "").strip()
    if not pending_user or pending_user.lower() != username.lower():
        return False, "Authentication is required for this user."
    with _session_lock:
        _expire_session_if_needed()
        owner = _session_state["owner"]
        if not owner:
            return False, "The testing system is currently idle."
        if owner.lower() != username.lower() or _session_state[
            "session_id"
        ] != session.get("session_id"):
            return (
                False,
                "Another user is currently running tests. Please try again later.",
            )
    return True, None


def _baseline(user, modality):
    key = AUDIO_BASELINE_KEY if modality == "audio" else CANVAS_BASELINE_KEY
    value = user.get(key)
    seed = user.get(f"{modality}_baseline_seed")
    if not seed:
        seed = next(
            (
                run.get("seed")
                for run in user.get(f"{modality}_stability", [])
                if run.get("seed")
            ),
            None,
        )
    if isinstance(value, dict):
        seed = seed or next((key for key in value if key != "__default__"), None)
        value = value.get(seed) or value.get("__default__")
    return value or None, seed


def _finish_event(state, error=None, result=None):
    if state["session_id"] in _session_events:
        return _session_events[state["session_id"]]
    user = get_user_record(state["owner"]) or {}
    modalities = {}
    for modality in ("audio", "canvas"):
        baseline, baseline_seed = _baseline(user, modality)
        report = state.get("results", {}).get(modality, {})
        seed = report.get("seed") or state.get("seed")
        hashes = report.get("hashes", [])
        modalities[modality] = {
            "seed": seed,
            "baseline_seed": baseline_seed,
            "seed_matches_baseline": (
                seed == baseline_seed if seed and baseline_seed else None
            ),
            "fingerprint": hashes[0] if hashes else None,
            "fingerprints": hashes,
            "baseline_fingerprint": baseline,
            "fingerprint_matches_baseline": (
                all(h == baseline for h in hashes) if hashes and baseline else None
            ),
        }
    if result is None:
        if not error and any(not item["fingerprints"] for item in modalities.values()):
            error = "Audio and Canvas verification did not both complete"
        if not error and any(
            not item["baseline_fingerprint"] for item in modalities.values()
        ):
            error = "Missing enrolled fingerprint baseline"
        result = (
            "error"
            if error
            else (
                "success"
                if all(
                    item["fingerprint_matches_baseline"] for item in modalities.values()
                )
                else "failure"
            )
        )
    if result == "failure" and not error:
        error = "Audio or Canvas fingerprint does not match the baseline"
    event = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "session_id": state["session_id"],
        "event_type": state["event_type"],
        "seed": state.get("seed"),
        **modalities,
        "result": result,
        "error": error,
    }
    append_session_event(state["owner"], event)
    _session_events[state["session_id"]] = event
    return event


def _begin_attempt(username, event_type, seed):
    with _session_lock:
        if _session_state["owner"] and _session_state["session_id"] == session.get(
            "session_id"
        ):
            _finish_event(_session_state, error="Session replaced by a new attempt")
            _clear_session_locked()
        elif session.get("pending_user"):
            _finish_event(
                {**session, "owner": session["pending_user"]},
                error="Session replaced by a new attempt",
            )
    session.clear()
    session.update(
        pending_user=username,
        session_id=secrets.token_hex(16),
        event_type=event_type,
        seed=seed,
    )


def _performance_token(username):
    """Authorize a report for this specific user and attempt, including retries."""
    return _performance_signer.dumps(
        {"username": username, "session_id": session["session_id"]}
    )


def generate_secure_password(length: int = 16) -> str:
    alphabet = (
        string.ascii_lowercase
        + string.ascii_uppercase
        + string.digits
        + "!@#$%^&*()-_=+[]{}"
    )
    specials = set("!@#$%^&*()-_=+[]{}")
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in specials for c in password)
        ):
            return password


def open_browser():
    time.sleep(1.5)
    webbrowser.open_new(f"http://127.0.0.1:{PORT}/")


def _public_fingerprint_details(details):

    if not isinstance(details, dict):
        return None
    allowed = (
        "screenWidth",
        "screenHeight",
        "screenColorDepth",
        "screenPixelDepth",
        "language",
        "platform",
        "hardwareConcurrency",
    )
    return {key: details.get(key) for key in allowed if key in details}


@app.route("/")
def index():
    return render_template("base.html")


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if data.get("consent") is not True:
        return jsonify(status="error", error="Informed consent is required"), 400
    generated_password = generate_secure_password()
    consent = {
        "accepted": True,
        "version": "2026-08-22",
        "recorded_at": datetime.now().astimezone().isoformat(),
    }
    ok, msg = register_user(
        username,
        generated_password,
        auto_generated=True,
        consent=consent,
    )
    if ok:
        _begin_attempt(msg, "registration", data.get("seed"))
        return jsonify(
            status="ok",
            username=msg,
            password=generated_password,
            session_id=session["session_id"],
            performance_token=_performance_token(msg),
        )
    else:
        return jsonify(status="error", error=msg), 400


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    ok, msg = authenticate_user(username, password)
    if ok:
        _begin_attempt(msg, "login", data.get("seed"))
        return jsonify(
            status="ok",
            username=msg,
            session_id=session["session_id"],
            performance_token=_performance_token(msg),
            verification="pending",
        )
    else:
        user = get_user_record(username)
        _begin_attempt(
            user["username"] if user else username, "login", data.get("seed")
        )
        if user:
            _finish_event(
                {
                    "owner": user["username"],
                    "session_id": session["session_id"],
                    "event_type": "login",
                    "seed": data.get("seed"),
                },
                error=msg,
                result="failure",
            )
        session.pop("pending_user", None)
        return jsonify(
            status="error",
            error=msg,
            session_id=session["session_id"],
            performance_token=_performance_token(user["username"]) if user else None,
        ), 401


@app.route("/session/acquire", methods=["POST"])
def acquire_session():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify(status="error", error="Username is required"), 400
    pending_user = (session.get("pending_user") or "").strip()
    if not pending_user or pending_user.lower() != username.lower():
        return jsonify(status="error", error="Authentication is required"), 401

    now_ts = time.time()
    with _session_lock:
        _expire_session_if_needed(now_ts)
        if session.get("session_id") in _session_events:
            session.pop("pending_user", None)
            return (
                jsonify(
                    status="error", error="Session has ended. Please sign in again."
                ),
                401,
            )
        owner = _session_state["owner"]
        if owner and _session_state["session_id"] != session.get("session_id"):
            _finish_event(
                {**session, "owner": pending_user}, error="Testing system is busy"
            )
            session.pop("pending_user", None)
            acquired_at = _session_state["acquired_at"]
            started_at = (
                datetime.fromtimestamp(acquired_at).isoformat() if acquired_at else None
            )
            return (
                jsonify(
                    status="busy",
                    owner=owner,
                    startedAt=started_at,
                    timeout=SESSION_TIMEOUT_SECONDS,
                ),
                409,
            )

        if owner and owner.lower() == username.lower():
            _session_state["last_heartbeat"] = now_ts
            return jsonify(
                status="ok",
                token=_session_state["token"],
                session_id=_session_state["session_id"],
                timeout=SESSION_TIMEOUT_SECONDS,
            )

        token = secrets.token_hex(32)
        _session_state.update(
            session_id=session["session_id"],
            event_type=session["event_type"],
            seed=session.get("seed"),
            results={},
        )
        _session_state["owner"] = pending_user
        _session_state["token"] = token
        _session_state["acquired_at"] = now_ts
        _session_state["last_heartbeat"] = now_ts
        return jsonify(
            status="ok",
            token=token,
            timeout=SESSION_TIMEOUT_SECONDS,
            session_id=session["session_id"],
        )


@app.route("/session/heartbeat", methods=["POST"])
def heartbeat_session():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    token = (data.get("token") or "").strip()
    if not username or not token:
        return jsonify(status="error", error="Username and token are required"), 400

    now_ts = time.time()
    with _session_lock:
        _expire_session_if_needed(now_ts)
        owner = _session_state["owner"]
        if (
            owner
            and owner.lower() == username.lower()
            and _session_state["token"] == token
            and _session_state["session_id"] == session.get("session_id")
        ):
            _session_state["last_heartbeat"] = now_ts
            return jsonify(status="ok")

    return jsonify(status="error", error="Session is no longer active"), 409


@app.route("/session/release", methods=["POST"])
def release_session():
    with measure("body_read"):
        data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    token = (data.get("token") or "").strip()
    if not username or not token:
        return jsonify(status="error", error="Username and token are required"), 400

    with _session_lock:
        _expire_session_if_needed()
        owner = _session_state["owner"]
        if (
            owner
            and owner.lower() == username.lower()
            and _session_state["token"] == token
            and _session_state["session_id"] == session.get("session_id")
        ):
            reason = data.get("reason", "manual")
            error = data.get("error") or (
                None if reason == "tests_complete" else f"Session ended: {reason}"
            )
            with measure("verify"):
                event = _finish_event(_session_state, error=error)
            session.pop("pending_user", None)
            session.pop("authenticated_user", None)
            if event["result"] == "success":
                session["authenticated_user"] = owner
            _clear_session_locked()
            action = "Login" if event["event_type"] == "login" else "Registration"
            message = (
                f"{action} successful."
                if event["result"] == "success"
                else f"{action} failed: {event['error']}"
            )
            return jsonify(
                status="ok",
                result=event["result"],
                message=message,
                session_id=event["session_id"],
            )

    return jsonify(status="error", error="Session release rejected"), 409


@app.route("/user/performance", methods=["POST"])
def save_performance():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(status="error", error="JSON object is required"), 400
    token = data.get("token")
    report = data.get("report")
    if not isinstance(token, str) or not token:
        return jsonify(status="error", error="Performance authorization is required"), 403
    try:
        authorization = _performance_signer.loads(token)
    except BadSignature:
        return jsonify(status="error", error="Invalid performance authorization"), 403
    if not isinstance(report, dict):
        return jsonify(status="error", error="Performance report is required"), 400
    if report.get("session_id") != authorization["session_id"]:
        return jsonify(status="error", error="Performance session does not match"), 403
    if (
        report.get("schema_version") != 2
        or report.get("outcome") not in ("success", "failure", "error")
        or not isinstance(report.get("shared"), dict)
        or not isinstance(report.get("modalities"), dict)
        or report.get("e2e_adjusted_ms") is None
    ):
        return jsonify(status="error", error="Invalid performance report format"), 400
    try:
        store_performance_report(
            authorization["username"], authorization["session_id"], report
        )
    except FileExistsError:
        return jsonify(status="error", error="A different report is already saved"), 409
    except FileNotFoundError:
        return jsonify(status="error", error="User not found"), 404
    except ValueError:
        return jsonify(status="error", error="Invalid performance report data"), 400
    except OSError:
        return jsonify(status="error", error="Performance save failed. Please retry."), 503
    return jsonify(
        status="ok",
        session_id=authorization["session_id"],
        path=f"users/{authorization['username']}/performance/{authorization['session_id']}.json",
    )


@app.route("/user/fingerprint", methods=["POST"])
def capture_fingerprint():
    with measure("body_read"):
        data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    ok_session, session_msg = _validate_session_owner(username)
    if not ok_session:
        return jsonify(status="error", error=session_msg), 409
    _session_state["seed"] = data.get("fingerprintHash") or _session_state["seed"]
    fingerprint_details = data.get("fingerprint")
    captured_at = data.get("timestamp") or time.strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "captured_at": captured_at,
        "hash": data.get("fingerprintHash"),
        "fingerprint_string": (
            data.get("fingerprintString") if STORE_RAW_FINGERPRINT else None
        ),
        "details": (
            fingerprint_details
            if STORE_RAW_FINGERPRINT
            else _public_fingerprint_details(fingerprint_details)
        ),
    }
    if STORE_CLIENT_METADATA:
        payload["client_ip"] = request.remote_addr
        payload["user_agent"] = request.headers.get("User-Agent")

    ok, msg = store_user_fingerprint(username, payload)
    if ok:
        return jsonify(status="ok")
    return jsonify(status="error", error=msg), 400


def _record_stability(modality):
    with measure("body_read"):
        data = request.get_json(silent=True) or {}
    with measure("verify"):
        username = (data.get("username") or "").strip()
        ok, message = _validate_session_owner(username)
        if not ok:
            return jsonify(status="error", error=message), 409
        seed = (data.get("seed") or "").strip()
        runs = data.get("testRuns") or []
        challenges = audio_challenges if modality == "audio" else canvas_challenges
        config_key = "audioConfig" if modality == "audio" else "drawConfig"
        hash_key = "waveformHash" if modality == "audio" else "hash"
        hashes = []
        for run in runs:
            config = run.get(config_key) if isinstance(run, dict) else None
            challenge = (
                challenges.get(config.get("challenge_id"))
                if isinstance(config, dict)
                else None
            )
            if (
                not challenge
                or not challenge.get("hash")
                or challenge.get("username") != session.get("pending_user")
                or challenge.get("session_id") != session.get("session_id")
                or challenge["seed"] != seed
                or challenge["hash"] != run.get(hash_key)
            ):
                return (
                    jsonify(
                        status="error",
                        error="Results must reference this session's completed uploads",
                    ),
                    400,
                )
            hashes.append(challenge["hash"])
        if not hashes or not seed:
            return (
                jsonify(
                    status="error", error="Missing seed or completed rendering results"
                ),
                400,
            )

        user = get_user_record(username)
        baseline, baseline_seed = _baseline(user, modality)
        if not baseline and session["event_type"] == "registration":
            baseline = hashes[0]
            if modality == "audio":
                ok, message = set_audio_baseline(username, baseline, seed=seed)
            else:
                ok, message = set_canvas_baseline(username, seed, baseline)
            if not ok:
                return jsonify(status="error", error=message), 500
            baseline_seed = seed

        mismatches = [i + 1 for i, value in enumerate(hashes) if value != baseline]
    record = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "session_id": session["session_id"],
        "seed": seed,
        "seed_matches_baseline": seed == baseline_seed if baseline_seed else None,
        "all_stable": not mismatches,
        "unique_hashes": list(dict.fromkeys(hashes)),
        "runs": runs,
        "hashes": hashes,
        "mismatch_runs": mismatches,
        "total_runs": len(hashes),
    }
    if data.get("stabilityRate") is not None:
        record["stability_rate"] = data["stabilityRate"]
    if STORE_CLIENT_METADATA:
        record["client_ip"] = request.remote_addr
    if data.get("drawConfig") is not None:
        record["draw_config"] = data["drawConfig"]
    append = append_audio_stability if modality == "audio" else append_canvas_stability
    ok, message = append(username, record)
    if not ok:
        return jsonify(status="error", error=message), 500
    previous = _session_state["results"].get(modality, {})
    _session_state["results"][modality] = {
        **record,
        "hashes": previous.get("hashes", []) + hashes,
    }
    _session_state["seed"] = _session_state["seed"] or seed
    return jsonify(
        status="ok",
        session_id=session["session_id"],
        baselineHash=baseline,
        allStable=not mismatches,
        mismatchRuns=mismatches,
        totalRuns=len(hashes),
        alertMessage=f"{modality.title()} fingerprints "
        + (
            "matched the baseline." if not mismatches else "did not match the baseline."
        ),
    )


@app.route("/user/audio_stability", methods=["POST"])
def record_audio_stability():
    return _record_stability("audio")


@app.route("/user/canvas_stability", methods=["POST"])
def record_canvas_stability():
    return _record_stability("canvas")


if __name__ == "__main__":
    if OPEN_BROWSER:
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=HOST, debug=DEBUG, port=PORT, use_reloader=False)
