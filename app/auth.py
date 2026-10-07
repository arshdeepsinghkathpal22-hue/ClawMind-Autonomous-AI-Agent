"""Optional password login. Off by default for local-only use.

Passwords are stored as PBKDF2-SHA256 hashes in .env (AUTH_PASSWORD_HASH).
Login sessions are random tokens kept in memory, so a restart logs everyone out.
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 600_000
SESSION_SECONDS = 12 * 3600
COOKIE_NAME = "clawmind_session"

_sessions = {}  # sha256(token) -> expiry timestamp
_lock = threading.Lock()


def hash_password(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations)
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def _key(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session():
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[_key(token)] = time.time() + SESSION_SECONDS
    return token


def check_session(token):
    if not token:
        return False
    now = time.time()
    with _lock:
        expiry = _sessions.get(_key(token))
        if not expiry:
            return False
        if expiry < now:
            del _sessions[_key(token)]
            return False
        return True


def end_session(token):
    if token:
        with _lock:
            _sessions.pop(_key(token), None)


def clear_sessions():
    with _lock:
        _sessions.clear()


class RateLimiter:
    """Simple sliding-window limiter kept in memory, keyed by client IP."""

    def __init__(self, limit, window_seconds=60):
        self.limit = limit
        self.window = window_seconds
        self.hits = {}
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self.lock:
            recent = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            if len(self.hits) > 10_000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            return True

    def reset(self):
        with self.lock:
            self.hits.clear()
