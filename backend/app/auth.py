"""Owner login, server-side sessions, login rate limit and the CSRF check."""

import hashlib
import secrets
import threading
import time
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request

from . import config, db

COOKIE = "session"
CSRF_HEADER = "x-lft"  # frontend sends `X-LFT: 1` on every request

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))  # verified when there is no owner, so timing is the same

# ip -> (attempts, time the count resets). shortcut: in-memory and per process; fine for one uvicorn worker.
_failures: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # Trustworthy because only cloudflared can reach the web port (plan §4).
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


def login(ip: str, username: str, password: str) -> str:
    """Check the owner's credentials and return a new session token. 5 failures per IP lock it out for 15 minutes."""
    now = time.time()
    with _lock:
        if len(_failures) > 10_000:
            for key in [k for k, (_, reset) in _failures.items() if reset <= now]:
                del _failures[key]
        count, reset = _failures.get(ip, (0, 0.0))
        if now >= reset:
            count = 0
        if count >= config.LOGIN_MAX_FAILURES:
            raise HTTPException(429, "Too many failed logins. Try again in 15 minutes.")
        # Count the attempt before verifying, so parallel guesses can't slip past the limit.
        _failures[ip] = (count + 1, now + config.LOGIN_LOCKOUT_SECONDS)

    with db.state() as c:
        owner = c.execute("SELECT username, password_hash FROM owner WHERE id = 1").fetchone()
    try:
        ok = _hasher.verify(owner["password_hash"] if owner else _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        ok = False
    if not (ok and owner and secrets.compare_digest(owner["username"].encode(), username.encode())):
        raise HTTPException(401, "Wrong username or password.")

    with _lock:
        _failures.pop(ip, None)
    token = secrets.token_urlsafe(32)
    with db.state() as c:
        c.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        c.execute("INSERT INTO sessions VALUES (?, ?)", (_token_hash(token), now + config.SESSION_SECONDS))
    return token


def logout(token: str | None) -> None:
    if token:
        with db.state() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def has_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    with db.state() as c:
        row = c.execute(
            "SELECT 1 FROM sessions WHERE token_hash = ? AND expires > ?", (_token_hash(token), time.time())
        ).fetchone()
    return row is not None


def require_session(request: Request) -> None:
    if not has_session(request):
        raise HTTPException(401, "Not logged in.")


def check_csrf(request: Request) -> None:
    """Mutating requests need an Origin matching Host and the custom header (on top of SameSite=Strict)."""
    if request.method in ("GET", "HEAD"):
        return
    origin_host = urlsplit(request.headers.get("origin", "")).netloc
    if not origin_host or origin_host != request.headers.get("host") or request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Cross-site request rejected.")
