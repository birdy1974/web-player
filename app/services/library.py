"""web-player — the media library: scanning and probing.

`ffprobe` is the only thing that knows whether an item is playable in a
browser, so every item carries a probe result (decision D3):

    {"container": "matroska", "video": "hevc", "audio": "aac",
     "width": 1920, "height": 1080, "browser_playable": false,
     "delivery": "hls", "duration_s": 123.4}

Probing a big share takes time, so it runs as a background job and the item
shows `probing…` until it is done — exactly like the reference projects do
with their duration fillers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import (
    AUDIO_EXTENSIONS, FFMPEG, FFPROBE, MEDIA_ROOT, POSTER_DIR, VIDEO_EXTENSIONS,
)
from ..models import Media, outputs_json

log = logging.getLogger("web-player.library")

# What a browser can play in a <video> element without any help.
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac"}
BROWSER_CONTAINER = {"mp4", "webm", "mov", "m4v"}

MIME_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".opus": "audio/opus", ".wav": "audio/wav",
    ".aac": "audio/aac",
}


def is_hls_url(url: str) -> bool:
    """Is this URL a playlist (an endless series of segments) rather than a file?

    Such a URL has no file behind it, so it cannot be handed to the user as a
    download.
    """
    if not url:
        return False
    path = url.split("?")[0].split("#")[0].lower()
    return path.endswith(".m3u8") or path.endswith(".m3u")


def media_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    return MIME_BY_EXT.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in (VIDEO_EXTENSIONS | AUDIO_EXTENSIONS)


# The containers a browser can play without any help. Used as the fallback
# when ffprobe has not run yet (it is slow on a big share), so an item is
# never left without a delivery.
BROWSER_NATIVE_EXT = {".mp4", ".m4v", ".webm", ".mov"}


def effective_delivery(item) -> str:
    """How this item is served *right now*.

    `item.delivery` is what the admin chose ("auto" by default). "auto" means
    "ask the probe", and when there is no probe yet we fall back to the file
    extension, so an item is always playable instead of falling through.
    """
    if item.delivery in ("direct", "hls"):
        return item.delivery
    if item.source_type == "youtube":
        # A YouTube link is direct once yt-dlp resolved it, and an embed
        # until then — the caller decides (see resolver.fallback_delivery).
        return "auto"
    probe = {}
    if item.probe:
        try:
            import json
            probe = json.loads(item.probe)
        except (TypeError, ValueError):
            probe = {}
    if probe.get("delivery") in ("direct", "hls"):
        return probe["delivery"]
    name = (item.path or item.url or "").split("?")[0].lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name.rsplit("/", 1)[-1] else ""
    if ext in BROWSER_NATIVE_EXT:
        return "direct"
    if ext in (".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"):
        return "direct"
    return "hls"


def relative_to_root(path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(MEDIA_ROOT.resolve()))
    except ValueError:
        return None


def safe_join(root: Path, sub: str | None) -> Path | None:
    """Resolve a relative path inside the root — it can never leave it.

    The path is resolved, so a symlink that points out of the media root is
    refused as well.
    """
    root = root.resolve()
    clean = (sub or "").strip().lstrip("/")
    try:
        candidate = (root / clean).resolve()
    except OSError:
        return None
    if candidate == root or candidate.is_relative_to(root):
        return candidate
    return None


def absolute_path(item: Media) -> Path | None:
    if item.source_type != "local" or not item.path:
        return None
    candidate = safe_join(MEDIA_ROOT, item.path)
    if candidate is None:
        return None          # never escape the media root
    return candidate


# --------------------------------------------------------------------------
# ffprobe
# --------------------------------------------------------------------------
async def probe(path: str | Path) -> dict | None:
    """Run ffprobe and turn its answer into the fields the app cares about."""
    cmd = [
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (asyncio.TimeoutError, OSError) as exc:
        log.warning("ffprobe failed for %s: %s (ffprobe=%s)", path, exc, FFPROBE)
        return None
    if proc.returncode != 0 or not out:
        log.warning("ffprobe answered nothing for %s (code %s)", path, proc.returncode)
        return None
    try:
        data = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        return None

    video = audio = None
    width = height = None
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not video:
            video = (stream.get("codec_name") or "").lower()
            width = stream.get("width")
            height = stream.get("height")
        elif kind == "audio" and not audio:
            audio = (stream.get("codec_name") or "").lower()

    fmt = data.get("format", {})
    container = _normalise_container(fmt.get("format_name") or "")
    duration = fmt.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    playable = _browser_playable(container, video, audio)
    return {
        "container": container,
        "video": video,
        "audio": audio,
        "width": width,
        "height": height,
        "duration_s": duration,
        "bitrate": fmt.get("bit_rate"),
        "browser_playable": playable,
        "delivery": "direct" if playable else "hls",
    }


def _normalise_container(format_name: str) -> str:
    """ffprobe reports a list ("mov,mp4,m4a,3gp,3g2,mj2"); pick one name.

    The order in that list is ffprobe's preference, not ours: for an .mp4
    file the first entry is "mov", which would make the console show a
    Matroska-looking name for an ordinary MP4.
    """
    names = [n.strip().lower() for n in format_name.split(",") if n.strip()]
    for wanted in ("mp4", "matroska", "webm", "mpegts", "avi", "ogg", "flv", "mov"):
        if wanted in names:
            return wanted
    return names[0] if names else ""


def _browser_playable(container: str, video: str | None, audio: str | None) -> bool:
    if container in ("mpegts", "hls"):
        return False
    if video is None:
        return bool(audio) and audio in BROWSER_AUDIO
    if container not in BROWSER_CONTAINER:
        return False
    return video in BROWSER_VIDEO


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------
async def scan_root(db: AsyncSession, *, group_id: int | None = None,
                    limit: int = 5000, folder: str | None = None) -> dict:
    """Add every media file under the media root that is not in the library.

    With `folder` only that subfolder is scanned — that is what the file
    browser of the console uses. Returns a small report so the console can
    say what happened.
    """
    start = MEDIA_ROOT
    if folder:
        start = safe_join(MEDIA_ROOT, folder)
        if start is None:
            raise ValueError("that folder is outside the media root")
        if not start.is_dir():
            raise ValueError("no such folder in the media root")

    added, skipped, failed = 0, 0, 0
    known = {row for row in (await db.execute(
        select(Media.path).where(Media.source_type == "local"))).scalars().all()}

    for path in _walk(start):
        if added + skipped >= limit:
            break
        rel = relative_to_root(path)
        if rel is None:
            continue
        if rel in known:
            skipped += 1
            continue
        size = path.stat().st_size
        item = Media(
            title=_title_from_name(path),
            kind="movie",
            group_id=group_id,
            source_type="local",
            path=rel,
            size_bytes=size,
            enabled=True,
            sort_order=added * 10,
            delivery="auto",
            outputs=outputs_json(True, False),
            resolved_state="",
        )
        db.add(item)
        added += 1
        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            failed += 1
            log.exception("could not add %s", path)
    log.info("scan of %s — added=%s skipped=%s failed=%s",
             start, added, skipped, failed)
    return {"added": added, "skipped": skipped, "failed": failed}


# --------------------------------------------------------------------------
# the file browser
# --------------------------------------------------------------------------
def browse(sub_path: str = "", *, limit: int = 2000) -> dict:
    """One folder of the media root, the way a file browser shows it.

    The result is what the console needs to draw an explorer: the folders
    and the files of one directory, the breadcrumbs and the parent to go up
    to. The path is always relative to the media root and can never leave it.
    """
    target = safe_join(MEDIA_ROOT, sub_path)
    if target is None:
        raise ValueError("that path is outside the media root")
    if not target.exists():
        raise FileNotFoundError("no such folder in the media root")
    if not target.is_dir():
        raise NotADirectoryError("that is a file, not a folder")

    try:
        raw = list(os.scandir(target))
    except OSError as exc:
        raise PermissionError(f"the folder cannot be read: {exc}") from exc

    entries: list[dict] = []
    truncated = False
    for entry in sorted(raw, key=lambda e: (not e.is_dir(), e.name.lower())):
        if entry.name.startswith("."):
            continue                    # hidden files are not media
        if len(entries) >= limit:
            truncated = True
            break
        path = Path(entry.path)
        rel = relative_to_root(path)
        if rel is None:
            continue                    # a link out of the media root
        try:
            if entry.is_dir():
                entries.append({
                    "name": entry.name, "type": "dir", "path": rel,
                    "size": None, "media": False, "kind": None,
                })
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix in AUDIO_EXTENSIONS:
            kind = "audio"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "video"
        else:
            kind = None
        entries.append({
            "name": entry.name, "type": "file", "path": rel, "size": size,
            "media": kind is not None, "kind": kind,
        })

    rel_dir = relative_to_root(target) or ""
    if rel_dir == ".":
        rel_dir = ""                      # the root itself
    parent = None
    if rel_dir:
        parent_path = PurePosixPath(rel_dir).parent
        parent = "" if str(parent_path) == "." else str(parent_path)

    breadcrumbs = [{"name": "media root", "path": ""}]
    if rel_dir:
        acc = ""
        for part in PurePosixPath(rel_dir).parts:
            acc = f"{acc}/{part}" if acc else part
            breadcrumbs.append({"name": part, "path": acc})

    return {
        "root": str(MEDIA_ROOT),
        "path": rel_dir,
        "parent": parent,
        "breadcrumbs": breadcrumbs,
        "entries": entries,
        "counts": {
            "dirs": sum(1 for e in entries if e["type"] == "dir"),
            "files": sum(1 for e in entries if e["type"] == "file"),
            "media": sum(1 for e in entries if e["media"]),
        },
        "truncated": truncated,
    }


def _walk(root: Path):
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if is_media_file(path):
                yield path


def _title_from_name(path: Path) -> str:
    stem = path.stem.replace("_", " ").replace(".", " ")
    return " ".join(stem.split()) or path.stem


async def probe_pending(db: AsyncSession, limit: int = 200) -> int:
    """Fill in the probe result of items that have not been probed yet."""
    rows = (await db.execute(
        select(Media).where(Media.probe.is_(None)).limit(limit))).scalars().all()
    done = 0
    for item in rows:
        path = absolute_path(item)
        if path is None or not path.exists():
            continue
        if item.size_bytes is None:
            try:
                item.size_bytes = path.stat().st_size
            except OSError:
                pass
        info = await probe_one(db, item)
        if info:
            done += 1
    return done


async def probe_one(db: AsyncSession, item: Media) -> dict | None:
    path = absolute_path(item)
    if path is None or not path.exists():
        log.warning("probe skipped — media=%s (%r): the file is gone (%s)",
                    item.id, item.title, item.path)
        return None
    info = await probe(path)
    if info:
        item.probe = json.dumps(info)
        item.delivery = info["delivery"]
        if info.get("duration_s"):
            item.duration_s = info["duration_s"]
        await db.commit()
        # A file without a video track (an audio book, a radio recording) has
        # no frame to grab — that is not an error, it simply has no poster.
        if not item.poster and info.get("video"):
            poster = await generate_poster(path, item.title)
            if poster:
                item.poster = poster
                await db.commit()
    if info:
        log.info("probed media=%s (%r): container=%s video=%s audio=%s %sx%s %.1fs "
                 "delivery=%s", item.id, item.title, info.get("container"),
                 info.get("video"), info.get("audio"), info.get("width"),
                 info.get("height"), info.get("duration_s") or 0, info.get("delivery"))
    return info


async def generate_poster(path: Path, title: str) -> str | None:
    """Grab one frame with ffmpeg and store it in the data volume.

    A poster is what makes the playlist readable at a glance; it is optional
    (a failure here must never stop an item from being added).
    """
    import re as _re

    slug = _re.sub(r"[^a-z0-9]+", "-", (title or path.stem).lower()).strip("-") or "item"
    name = f"{slug}.jpg"
    target = POSTER_DIR / name
    if target.exists():
        return name
    try:
        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        # The temporary name keeps the .jpg extension: ffmpeg picks the
        # muxer from the file name, so a ".tmp" name would fail silently.
        tmp = POSTER_DIR / (name + ".tmp.jpg")
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", "5", "-i", str(path), "-frames:v", "1",
               "-vf", "scale=480:-2", "-q:v", "6", "-y", str(tmp)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(target)
            return name
        message = err.decode("utf-8", "replace").strip().splitlines()
        log.warning("no poster for %s: %s", path, message[-1] if message else
                    f"ffmpeg exited with {proc.returncode}")
        tmp.unlink(missing_ok=True)
    except (asyncio.TimeoutError, OSError) as exc:
        log.warning("could not make a poster for %s: %s", path, exc)
    return None
