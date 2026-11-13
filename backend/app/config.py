"""Limits and paths from env (plan §2). Read as `config.X` at call time so tests can override."""

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


MB = 1024 * 1024

STATE_DIR = Path(os.environ.get("LFT_STATE_DIR", "/data/state"))  # web only
JOBS_DIR = Path(os.environ.get("LFT_JOBS_DIR", "/data/jobs"))  # web + worker
STATIC_DIR = Path(os.environ.get("LFT_STATIC_DIR", "/app/static"))  # built frontend

MAX_QUEUED = _int("LFT_MAX_QUEUED", 5)
MAX_UPLOAD_BYTES = _int("LFT_MAX_UPLOAD_MB", 50) * MB  # whole request body, per job
MAX_FILES = _int("LFT_MAX_FILES", 30)
MAX_IMAGE_MP = _int("LFT_MAX_IMAGE_MP", 60)  # megapixels per image
MAX_PDF_PAGES = _int("LFT_MAX_PDF_PAGES", 100)
JOB_TIMEOUT_SECONDS = _int("LFT_JOB_TIMEOUT_SECONDS", 300)
DISK_BUDGET_BYTES = _int("LFT_DISK_BUDGET_MB", 2048) * MB  # jobs volume
RETENTION_SECONDS = _int("LFT_RETENTION_SECONDS", 3600)
CLEANUP_INTERVAL_SECONDS = _int("LFT_CLEANUP_INTERVAL_SECONDS", 300)

# Optional "host:port" for plain-HTTP access on the LAN: only requests to this exact Host get a non-Secure cookie.
LAN_HOST = os.environ.get("LFT_LAN_HOST", "")

SESSION_SECONDS = 7 * 24 * 3600
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60
