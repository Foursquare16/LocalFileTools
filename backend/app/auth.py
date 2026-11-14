"""Anonymous session cookie, the closed switch and the CSRF check."""

import hashlib
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from . import config

COOKIE = "session"
CSRF_HEADER = "x-lft"  # frontend sends `X-LFT: 1` on every request
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")  # secrets.token_urlsafe(32)


def well_formed(token: str | None) -> bool:
    return bool(token) and _TOKEN.fullmatch(token) is not None


def session(request: Request) -> str:
    """This browser's session: the SHA-256 of its token, which security_headers made sure exists. Jobs store only
    the hash."""
    return hashlib.sha256(request.state.session_token.encode()).hexdigest()


def closed_flag():
    """While this file exists the service is closed: `python -m app.cli close` / `open`."""
    return config.STATE_DIR / "closed"


def require_open() -> None:
    if closed_flag().exists():
        raise HTTPException(503, "closed")


def check_csrf(request: Request) -> None:
    """Mutating requests need an Origin matching Host and the custom header (on top of SameSite=Strict)."""
    if request.method in ("GET", "HEAD"):
        return
    origin_host = urlsplit(request.headers.get("origin", "")).netloc
    if not origin_host or origin_host != request.headers.get("host") or request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "Cross-site request rejected.")
