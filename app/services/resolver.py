"""web-player — the YouTube resolver (decision D4).

`yt-dlp` turns a YouTube page URL into a **real media URL** that the NAS can
serve with HTTP Range support. That is what makes a YouTube link an ordinary
item: our player, our seek bar, and — unlike an embedded player — it can be
written into an Enigma2 bouquet.

The resolved URL expires, so it is cached with a timestamp and re-resolved on
demand. A failure is reported in the console and the item falls back to the
embedded player instead of disappearing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import YTDLP, YTDLP_FORMAT
from ..models import Media

log = logging.getLogger("web-player.resolver")

RESOLVE_TIMEOUT = 180
# A resolved YouTube URL is good for a few hours; re-resolve a bit earlier.
FRESH_FOR = timedelta(hours=4)


async def resolve(db: AsyncSession, item: Media, *, force: bool = False) -> dict:
    """Resolve (or re-resolve) a YouTube item. Never raises."""
    if item.source_type != "youtube" or not item.url:
        return {"state": "skipped"}
    if not force and _is_fresh(item):
        return {"state": "resolved", "url": item.resolved_url,
                "at": item.resolved_at.isoformat() if item.resolved_at else None}

    item.resolved_state = "resolving"
    item.resolved_error = None
    await db.commit()

    cmd = [
        YTDLP, "--no-playlist", "--no-warnings", "--dump-json",
        "--no-check-certificate",
        "-f", YTDLP_FORMAT,
        item.url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=RESOLVE_TIMEOUT)
    except asyncio.TimeoutError:
        item.resolved_state = "failed"
        item.resolved_error = f"yt-dlp timed out after {RESOLVE_TIMEOUT}s"
        await db.commit()
        return {"state": "failed", "error": item.resolved_error}
    except OSError as exc:
        item.resolved_state = "failed"
        item.resolved_error = f"yt-dlp could not be started: {exc}"
        await db.commit()
        return {"state": "failed", "error": item.resolved_error}

    if proc.returncode != 0 or not out:
        message = (err.decode("utf-8", "replace").strip().splitlines() or ["unknown error"])[-1]
        item.resolved_state = "failed"
        item.resolved_error = message[:500]
        await db.commit()
        log.warning("yt-dlp failed for %s: %s", item.url, message)
        return {"state": "failed", "error": item.resolved_error}

    try:
        info = json.loads(out.decode("utf-8", "replace").splitlines()[0])
    except (ValueError, IndexError):
        item.resolved_state = "failed"
        item.resolved_error = "yt-dlp returned something we could not read"
        await db.commit()
        return {"state": "failed", "error": item.resolved_error}

    url, fmt = _pick_format(info)
    if not url:
        item.resolved_state = "failed"
        item.resolved_error = "yt-dlp found no playable stream"
        await db.commit()
        return {"state": "failed", "error": item.resolved_error}

    item.resolved_url = url
    item.resolved_at = datetime.now(timezone.utc)
    item.resolved_state = "resolved"
    item.resolved_error = None
    item.resolved_meta = json.dumps({
        "title": info.get("title"),
        "duration": info.get("duration"),
        "ext": fmt.get("ext") or info.get("ext"),
        "quality": (fmt.get("height") and f"{fmt.get('height')}p") or info.get("format_note"),
        "filesize": fmt.get("filesize") or fmt.get("filesize_approx")
        or info.get("filesize") or info.get("filesize_approx"),
        "format_id": fmt.get("format_id") or info.get("format_id"),
        "audio": fmt.get("acodec"),
        "video": fmt.get("vcodec"),
        "separate_streams": bool(info.get("requested_formats")),
    })
    if info.get("title") and not item.title:
        item.title = info["title"][:300]
    if info.get("duration") and not item.duration_s:
        item.duration_s = float(info["duration"])
    item.delivery = "direct"
    await db.commit()
    return {"state": "resolved", "url": url, "at": item.resolved_at.isoformat()}


def _has_audio_and_video(fmt: dict) -> bool:
    """Does this format carry both a picture and a sound track?"""
    return (fmt.get("vcodec") not in (None, "none")
            and fmt.get("acodec") not in (None, "none"))


def _pick_format(info: dict) -> tuple[str | None, dict]:
    """One concrete media URL out of a yt-dlp info dict.

    yt-dlp usually answers with a **merged** result for a YouTube link: a
    video stream and an audio stream as two separate URLs. A redirect can only
    point at one file, so a format that already carries both is preferred; if
    there is none, the video stream is used.
    """
    formats = info.get("requested_formats") or []
    if formats:
        for fmt in formats:
            if _has_audio_and_video(fmt) and fmt.get("url"):
                return fmt["url"], fmt
        for fmt in formats:
            if fmt.get("url"):
                return fmt["url"], fmt
    url = info.get("url")
    return (url, info) if url else (None, {})


def url_expired(url: str) -> bool:
    """Has the signed URL expired?

    YouTube hands out links with an `expire` stamp in the query. When it has
    passed, the link is dead and the item has to be resolved again.
    """
    try:
        stamp = parse_qs(urlsplit(url).query).get("expire", [""])[0]
        return bool(stamp.isdigit()) and int(stamp) < int(time.time())
    except (ValueError, IndexError, TypeError):
        return False


async def fresh_url(db: AsyncSession, item: Media) -> tuple[str | None, str | None]:
    """A live media URL for this item — resolved now when the old one is dead.

    Returns `(url, error)`. A YouTube link is signed and expires, so a
    download that happens hours after the last visit gets a fresh one.
    """
    if item.source_type != "youtube":
        return item.url, None
    if (item.resolved_state == "resolved" and item.resolved_url
            and not url_expired(item.resolved_url)):
        return item.resolved_url, None
    await resolve(db, item, force=True)
    if item.resolved_state != "resolved" or not item.resolved_url:
        return None, item.resolved_error or "yt-dlp could not resolve this link"
    return item.resolved_url, None


def _is_fresh(item: Media) -> bool:
    if item.resolved_state != "resolved" or not item.resolved_url:
        return False
    if not item.resolved_at:
        return False
    at = item.resolved_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - at < FRESH_FOR


def fallback_delivery(item: Media) -> str:
    """What the player should do with this item right now."""
    from .library import effective_delivery

    if item.source_type == "youtube":
        if item.resolved_state == "resolved" and item.resolved_url:
            return "direct"
        return "embed"
    return effective_delivery(item)
