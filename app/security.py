"""web-player — authentication.

* The console and the player use **separate sessions**. A signed cookie
  carries either `admin` (console) or `player` (public player); a signed
  cookie for the player can never open the console.
* Passwords are stored as Argon2 hashes (decision D5 — no plain-text copy).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

log = logging.getLogger("web-player.security")

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or not password:
        return False
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001 - a broken hash must not lock the admin out
        log.exception("password verification failed")
        return False


# --------------------------------------------------------------------------
# media tokens
# --------------------------------------------------------------------------
def media_token(secret: str, media_id: int, user_id: int | None = None,
                ttl: int = 7 * 24 * 3600) -> str:
    """A signed, expiring token for one media item.

    The stream URLs carry it so the media endpoints can check *which* item is
    being requested without trusting a plain id in the path.
    """
    expires = int(datetime.now(timezone.utc).timestamp()) + ttl
    payload = f"{media_id}:{user_id or 0}:{expires}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_media_token(secret: str, token: str) -> tuple[int, int | None] | None:
    """Return (media_id, user_id) for a valid token, else None."""
    try:
        media_s, user_s, exp_s, sig = token.split(":")
    except (ValueError, AttributeError):
        return None
    payload = f"{media_s}:{user_s}:{exp_s}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        if int(exp_s) < datetime.now(timezone.utc).timestamp():
            return None
        return int(media_s), (int(user_s) or None)
    except ValueError:
        return None


def new_password(length: int = 14) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def session_expiry(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
