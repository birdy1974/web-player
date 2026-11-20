"""Seekable, on-demand HLS for finite movies.

The VOD playlist is available before any media is encoded. FFmpeg starts
where the browser first asks for a segment and restarts at an uncached seek.
The encoder and keyframe grid stay the same on every start: a stream copy
could only split at the source's keyframes, which need not be on that grid.
Files playable directly in the browser bypass this service entirely.
"""
from __future__ import annotations

import asyncio
import logging
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import FFMPEG, HLS_DIR, HLS_SEGMENT, TRANSCODE_MODE, vaapi_device

log = logging.getLogger("web-player.hls")

SESSION_TTL = 120          # seconds without a request before a session dies
SESSION_PREFIX = "wp-hls-"
SEGMENT_TIMEOUT = 30
READ_AHEAD = 2             # let the current encoder finish nearby segments
TAIL_PADDING = 0.050      # container durations can include a sliver of audio padding

_transcode_state: dict[str, bool] = {}


# --------------------------------------------------------------------------
# hardware detection
# --------------------------------------------------------------------------
def vaapi_available() -> bool:
    """Is VAAPI usable? The answer is cached — `vainfo` is not free."""
    if "ok" in _transcode_state:
        return _transcode_state["ok"]
    device = Path(vaapi_device())
    ok = device.exists() and shutil.which(FFMPEG) is not None
    if ok:
        try:
            proc = __import__("subprocess").run(
                ["vainfo", "--display", "drm", "--device", str(device)],
                capture_output=True, timeout=15)
            ok = proc.returncode == 0 and any(
                b"VAProfileH264" in line and b"VAEntrypointEncSlice" in line
                for line in proc.stdout.splitlines()
            )
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


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
@dataclass
class HLSSession:
    media_id: int
    user_id: int | None
    source: str
    duration: float
    seg_seconds: int
    total_segments: int
    directory: Path
    video_codec: str | None
    mode: str
    start_segment: int = 0
    next_segment: int = 0
    proc: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    last_access: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    failed: str = ""

    @property
    def staging(self) -> Path:
        return self.directory / "encoding"

    def segment_time(self, index: int) -> float:
        return index * self.seg_seconds

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


_sessions: dict[str, HLSSession] = {}


def session_key(media_id: int, user_id: int | None) -> str:
    return f"{media_id}:{user_id or 0}"


async def get_session(media_id: int, user_id: int | None, source: str,
                      duration: float, video_codec: str | None,
                      *, audio_only: bool = False) -> HLSSession:
    """Prepare the timeline without starting FFmpeg on a manifest request."""
    key = session_key(media_id, user_id)
    while True:
        session = _sessions.get(key)
        if session is None:
            directory = HLS_DIR / f"{SESSION_PREFIX}{media_id}-{user_id or 0}"
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
            count = math.ceil(duration / HLS_SEGMENT)
            if count > 1 and duration - (count - 1) * HLS_SEGMENT <= TAIL_PADDING:
                # Padding after the last video frame doesn't start a new GOP.
                count -= 1
            session = _sessions[key] = HLSSession(
                media_id=media_id, user_id=user_id, source=source, duration=duration,
                seg_seconds=HLS_SEGMENT, total_segments=count,
                directory=directory, video_codec=video_codec,
                mode="audio" if audio_only else transcode_mode(),
            )
        async with session.lock:
            # It could have been reaped while we were waiting for the lock.
            if _sessions.get(key) is not session:
                continue
            if session.source != source or session.duration != duration:
                await _close(key, session)
                continue
            session.last_access = time.monotonic()
            return session


def _command(session: HLSSession, index: int) -> list[str]:
    offset = session.segment_time(index)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    hw_decode = (session.mode == "vaapi"
                 and (session.video_codec or "").lower() in {"h264", "hevc", "vp9"})
    if session.mode == "vaapi":
        cmd += ["-vaapi_device", vaapi_device()]
        if hw_decode:
            cmd += ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]
    if offset:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-i", session.source]

    if session.mode == "audio":
        cmd += ["-vn", "-map", "0:a:0"]
    else:
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
        if session.mode == "vaapi":
            cmd += [
                "-vf", "scale_vaapi=format=nv12" if hw_decode else "format=nv12,hwupload",
                "-c:v", "h264_vaapi", "-b:v", "4000k", "-maxrate", "4500k", "-bufsize", "8000k",
                "-low_power", "1",
            ]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        # hls_time is only a target: without a keyframe the muxer waits for
        # the next GOP, so a nominal 4 s segment can contain 10 s of video.
        cmd += ["-force_key_frames", f"expr:gte(t,n_forced*{session.seg_seconds})"]

    return cmd + [
        "-c:a", "aac", "-b:a", "128k",
        # A fresh encoder starts its timestamps at zero. Keep its output on
        # the movie's timeline so it can play next to already-cached segments.
        "-output_ts_offset", f"{offset:.3f}",
        "-f", "hls", "-hls_time", str(session.seg_seconds), "-hls_list_size", "4",
        "-hls_flags", "temp_file+independent_segments",
        "-start_number", str(index),
        "-hls_segment_filename", str(session.staging / "seg%06d.ts"),
        str(session.staging / "index.m3u8"),
    ]


def _advance_encoder(session: HLSSession) -> None:
    # The last entry of FFmpeg's short private playlist is a completion
    # marker. A .ts existing on disk alone does not prove a write is done.
    try:
        lines = (session.staging / "index.m3u8").read_text().splitlines()
    except FileNotFoundError:
        return
    for name in reversed(lines):
        number = name[3:-3]
        if name.startswith("seg") and name.endswith(".ts") and number.isdigit():
            session.next_segment = max(session.next_segment, int(number) + 1)
            return


def _ready_segment(session: HLSSession, index: int) -> Path | None:
    name = f"seg{index:06d}.ts"
    cached = session.directory / name
    if cached.is_file():
        return cached
    if index >= session.next_segment:
        _advance_encoder(session)
    if session.start_segment <= index < session.next_segment:
        pending = session.staging / name
        if pending.is_file():
            # Keep published files out of the encoder's path so a later
            # seek cannot overwrite another response's content.
            pending.replace(cached)
            return cached
    return None


async def _start(session: HLSSession, index: int) -> None:
    """Start at the requested segment. Only called with session.lock held."""
    await _stop_process(session)
    if session.staging.exists():
        _advance_encoder(session)
        for finished in range(session.start_segment, session.next_segment):
            _ready_segment(session, finished)
        shutil.rmtree(session.staging)
    session.staging.mkdir()
    session.start_segment = session.next_segment = index
    session.failed = ""

    log.info("hls start: media=%s user=%s offset=%.1f mode=%s codec=%s source=%s",
             session.media_id, session.user_id, session.segment_time(index), session.mode,
             session.video_codec or "unknown", session.source)
    try:
        proc = await asyncio.create_subprocess_exec(
            *_command(session, index), stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        session.failed = f"ffmpeg could not be started: {exc}"
        log.error("hls ffmpeg could not be started for media=%s: %s", session.media_id, exc)
        return
    session.proc = proc

    async def watch() -> None:
        _, err = await proc.communicate()
        if session.proc is proc and proc.returncode not in (0, None):
            message = err.decode("utf-8", "replace").strip().splitlines()
            session.failed = message[-1][:300] if message else "ffmpeg stopped"
            log.warning("hls ffmpeg for media=%s stopped (code %s): %s",
                        session.media_id, proc.returncode, session.failed)

    session.task = asyncio.create_task(watch())


async def _stop_process(session: HLSSession) -> None:
    proc, session.proc = session.proc, None
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass
    if session.task:
        session.task.cancel()
        session.task = None


async def ensure_segment(session: HLSSession, index: int) -> Path | None:
    """Return cached data, wait briefly for the encoder, or seek it once."""
    if not 0 <= index < session.total_segments:
        return None
    async with session.lock:
        if _sessions.get(session_key(session.media_id, session.user_id)) is not session:
            return None
        path = _ready_segment(session, index)
        if path is not None:
            return path
        if (not session.alive() or index < session.start_segment
                or index > session.next_segment + READ_AHEAD):
            if session.alive():
                log.info("hls seek: media=%s segment=%s (encoder at %s)",
                         session.media_id, index, session.next_segment)
            await _start(session, index)
        proc = session.proc

    # Don't hold the restart lock while encoding. A new seek can supersede
    # this request, and adjacent requests can wait for the same process.
    deadline = time.monotonic() + SEGMENT_TIMEOUT
    while True:
        path = _ready_segment(session, index)
        if path is not None:
            log.info("hls segment ready: media=%s segment=%s", session.media_id, index)
            return path
        if proc is None or session.proc is not proc or proc.returncode is not None:
            return None
        if time.monotonic() >= deadline:
            log.warning("hls segment %s for media=%s took longer than %s s",
                        index, session.media_id, SEGMENT_TIMEOUT)
            return None
        await asyncio.sleep(0.05)


def build_playlist(session: HLSSession) -> str:
    """A full VOD playlist — the client may seek before a segment exists."""
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{session.seg_seconds}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for index in range(session.total_segments):
        length = (session.seg_seconds if index < session.total_segments - 1
                  else session.duration - session.segment_time(index))
        lines.append(f"#EXTINF:{length:.3f},")
        lines.append(f"/player/segment/{session.media_id}/{index}.ts")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


async def _close(key: str, session: HLSSession) -> None:
    await _stop_process(session)
    shutil.rmtree(session.directory, ignore_errors=True)
    _sessions.pop(key, None)


async def drop_session(media_id: int, user_id: int | None) -> None:
    key = session_key(media_id, user_id)
    if session := _sessions.get(key):
        async with session.lock:
            if _sessions.get(key) is session:
                await _close(key, session)
                log.info("hls session dropped: media=%s user=%s", media_id, user_id)


async def reap_idle() -> int:
    """Stop the sessions nobody is watching any more."""
    now = time.monotonic()
    count = 0
    for key, session in list(_sessions.items()):
        if now - session.last_access <= SESSION_TTL:
            continue
        async with session.lock:
            if _sessions.get(key) is session and now - session.last_access > SESSION_TTL:
                await _close(key, session)
                count += 1
                log.info("hls session reaped (idle): media=%s user=%s", session.media_id, session.user_id)
    return count


def active_sessions() -> list[dict]:
    return [{
        "media_id": s.media_id,
        "user_id": s.user_id,
        "offset": round(s.segment_time(s.start_segment), 1),
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
