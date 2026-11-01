"""web-player — on-demand HLS.

Decision D3: an item the browser cannot play (MKV, HEVC, AVI, …) is served
as a segmented HLS stream that ffmpeg produces **while the user watches**.
That is what makes "jump to a specific timestamp" work for a NAS movie:

* the playlist is a full VOD list built from the known duration, so the
  player can seek anywhere before a single segment exists;
* a segment that is not there yet makes ffmpeg **restart at that offset**
  (`-ss` before `-i` = fast input seek), which is the same trick the
  reference project uses for its pause/resume;
* H.264 sources are **copied** (0 % CPU); HEVC/VP9/AV1 are **transcoded with
  Intel VAAPI** (the DS918+ encodes H.264 only) with a libx264 fallback.

Sessions are idle-collected so a closed browser does not leave ffmpeg running.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import FFMPEG, HLS_DIR, HLS_SEGMENT, TRANSCODE_MODE, vaapi_device

log = logging.getLogger("web-player.hls")

SESSION_TTL = 120          # seconds without a request before a session dies
SESSION_PREFIX = "wp-hls-"

_transcode_state: dict[str, bool] = {}


# --------------------------------------------------------------------------
# hardware detection
# --------------------------------------------------------------------------
def vaapi_available() -> bool:
    """Is VAAPI usable? The answer is cached — `vainfo` is not free."""
    if "ok" in _transcode_state:
        return _transcode_state["ok"]
    device = Path(vaapi_device())
    ok = device.exists() and shutil.which("ffmpeg") is not None
    if ok:
        try:
            proc = __import__("subprocess").run(
                ["vainfo"], capture_output=True, timeout=15)
            ok = proc.returncode == 0 and b"VAProfileH264" in proc.stdout
        except Exception:  # noqa: BLE001
            ok = False
    _transcode_state["ok"] = ok
    log.info("VAAPI: %s", "available" if ok else "not available (software fallback)")
    return ok


def transcode_mode() -> str:
    """'vaapi' | 'software' — what the pipeline will actually use."""
    if TRANSCODE_MODE == "software":
        return "software"
    if TRANSCODE_MODE == "vaapi":
        return "vaapi"
    return "vaapi" if vaapi_available() else "software"


def needs_transcode(video_codec: str | None) -> bool:
    return (video_codec or "").lower() not in ("h264", "avc1", "")


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
@dataclass
class Segment:
    index: int
    start: float
    duration: float


@dataclass
class HLSSession:
    key: str
    media_id: int
    user_id: int | None
    source: str
    duration: float
    seg_seconds: int
    total_segments: int
    start_segment: int = 0
    offset: float = 0.0
    directory: Path = field(default_factory=Path)
    proc: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_access: float = field(default_factory=time.monotonic)
    mode: str = "copy"
    failed: str = ""
    restarting: bool = False

    def segment_time(self, index: int) -> float:
        return index * self.seg_seconds

    def segment_path(self, index: int) -> Path:
        return self.directory / f"seg{index:06d}.ts"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


_sessions: dict[str, HLSSession] = {}


def session_key(media_id: int, user_id: int | None) -> str:
    return f"{media_id}:{user_id or 0}"


async def get_session(media_id: int, user_id: int | None, source: str,
                      duration: float, video_codec: str | None) -> HLSSession:
    """Return the session for this item, restarting it when the client seeks."""
    key = session_key(media_id, user_id)
    session = _sessions.get(key)
    seg = HLS_SEGMENT
    total = max(1, int((duration or 0) // seg) + 1)

    if session is not None and not session.alive() and session.failed:
        # The previous ffmpeg died; start over rather than serving nothing.
        session = None
        _sessions.pop(key, None)

    if session is None:
        session = HLSSession(
            key=key, media_id=media_id, user_id=user_id, source=source,
            duration=duration, seg_seconds=seg, total_segments=total,
            directory=_new_dir(media_id, user_id),
        )
        _sessions[key] = session
        await _start(session, 0, video_codec)
    session.last_access = time.monotonic()
    return session


def _new_dir(media_id: int, user_id: int | None) -> Path:
    HLS_DIR.mkdir(parents=True, exist_ok=True)
    path = HLS_DIR / f"{SESSION_PREFIX}{media_id}-{user_id or 0}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _start(session: HLSSession, offset: float, video_codec: str | None) -> None:
    """Start ffmpeg at `offset`, writing segments into the session directory."""
    await _stop_process(session)

    start_segment = int(offset // session.seg_seconds)
    session.start_segment = start_segment
    session.offset = offset
    session.mode = "transcode" if needs_transcode(video_codec) else "copy"
    mode = transcode_mode() if session.mode == "transcode" else "copy"

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if offset > 0:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", session.source]

    if mode == "copy":
        cmd += ["-c", "copy"]
    elif mode == "vaapi":
        cmd += [
            "-vaapi_device", vaapi_device(),
            "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi",
            "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi", "-b:v", "4000k", "-maxrate", "4500k", "-bufsize", "8000k",
            "-low_power", "1",
        ]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k"]

    cmd += [
        "-f", "hls",
        "-hls_time", str(session.seg_seconds),
        "-hls_list_size", "0",
        "-hls_flags", "temp_file",
        "-start_number", str(start_segment),
        "-hls_segment_filename", str(session.directory / "seg%06d.ts"),
        str(session.directory / "index.m3u8"),
    ]

    log.info("hls start: media=%s user=%s offset=%.1f mode=%s codec=%s source=%s",
             session.media_id, session.user_id, offset, mode,
             video_codec or "unknown", session.source)
    try:
        session.proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        session.failed = f"ffmpeg could not be started: {exc}"
        session.proc = None
        log.error("hls ffmpeg could not be started for media=%s: %s (ffmpeg=%s)",
                  session.media_id, exc, FFMPEG)
        return

    async def _watch() -> None:
        _, err = await session.proc.communicate()
        if session.restarting:
            # We killed it ourselves to seek; that is not a failure.
            session.restarting = False
            return
        if session.proc.returncode not in (0, None):
            message = err.decode("utf-8", "replace").strip().splitlines()
            session.failed = message[-1][:300] if message else "ffmpeg stopped"
            log.warning("hls ffmpeg for media=%s stopped (code %s): %s",
                        session.media_id, session.proc.returncode, session.failed)

    session.task = asyncio.create_task(_watch())


async def _stop_process(session: HLSSession) -> None:
    if session.proc is not None and session.proc.returncode is None:
        session.restarting = True
        try:
            session.proc.kill()
            await asyncio.wait_for(session.proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
    session.proc = None
    if session.task:
        session.task.cancel()
        session.task = None


async def ensure_segment(session: HLSSession, index: int) -> Path | None:
    """Make sure the file for `index` exists, seeking ffmpeg when needed."""
    path = session.segment_path(index)
    if path.exists() and path.stat().st_size > 0:
        log.debug("hls segment %s for media=%s was already there", index, session.media_id)
        return path

    if index < session.start_segment:
        # The client went back before the current window: restart at that
        # segment and let it produce the file again.
        log.info("hls seek backwards: media=%s segment=%s (from %s)",
                 session.media_id, index, session.start_segment)
        await _start(session, session.segment_time(index), None)
    elif index > session.start_segment + 1:
        ahead = session.segment_time(index) - session.offset
        if ahead > session.seg_seconds * 3:
            log.info("hls seek forwards: media=%s segment=%s (from %s, %.1fs ahead)",
                     session.media_id, index, session.start_segment, ahead)
            await _start(session, session.segment_time(index), None)

    # Wait for ffmpeg to write it.
    for _ in range(600):          # 30 s
        if path.exists() and path.stat().st_size > 0:
            log.info("hls segment ready: media=%s segment=%s (%s bytes)",
                     session.media_id, index, path.stat().st_size)
            return path
        if session.proc is None or session.proc.returncode is not None:
            log.warning("hls segment %s for media=%s is not coming (ffmpeg is gone)",
                        index, session.media_id)
            return None
        await asyncio.sleep(0.05)
    log.warning("hls segment %s for media=%s took longer than 30 s", index, session.media_id)
    return None


def build_playlist(session: HLSSession) -> str:
    """A full VOD playlist — the client may seek anywhere inside it."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{session.seg_seconds}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    count = session.total_segments
    for index in range(count):
        start = index * session.seg_seconds
        length = min(session.seg_seconds, max(0.0, (session.duration or 0) - start))
        if length <= 0:
            break
        lines.append(f"#EXTINF:{length:.3f},")
        lines.append(f"/player/segment/{session.media_id}/{index}.ts")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


async def drop_session(media_id: int, user_id: int | None) -> None:
    session = _sessions.pop(session_key(media_id, user_id), None)
    if session:
        await _stop_process(session)
        shutil.rmtree(session.directory, ignore_errors=True)
        log.info("hls session dropped: media=%s user=%s", media_id, user_id)


async def reap_idle() -> int:
    """Stop the sessions nobody is watching any more."""
    now = time.monotonic()
    dead = [s for s in _sessions.values() if now - s.last_access > SESSION_TTL]
    for session in dead:
        _sessions.pop(session.key, None)
        await _stop_process(session)
        shutil.rmtree(session.directory, ignore_errors=True)
        log.info("hls session reaped (idle): media=%s user=%s offset=%.1f",
                 session.media_id, session.user_id, session.offset)
    return len(dead)


def active_sessions() -> list[dict]:
    return [{
        "media_id": s.media_id,
        "user_id": s.user_id,
        "offset": round(s.offset, 1),
        "mode": s.mode,
        "segments": s.total_segments,
        "alive": s.alive(),
        "failed": s.failed,
        "idle_s": round(time.monotonic() - s.last_access, 1),
    } for s in _sessions.values()]


def vaapi_report() -> dict:
    return {
        "available": vaapi_available(),
        "mode": transcode_mode(),
        "device": vaapi_device(),
        "segment_seconds": HLS_SEGMENT,
    }
