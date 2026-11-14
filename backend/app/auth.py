"""Owner login, server-side sessions, login rate limit and the CSRF check."""

import hashlib
import ipaddress
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
# One password check at a time: each takes 64 MiB, and it caps guessing speed whatever address the guesses come from.
_verify_lock = threading.Lock()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _from_proxy(peer: str) -> bool:
    if not config.PROXY_IPS:
        return True  # nothing but cloudflared can reach the web port (deploy cases A and B)
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n.strip(), strict=False) for n in config.PROXY_IPS.split(","))


def client_ip(request: Request) -> str:
    """The address the login rate limit counts by. CF-Connecting-IP is believed only when the proxy sent it."""
    peer = request.client.host if request.client else ""
    ip = (_from_proxy(peer) and request.headers.get("cf-connecting-ip")) or peer
    try:
        # One IPv6 customer holds a whole /64, so count that as one client.
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ipaddress.ip_address(ip).version == 6 else ip
    except ValueError:
        return ip


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
        with _verify_lock:
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


def create_invite(name: str, hours: float) -> str:
    """A guest link's token: it logs in whoever holds it, without a password, until it expires or is revoked."""
    token, now = secrets.token_urlsafe(32), time.time()
    with db.state() as c:
        c.execute("DELETE FROM invites WHERE expires <= ?", (now,))
        c.execute("INSERT INTO invites VALUES (?, ?, ?)", (_token_hash(token), name, now + hours * 3600))
    return token


def invite_seconds(token: str | None) -> int | None:
    """Seconds until this guest token stops working; None if it is not a live one."""
    if not token:
        return None
    now = time.time()
    with db.state() as c:
        row = c.execute(
            "SELECT expires FROM invites WHERE token_hash = ? AND expires > ?", (_token_hash(token), now)
        ).fetchone()
    return int(row["expires"] - now) if row else None


def has_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    with db.state() as c:
        row = c.execute(
            "SELECT 1 FROM sessions WHERE token_hash = ? AND expires > ?", (_token_hash(token), time.time())
        ).fetchone()
    return row is not None or invite_seconds(token) is not None


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
