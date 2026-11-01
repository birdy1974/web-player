"""web-player — configuration from the environment.

Every value has a default that works on a fresh Synology container, so the
image starts even with an empty .env (the administrator password is the one
exception: it is required, because a management console without a password
would be a hole in the LAN).
"""
from __future__ import annotations

import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _auto_secret() -> str:
    """A stable secret per installation, so sessions survive a restart."""
    import secrets

    path = DATA_DIR / "session.key"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path.read_text().strip() or secrets.token_urlsafe(32)
        secret = secrets.token_urlsafe(32)
        path.write_text(secret)
        path.chmod(0o600)
        return secret
    except OSError:
        return secrets.token_urlsafe(32)


DATA_DIR = Path(os.environ.get("WEB_PLAYER_DATA_DIR", "/config")).resolve()
MEDIA_ROOT = Path(os.environ.get("WEB_PLAYER_MEDIA_ROOT", "/media")).resolve()

ADMIN_USER = os.environ.get("WEB_PLAYER_ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("WEB_PLAYER_ADMIN_PASSWORD", "").strip()

DB_PATH = DATA_DIR / "web-player.db"
SESSION_SECRET = os.environ.get("WEB_PLAYER_SECRET") or _auto_secret()
COOKIE_SECURE = _bool("WEB_PLAYER_COOKIE_SECURE", False)
SESSION_HOURS = _int("WEB_PLAYER_SESSION_HOURS", 72)

BASE_URL = os.environ.get("WEB_PLAYER_BASE_URL", "").strip().rstrip("/")
PUBLIC_URL = os.environ.get("WEB_PLAYER_PUBLIC_URL", "").strip().rstrip("/")

TRANSCODE_MODE = os.environ.get("WEB_PLAYER_TRANSCODE", "auto").strip().lower()
HLS_SEGMENT = max(2, _int("WEB_PLAYER_HLS_SEGMENT", 4))
MAX_STREAMS = max(1, _int("WEB_PLAYER_MAX_STREAMS", 3))
HLS_DIR = DATA_DIR / "hls"
POSTER_DIR = DATA_DIR / "posters"
FFMPEG = os.environ.get("WEB_PLAYER_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("WEB_PLAYER_FFPROBE", "ffprobe")
YTDLP = os.environ.get("WEB_PLAYER_YTDLP", "yt-dlp")

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv",
    ".mpg", ".mpeg", ".m2ts", ".ts", ".mts", ".vob", ".3gp", ".ogv",
}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"}


def vaapi_device() -> str:
    return os.environ.get("WEB_PLAYER_VAAPI_DEVICE", "/dev/dri/renderD128")
