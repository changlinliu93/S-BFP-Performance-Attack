import os, json, threading, tempfile, time
from werkzeug.security import generate_password_hash, check_password_hash
from copy import deepcopy

from config import DATA_DIR as CONFIG_DATA_DIR
from performance_monitor import exclude_storage

AUDIO_BASELINE_KEY = "audio_baseline"
CANVAS_BASELINE_KEY = "canvas_baseline"

DATA_DIR = str(CONFIG_DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
USERS_DIR = os.path.join(DATA_DIR, "users")
os.makedirs(USERS_DIR, exist_ok=True)
LEGACY_USERS_FILE = os.path.join(DATA_DIR, "users.json")

_users_lock = threading.Lock()
_init_lock = threading.Lock()
_storage_initialized = False


def _ensure_storage_initialized():
    """Migrate existing records into username folders without overwriting data."""
    global _storage_initialized
    if _storage_initialized:
        return
    with _init_lock:
        if _storage_initialized:
            return
        if os.path.exists(LEGACY_USERS_FILE):
            with open(LEGACY_USERS_FILE, "r", encoding="utf-8") as f:
                legacy_data = json.load(f)
            for entry in legacy_data:
                path = _user_path(entry["username"])
                flat_path = os.path.join(USERS_DIR, f'{entry["username"]}.json')
                if not os.path.exists(path) and not os.path.exists(flat_path):
                    _write_user_atomic(path, entry)
        for name in os.listdir(USERS_DIR):
            source = os.path.join(USERS_DIR, name)
            if name.endswith(".json") and os.path.isfile(source):
                target = _user_path(name[:-5])
                if os.path.exists(target):
                    raise FileExistsError(
                        f"Refusing to overwrite user record: {target}"
                    )
                _migrate_user_file(source, target)
        _storage_initialized = True


@exclude_storage
def _migrate_user_file(source, target):
    _create_user_directories(target)
    os.rename(source, target)


def _user_path(username):
    """Resolve one username component, never an arbitrary filesystem path."""
    if not username or not all(c.isalnum() or c == "_" for c in username):
        raise ValueError("Invalid username")
    return os.path.join(USERS_DIR, username, f"{username}.json")


def _create_user_directories(record_path):
    directory = os.path.dirname(record_path)
    for modality in ("audio", "canvas"):
        os.makedirs(os.path.join(directory, modality), exist_ok=True)


@exclude_storage
def _write_user_atomic(path, user_data):
    """Atomically write a single user file."""
    _create_user_directories(path)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix="user_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _load_user_file(path):
    """Load a single user file and ensure a dict response."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _list_user_entries():
    try:
        return [
            name
            for name in os.listdir(USERS_DIR)
            if all(c.isalnum() or c == "_" for c in name)
            and os.path.isfile(_user_path(name))
        ]
    except FileNotFoundError:
        return []


def _resolve_username_path(username):
    """Find the file path and canonical username for the given username."""
    target = (username or "").strip()
    if not target:
        return None, None
    lower = target.lower()
    for entry in _list_user_entries():
        if entry.lower() == lower:
            return _user_path(entry), entry
    return None, None


def _update_user_record(username, mutator):
    """Internal helper to mutate a specific user and persist the change."""
    _ensure_storage_initialized()
    with _users_lock:
        path, canonical = _resolve_username_path(username)
        if not path:
            return False, "User not found"
        user = _load_user_file(path) or {}
        if not user.get("username"):
            user["username"] = canonical
        try:
            mutator(user)
        except Exception as exc:  # noqa: BLE001
            return False, f"Failed to update user data: {exc}"
        try:
            _write_user_atomic(path, user)
        except Exception:
            return False, "Server write failed"
        return True, "ok"


# ---------------------------
# User operations
# ---------------------------


def register_user(username, password, *, auto_generated=False, consent=None):
    """
    Register a user and return (True, username) or (False, error_message).
    If auto_generated is True, the supplied password is assumed to already satisfy
    the desired complexity requirements.
    """
    _ensure_storage_initialized()
    username = username.strip()
    password = (password or "").strip()
    if not username:
        return False, "Username is required"
    if not (3 <= len(username) <= 20) or not all(
        c.isalnum() or c == "_" for c in username
    ):
        return (
            False,
            "Invalid username format (letters, digits, underscores; length 3-20)",
        )
    if not password:
        return False, "Password must be provided"
    if not auto_generated and len(password) < 6:
        return False, "Password must be at least 6 characters long"

    with _users_lock:
        path, _ = _resolve_username_path(username)
        if path:
            return False, "Username already exists"

        pwd_hash = generate_password_hash(password)
        user_payload = {
            "username": username,
            "password_hash": pwd_hash,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if consent:
            user_payload["consent"] = deepcopy(consent)
        target_path = _user_path(username)
        try:
            _write_user_atomic(target_path, user_payload)
        except Exception:
            return False, "Server write failed"

    return True, username


def authenticate_user(username, password):
    """
    Validate login credentials and return (True, username) or (False, error_message).
    """
    _ensure_storage_initialized()
    username = username.strip()
    if not username or not password:
        return False, "Username or password cannot be empty"

    with _users_lock:
        path, canonical = _resolve_username_path(username)
        if not path:
            return False, "User not found"
        user = _load_user_file(path)
        if not user:
            return False, "User not found"
        pwd_hash = user.get("password_hash")
        if not pwd_hash or not check_password_hash(pwd_hash, password):
            return False, "Incorrect password"
        return True, user.get("username", canonical)


def list_users():
    """Debug helper: return a list of usernames."""
    _ensure_storage_initialized()
    with _users_lock:
        usernames = []
        for entry in _list_user_entries():
            path = _user_path(entry)
            data = _load_user_file(path)
            usernames.append(data.get("username") or entry)
        return usernames


def get_user_record(username):
    """Get the full user record (deep copy to avoid external mutation)."""
    _ensure_storage_initialized()
    username = (username or "").strip()
    if not username:
        return None
    with _users_lock:
        path, _ = _resolve_username_path(username)
        if not path:
            return None
        record = _load_user_file(path)
        if not record:
            return None
        return deepcopy(record)
    return None


@exclude_storage
def store_raw_upload(username, modality, challenge_id, raw):
    """Save original bytes; retries may reuse a filename only for identical data."""
    _ensure_storage_initialized()
    path, _ = _resolve_username_path(username)
    if path is None:
        raise FileNotFoundError("User not found")
    extensions = {"audio": ".pcm", "canvas": ".png"}
    if (
        modality not in extensions
        or len(challenge_id) != 32
        or any(c not in "0123456789abcdef" for c in challenge_id)
    ):
        raise ValueError("Invalid upload destination")
    _create_user_directories(path)
    target = os.path.join(
        os.path.dirname(path), modality, challenge_id + extensions[modality]
    )
    try:
        handle = open(target, "xb")
    except FileExistsError:
        with open(target, "rb") as existing:
            if existing.read() == raw:
                return
        raise FileExistsError("Different content already stored for this challenge")
    try:
        with handle:
            handle.write(raw)
    except OSError:
        os.remove(target)
        raise


@exclude_storage
def store_performance_report(username, session_id, report):
    """Atomically save one report per attempt and accept identical retries."""
    if (
        not isinstance(session_id, str)
        or len(session_id) != 32
        or any(c not in "0123456789abcdef" for c in session_id)
    ):
        raise ValueError("Invalid performance session ID")
    content = json.dumps(
        report, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ) + "\n"
    _ensure_storage_initialized()
    with _users_lock:
        user_path, _ = _resolve_username_path(username)
        if user_path is None:
            raise FileNotFoundError("User not found")
        directory = os.path.join(os.path.dirname(user_path), "performance")
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, session_id + ".json")
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as existing:
                if existing.read() == content:
                    return
            raise FileExistsError("A different report already exists for this session")
        fd, temporary = tempfile.mkstemp(dir=directory, prefix="report_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def store_user_fingerprint(username, fingerprint_payload):
    """
    Save the user's static fingerprint information.
    """
    username = (username or "").strip()
    if not username:
        return False, "Username is required"
    if not isinstance(fingerprint_payload, dict):
        return False, "Invalid fingerprint data format"

    return _update_user_record(
        username,
        lambda user: user.__setitem__("fingerprint", fingerprint_payload),
    )


def store_system_timing(username, timing_record):
    """
    Save the total system timing (from fingerprint capture start to all tests complete).
    """
    username = (username or "").strip()
    if not username:
        return False, "Username is required"
    if not isinstance(timing_record, dict):
        return False, "Invalid timing data format"

    return _update_user_record(
        username,
        lambda user: user.__setitem__("system_timing", timing_record),
    )


@exclude_storage
def append_session_event(username, event):
    """Append one completed attempt to the user's JSON Lines event log."""
    _ensure_storage_initialized()
    with _users_lock:
        path, _ = _resolve_username_path(username)
        if path is None:
            raise FileNotFoundError("User not found")
        with open(os.path.join(os.path.dirname(path), "events.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def append_audio_stability(username, stability_record):
    """Record audio automated test stability results."""
    username = (username or "").strip()
    if not username:
        return False, "Username is required"
    if not isinstance(stability_record, dict):
        return False, "Invalid result data format"

    def mutator(user):
        history = user.setdefault("audio_stability", [])
        history.append(stability_record)

    return _update_user_record(username, mutator)


def set_audio_baseline(username, baseline_hash, *, overwrite=False, seed=None):
    """Set or update the user's audio baseline hash."""
    username = (username or "").strip()
    baseline_hash = (baseline_hash or "").strip()
    if not username:
        return False, "Username is required"
    if not baseline_hash:
        return False, "Baseline hash is required"

    def mutator(user):
        if overwrite or not user.get(AUDIO_BASELINE_KEY):
            user[AUDIO_BASELINE_KEY] = baseline_hash
            user["audio_baseline_seed"] = seed

    return _update_user_record(username, mutator)


def append_canvas_stability(username, stability_record):
    """Record Canvas automated test stability results."""
    username = (username or "").strip()
    if not username:
        return False, "Username is required"
    if not isinstance(stability_record, dict):
        return False, "Invalid result data format"

    def mutator(user):
        history = user.setdefault("canvas_stability", [])
        history.append(stability_record)

    return _update_user_record(username, mutator)


def set_canvas_baseline(username, seed, baseline_hash, *, overwrite=False):
    """Set or update the user's Canvas baseline hash (segmented by seed)."""
    username = (username or "").strip()
    seed = (seed or "__default__").strip() or "__default__"
    baseline_hash = (baseline_hash or "").strip()
    if not username:
        return False, "Username is required"
    if not baseline_hash:
        return False, "Baseline hash is required"

    def mutator(user):
        baselines = user.get(CANVAS_BASELINE_KEY)
        if isinstance(baselines, dict):
            target = baselines
        elif baselines:
            target = {"__default__": baselines}
        else:
            target = {}

        if overwrite or target.get(seed) in (None, ""):
            target[seed] = baseline_hash
            user.setdefault("canvas_baseline_seed", seed)

        user[CANVAS_BASELINE_KEY] = target

    return _update_user_record(username, mutator)
