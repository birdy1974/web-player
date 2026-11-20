#!/usr/bin/env python3
"""End-to-end test of the web-player application.

Runs against a throw-away data directory and a throw-away media directory, so
it never touches the real installation.

    .venv/bin/python tools/test_app.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="web-player-test-"))
DATA = TMP / "config"
MEDIA = TMP / "media"
DATA.mkdir(parents=True)
MEDIA.mkdir(parents=True)


def make_sample(target: Path) -> None:
    """A small mp4 so the streaming paths have something to serve.

    The demo clips of the mock-up are generated (they are not in the
    repository), so a plain checkout makes its own: a few seconds of a test
    pattern. ffprobe then has something real to read — and the poster frame
    (grabbed at 5 s) is there as well.
    """
    sample = ROOT / "mockup" / "media" / "coffee-and-code.mp4"
    if sample.exists():
        shutil.copy(sample, target)
        return
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=8",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(target)],
            check=False)
    except OSError:
        pass          # no ffmpeg on this machine — the dummy file below is used
    if not target.exists() or target.stat().st_size == 0:
        target.write_bytes(b"not a real video - the probe will fail, that is fine")


make_sample(MEDIA / "clip.mp4")

os.environ.update({
    "WEB_PLAYER_DATA_DIR": str(DATA),
    "WEB_PLAYER_MEDIA_ROOT": str(MEDIA),
    "WEB_PLAYER_ADMIN_PASSWORD": "admin-secret",
    "WEB_PLAYER_BASE_URL": "http://nas.local:8088",
    "WEB_PLAYER_LOG_LEVEL": "WARNING",
    "WEB_PLAYER_TRANSCODE": "software",
    "WEB_PLAYER_HLS_SEGMENT": "4",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.assets import asset_url  # noqa: E402
from app.main import app  # noqa: E402
from app.services import hls  # noqa: E402

failures = 0


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    global failures
    failures += 1
    print(f"  ✗ {msg}")


def check(condition, msg: str) -> bool:
    if condition:
        ok(msg)
        return True
    fail(msg)
    return False


def fake_resolved(media_id: int, url: str) -> None:
    """Pretend that yt-dlp resolved a YouTube link.

    The test must not need the internet, so the signed media URL is written
    into the database directly. The `expire` stamp is far in the future, so
    the application uses the URL as it is.
    """
    import sqlite3

    from app.config import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "update media set source_type='youtube', resolved_url=?, resolved_state='resolved',"
            " resolved_at=datetime('now') where id=?", (url, media_id))
        conn.commit()
    finally:
        conn.close()


def start_file_server(directory: Path):
    """A tiny HTTP server, so the proxy mode can be tested without internet."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):  # noqa: ANN002
            pass

    handler = functools.partial(Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def check_hls_seek(client: TestClient, group_id: int, user_id: int) -> None:
    """Exercise the HTTP and real FFmpeg paths, not just the VOD manifest."""
    print("\n== HLS startup and cold seeking ==")
    sample = MEDIA / "hevc-seek.mkv"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=royalblue:s=320x180:r=25",
        "-f", "lavfi", "-i", "sine=f=440:r=48000", "-t", "24.5",
        "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "pools=1:frame-threads=1:keyint=250:scenecut=0:log-level=error",
        "-c:a", "aac", str(sample),
    ], capture_output=True, check=True)
    r = client.post("/api/console/media", json={
        "title": "HEVC seek test", "path": sample.name, "group_id": group_id,
    })
    if not check(r.status_code == 200, "the small HEVC fixture is added"):
        return
    media_id = r.json()["id"]
    client.post("/player/login", data={"username": "family", "password": "family-secret"})
    manifest = client.get(f"/player/stream/{media_id}/stream.m3u8")
    if not check(manifest.status_code == 200 and manifest.headers.get("cache-control") == "no-store",
                 "the HLS manifest is available but not cacheable by the browser"):
        return
    key = hls.session_key(media_id, user_id)
    try:
        durations = [float(value) for value in re.findall(r"^#EXTINF:([\d.]+),", manifest.text, re.M)]
        check(len(durations) == 7 and durations[:6] == [4.0] * 6 and 0 < durations[-1] < 1,
              f"the manifest has six full segments and a fractional tail ({durations})")
        session = client.portal.call(lambda: hls._sessions[key])
        check(session.video_pix_fmt == "yuv420p", "ffprobe passes the pixel format to the HLS session")
        check(not session.staging.exists(), "the manifest does not start FFmpeg at the beginning")

        def segment(index: int):
            return client.get(f"/player/segment/{media_id}/{index}.ts")

        def inspect_segment(index: int, payload: bytes) -> float:
            # Inspect the fixture's single MPEG-TS video stream directly.
            # Some static FFmpeg builds crash when opening MPEG-TS as input.
            video_pid = None
            first_frame = bytearray()
            timestamps = []
            assert payload and len(payload) % 188 == 0, f"segment {index} is not complete MPEG-TS"
            for offset in range(0, len(payload), 188):
                packet = payload[offset:offset + 188]
                assert packet[0] == 0x47, f"segment {index} lost MPEG-TS packet sync"
                if not packet[3] & 0x10:  # no payload
                    continue
                pid = ((packet[1] & 0x1f) << 8) | packet[2]
                data = packet[5 + packet[4]:] if packet[3] & 0x20 else packet[4:]
                if packet[1] & 0x40:  # beginning of a PES packet
                    if not data.startswith(b"\0\0\x01\xe0") or not data[7] & 0x80:
                        continue
                    video_pid = pid
                    a, b, c, d, e = data[9:14]
                    timestamps.append(((a & 0x0e) << 29) | (b << 22) | ((c & 0xfe) << 14)
                                      | (d << 7) | (e >> 1))
                    data = data[9 + data[8]:]
                if pid == video_pid and len(timestamps) == 1:
                    first_frame.extend(data)
            nal_types = {value[0] & 0x1f for value in re.findall(rb"\0\0\x01(.)", first_frame, re.S)}
            check({5, 7, 8} <= nal_types, f"segment {index} starts with H.264 parameter sets and an IDR frame")
            assert timestamps, f"segment {index} contains no video timestamps"
            duration = (max(timestamps) - min(timestamps)) / 90000 + 1 / 25
            check(abs(duration - durations[index]) < 0.15,
                  f"segment {index} contains {duration:.2f}s of video (manifest: {durations[index]:.2f}s)")
            return min(timestamps) / 90000

        ahead = segment(4)
        if not check(ahead.status_code == 200 and ahead.headers.get("content-type") == "video/mp2t",
                     "requesting an ungenerated segment starts directly at the forward offset"):
            return
        check(session.start_segment == 4, "FFmpeg did not encode the first 16 seconds for that seek")
        ahead_start = inspect_segment(4, ahead.content)
        tail = segment(6)
        if check(tail.status_code == 200, "the fractional last segment is playable"):
            inspect_segment(6, tail.content)

        first = segment(0)
        if not check(first.status_code == 200 and session.start_segment == 0,
                     "jumping backwards to an uncached segment restarts the encoder"):
            return
        first_start = inspect_segment(0, first.content)
        check(abs(ahead_start - first_start - 16) < 0.15, "both FFmpeg runs encode on the same timestamp timeline")
        again = segment(4)
        check(again.status_code == 200 and again.content == ahead.content and session.start_segment == 0,
              "jumping to a cached segment reuses the bytes without restarting FFmpeg")
        check(segment(-1).status_code == segment(len(durations)).status_code == 404,
              "out-of-range segment requests do not start an encoder")

        client.post("/player/login", data={"username": "stranger", "password": "s3cret"})
        check(segment(4).status_code == 404, "even a cached HLS segment still checks the viewer's access")
    finally:
        client.portal.call(hls.drop_session, media_id, user_id)


def check_vaapi_pipeline(client: TestClient, media_id: int, user_id: int) -> None:
    print("\n== VAAPI commands and diagnostics ==")
    session = hls.HLSSession(
        media_id=media_id, user_id=user_id, source="/media/family movie.mkv",
        duration=120, seg_seconds=4, total_segments=30, directory=TMP / "vaapi",
        video_codec="h264", video_pix_fmt="yuv420p", mode="vaapi",
    )

    def value(command, option):
        return command[command.index(option) + 1] if option in command else None

    with patch.object(hls, "VAAPI_RATE_CONTROL", "cqp"):
        first, seek = (hls._command(session, index) for index in (0, 15))
        for label, cmd in (("startup", first), ("seek", seek)):
            check(value(cmd, "-hwaccel") == value(cmd, "-hwaccel_output_format") == "vaapi"
                  and value(cmd, "-c:v") == "h264_vaapi" and "-vf" not in cmd,
                  f"{label} passes H.264 decoder surfaces straight to the VAAPI encoder")
            check(value(cmd, "-rc_mode") == "CQP" and value(cmd, "-qp") == "23"
                  and not {"-b:v", "-maxrate", "-bufsize", "-low_power"}.intersection(cmd),
                  f"{label} uses the CQP profile without requesting a bitrate or forcing an entry point")
        check("-ss" not in first and value(seek, "-ss") == "60.000" and seek.index("-ss") < seek.index("-i")
              and value(seek, "-output_ts_offset") == "60.000" and value(seek, "-start_number") == "15"
              and value(first, "-force_key_frames") == value(seek, "-force_key_frames") == "expr:gte(t,n_forced*4)",
              "hardware CQP preserves the segment grid and seek timestamps")

    with patch.object(hls, "VAAPI_RATE_CONTROL", "bitrate"):
        capped = hls._command(session, 0)
        check(value(capped, "-b:v") == "4000k" and value(capped, "-maxrate") == "4500k"
              and value(capped, "-bufsize") == "8000k" and "-rc_mode" not in capped,
              "the default VAAPI profile keeps its bitrate controls")
        for codec, pix_fmt, expected in (
            ("h264", None, None),
            ("hevc", "yuv420p", None),
            ("hevc", "yuv420p10le", "scale_vaapi=format=nv12"),
            ("av1", "yuv420p", "format=nv12,hwupload"),
        ):
            cmd = hls._command(replace(session, video_codec=codec, video_pix_fmt=pix_fmt), 0)
            check(value(cmd, "-vf") == expected, f"{codec}/{pix_fmt or 'legacy probe'} filter: {expected or 'none'}")

    client.post("/player/login", data={"username": "family", "password": "family-secret"})
    client.portal.call(hls.drop_session, media_id, user_id)
    encoders = []
    real_spawn = hls.asyncio.create_subprocess_exec

    async def spawn(*cmd, **kwargs):
        encoders.append(value(cmd, "-c:v"))
        if "h264_vaapi" in cmd:
            # Simulate a failing GPU on CPU-only CI runners.
            return await real_spawn(sys.executable, "-c", (
                "import sys; print('Failed to create processing pipeline config\\n"
                "Error while processing the decoded data', file=sys.stderr); sys.exit(1)"
            ), **kwargs)
        return await real_spawn(*cmd, **kwargs)

    with (
        patch.object(hls, "vaapi_available", return_value=True),
        patch.object(hls, "VAAPI_RATE_CONTROL", "cqp"),
        patch.object(hls.asyncio, "create_subprocess_exec", new=spawn),
    ):
        for mode in ("vaapi", "auto"):
            try:
                with patch.object(hls, "TRANSCODE_MODE", mode):
                    r = client.get(f"/player/segment/{media_id}/0.ts")
                check(r.status_code == 504 and encoders == ["h264_vaapi"],
                      f"{mode} does not silently start a CPU encoder after a VAAPI failure")
            finally:
                client.portal.call(hls.drop_session, media_id, user_id)
                encoders.clear()

    records = client.get("/api/console/logs", params={"search": "Failed to create processing pipeline config"}).json()
    check(bool(records.get("records")), "the admin log includes the driver diagnostic, not just the last error line")


def main() -> int:
    with TestClient(app) as client:
        print("\n== health ==")
        r = client.get("/api/health")
        check(r.status_code == 200, f"/api/health returns 200 ({r.status_code})")
        check(r.json().get("ok") is True, "the database answers")

        print("\n== the console is private ==")
        r = client.get("/api/console/overview")
        check(r.status_code == 401, f"the console API refuses an anonymous caller ({r.status_code})")
        r = client.get("/console", follow_redirects=False)
        check(r.status_code in (302, 307), f"/console redirects to the login ({r.status_code})")

        print("\n== the administrator signs in ==")
        r = client.post("/console/login", data={"username": "admin", "password": "wrong"})
        check(r.status_code == 401, "a wrong password is refused")
        r = client.post("/console/login", data={"username": "admin", "password": "admin-secret"},
                        follow_redirects=False)
        check(r.status_code in (302, 307), f"the right password signs in ({r.status_code})")

        print("\n== groups ==")
        r = client.post("/api/console/groups", json={"name": "Movies"})
        check(r.status_code == 200 and "id" in r.json(), "a group can be created")
        movies_id = r.json().get("id")
        r = client.post("/api/console/groups", json={"name": "Movies"})
        check(r.status_code == 409, "a duplicate group is refused")
        r = client.get("/api/console/groups")
        check(len(r.json()["items"]) == 1, "the group list shows one group")

        print("\n== users ==")
        r = client.post("/api/console/users", json={
            "name": "family", "password": "family-secret", "group_ids": [movies_id]})
        check(r.status_code == 200, "a user can be created")
        family_id = r.json()["id"]
        r = client.post("/api/console/users", json={"name": "family", "password": "x"})
        check(r.status_code == 409, "a duplicate user is refused")
        r = client.post("/api/console/users", json={"name": "noPW"})
        check(r.status_code == 400, "a user without a password is refused")
        r = client.get("/api/console/users")
        check(len(r.json()["items"]) == 2, "the user list shows the administrator and the new user")
        r = client.put(f"/api/console/users/{family_id}", json={"max_streams": 20})
        check(r.status_code == 200, "a user's max simultaneous streams can be set to 20")
        users = client.get("/api/console/users").json()["items"]
        check(any(u["id"] == family_id and u["max_streams"] == 20 for u in users),
              "the console saves and returns the per-user value of 20")

        print("\n== the library is scanned ==")
        r = client.post("/api/console/media/scan", json={})
        check(r.status_code == 200, f"the scan runs ({r.status_code})")
        report = r.json()
        check(report.get("added", 0) >= 1, f"the clip on disk was added ({report})")
        r = client.get("/api/console/media")
        items = r.json()["items"]
        check(len(items) == 1, f"the library has one item ({len(items)})")
        media = items[0]
        check(media["title"] == "clip", f"the title came from the file name ({media['title']})")
        if shutil.which("ffprobe"):
            check(media["probe"].get("container") == "mp4",
                  f"ffprobe answered ({media['probe'].get('container')})")
            check(media["delivery"] == "direct",
                  f"a browser-playable mp4 is served directly ({media['delivery']})")
        else:
            print("  ! ffprobe is not installed here - the probe assertions are skipped")
            client.post(f"/api/console/media/{media_id}/probe")
        media_id = media["id"]

        print("\n== the per-user visibility ==")
        r = client.put(f"/api/console/media/{media_id}", json={
            "title": "clip", "group_id": movies_id, "output_web": True, "output_enigma2": True})
        check(r.status_code == 200, "an item can be put in a group")
        r = client.get("/api/console/media")
        item = r.json()["items"][0]
        per_user = item["per_user"][str(family_id)]
        check(per_user["visible"] is True, "the item is visible through the group")
        check(per_user["reason"] == "via group", f"the reason is explained ({per_user['reason']})")

        # hide it for this user
        r = client.post(f"/api/console/media/{media_id}/visibility",
                        json={"user_id": family_id, "state": "deny"})
        check(r.status_code == 200, "the item can be hidden for one user")
        r = client.get("/api/console/media")
        per_user = r.json()["items"][0]["per_user"][str(family_id)]
        check(per_user["visible"] is False and per_user["reason"] == "hidden for this user",
              f"the override wins over the group ({per_user})")
        # grant it back
        client.post(f"/api/console/media/{media_id}/visibility",
                    json={"user_id": family_id, "state": "allow"})
        r = client.get("/api/console/media")
        per_user = r.json()["items"][0]["per_user"][str(family_id)]
        check(per_user["visible"] is True and per_user["reason"] == "granted to this user",
              f"the grant wins over the group ({per_user})")

        print("\n== a user who is not in the group sees nothing ==")
        r = client.post("/api/console/users", json={"name": "stranger", "password": "s3cret"})
        stranger_id = r.json()["id"]
        r = client.get("/api/console/media")
        per_user = r.json()["items"][0]["per_user"][str(stranger_id)]
        check(per_user["visible"] is False, "the stranger does not see the item")

        print("\n== the public player is password protected ==")
        client.post("/player/logout")
        r = client.get("/api/player/playlist")
        check(r.status_code == 401, f"the playlist refuses an anonymous caller ({r.status_code})")
        r = client.get("/player", follow_redirects=False)
        check(r.status_code == 302 and r.headers.get("location") == "/player/login",
              "the player redirects an anonymous visitor to sign in")
        r = client.post("/player/login", data={"username": "family", "password": "wrong"})
        check(r.status_code == 401 and 'id="player-startup"' not in r.text,
              "a wrong password is refused without showing the player")
        check('id="login-submit" type="submit"' in r.text and 'role="alert"' in r.text,
              "a failed sign-in returns an enabled form with an error")
        check(asset_url("css/app.css") in r.text, "sign-in uses the shared versioned stylesheet")
        r = client.post("/player/login", data={"username": "family", "password": "family-secret"},
                        follow_redirects=False)
        check(r.status_code == 302 and r.headers.get("location") == "/player",
              "the right password redirects to the player")
        r = client.get("/player")
        check(r.status_code == 200 and 'id="player-startup"' in r.text and 'id="player-content" hidden' in r.text,
              "the signed-in page serves the waiting screen before loading the playlist")
        check(r.headers.get("cache-control") == "no-store", "the signed-in page is not cached")
        check(all(asset_url(name) in r.text for name in (
            "css/app.css", "js/player.js", "vendor/video.min.js", "vendor/video-js.min.css",
        )), "the page requests versioned scripts and styles")

        print("\n== public playback assets ==")
        url = asset_url("vendor/video.min.js")
        packed = client.get(url, headers={"Accept-Encoding": "gzip"})
        plain = client.get(url, headers={"Accept-Encoding": "gzip;q=0, identity"})
        check(packed.status_code == plain.status_code == 200 and packed.content == plain.content,
              "compressed and identity responses deliver the same player script")
        check(packed.headers.get("content-encoding") == "gzip" and "content-encoding" not in plain.headers,
              "gzip is used only when accepted")
        check(int(packed.headers["content-length"]) < int(plain.headers["content-length"]) / 2,
              f"gzip cuts the player script from {len(plain.content):,} to {packed.headers['content-length']} bytes")
        check("immutable" in packed.headers.get("cache-control", "")
              and packed.headers.get("vary") == "Accept-Encoding" and "set-cookie" not in packed.headers,
              "versioned assets can be cached and do not refresh a private session cookie")
        r = client.get(url, headers={"Accept-Encoding": "gzip", "If-None-Match": packed.headers["etag"]})
        check(r.status_code == 304 and not r.content, "a matching asset ETag revalidates without bytes")
        r = client.get("/static/vendor/video.min.js?v=old")
        check("immutable" not in r.headers.get("cache-control", ""), "a stale version is not cached as immutable")

        r = client.get("/api/player/playlist")
        check(r.status_code == 200, "the playlist answers")
        check(r.headers.get("cache-control") == "no-store", "the per-account playlist is not cached")
        playlist = r.json()["items"]
        check(len(playlist) == 1, f"the family sees the one shared item ({len(playlist)})")
        check(playlist[0]["src"].startswith("/player/stream/"), "the item carries a stream URL")
        check(playlist[0].get("mime") == "video/mp4",
              f"the item carries its mime type ({playlist[0].get('mime')})")

        print("\n== the playlist is per user ==")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "stranger", "password": "s3cret"})
        r = client.get("/api/player/playlist")
        check(len(r.json()["items"]) == 0, "the stranger sees an empty playlist")

        print("\n== streaming ==")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "family", "password": "family-secret"})
        r = client.get(f"/player/stream/{media_id}/direct")
        check(r.status_code == 200, f"the direct stream answers ({r.status_code})")
        check(r.headers.get("accept-ranges") == "bytes", "the stream advertises Range support")
        check(r.headers.get("content-type", "").startswith("video/"), "the content type is right")
        # The type the player is told and the type the server sends must be
        # the same one — a mismatch is what makes a browser refuse the file.
        check(playlist[0].get("mime") == r.headers.get("content-type", "").split(";")[0],
              f"the type the player is told is the type the server sends "
              f"({playlist[0].get('mime')} / {r.headers.get('content-type')})")
        r = client.get(f"/player/stream/{media_id}/direct", headers={
            "Range": "bytes=0-1023", "Accept-Encoding": "gzip",
        })
        check(r.status_code == 206, f"a Range request answers 206 ({r.status_code})")
        check(len(r.content) == 1024, f"the Range request returns the right number of bytes ({len(r.content)})")
        check("content-encoding" not in r.headers and r.headers.get("cache-control") == "no-store",
              "the static asset optimization never compresses or publicly caches media")

        print("\n== an item that is not shared is not streamable ==")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "stranger", "password": "s3cret"})
        r = client.get(f"/player/stream/{media_id}/direct")
        check(r.status_code == 404, f"a user who may not see the item gets nothing ({r.status_code})")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "family", "password": "family-secret"})

        print("\n== the HLS playlist ==")
        r = client.get(f"/player/stream/{media_id}/stream.m3u8")
        check(r.status_code == 200, f"the HLS playlist answers ({r.status_code})")
        check(r.headers.get("content-type", "").startswith("application/vnd.apple.mpegurl"),
              f"the playlist is served as an HLS playlist ({r.headers.get('content-type')})")
        if r.status_code == 200:
            body = r.text
            check(body.startswith("#EXTM3U"), "it is an HLS playlist")
            check("#EXT-X-ENDLIST" in body, "it is a complete (VOD) playlist")
            check("/player/segment/" in body, "the segments point back at the application")

        print("\n== downloading a file ==")
        r = client.get(f"/player/download/{media_id}")
        check(r.status_code == 200, f"the download answers ({r.status_code})")
        disposition = r.headers.get("content-disposition", "")
        check("attachment" in disposition, f"it is an attachment ({disposition})")
        check("clip.mp4" in disposition, f"the file keeps its name ({disposition})")
        check(r.headers.get("accept-ranges") == "bytes", "the download advertises Range support")
        check(len(r.content) > 0, f"the bytes arrive ({len(r.content)})")
        r = client.get(f"/player/download/{media_id}", headers={"Range": "bytes=0-99"})
        check(r.status_code == 206, f"a resumed download answers 206 ({r.status_code})")
        check(len(r.content) == 100, f"the resumed part has the right size ({len(r.content)})")
        r = client.get("/api/player/playlist")
        entry = r.json()["items"][0]
        check(entry.get("downloadable") is True, "the playlist says the item is downloadable")
        check(entry.get("download_url") == f"/player/download/{media_id}",
              f"the playlist carries the download URL ({entry.get('download_url')})")
        check(entry.get("download_name") == "clip.mp4",
              f"the playlist carries the file name ({entry.get('download_name')})")

        print("\n== an item that is not shared cannot be downloaded either ==")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "stranger", "password": "s3cret"})
        r = client.get(f"/player/download/{media_id}")
        check(r.status_code == 404, f"the stranger gets nothing ({r.status_code})")
        client.post("/player/logout")
        client.post("/player/login", data={"username": "family", "password": "family-secret"})

        print("\n== a remote file is downloaded from the source ==")
        remote_url = "https://cdn.example.com/movies/remote-clip.mp4"
        r = client.post("/api/console/media", json={
            "title": "Remote clip", "source_type": "url", "url": remote_url,
            "group_id": movies_id, "output_web": True})
        check(r.status_code == 200, "a URL item can be added")
        remote_id = r.json()["id"]

        r = client.get(f"/player/download/{remote_id}", follow_redirects=False)
        check(r.status_code == 302, f"a remote download is a redirect ({r.status_code})")
        check(r.headers.get("location") == remote_url,
              f"the redirect points at the source ({r.headers.get('location')})")
        check(not r.content, f"the NAS sends no bytes of its own ({len(r.content)})")

        r = client.get("/api/player/playlist")
        entry = next(i for i in r.json()["items"] if i["id"] == remote_id)
        check(entry.get("downloadable") is True, "a remote file is downloadable")
        check(entry.get("download_remote") is True, "the playlist marks the item as remote")

        print("\n== a YouTube link is downloaded from the CDN ==")
        r = client.post("/api/console/media", json={
            "title": "A YouTube video", "source_type": "url",
            "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "group_id": movies_id, "output_web": True})
        check(r.status_code == 200, "a YouTube item can be added")
        yt_id = r.json()["id"]
        signed = ("https://r1---sn-abc.googlevideo.com/videoplayback"
                  "?expire=9999999999&id=abc&itag=22")
        fake_resolved(yt_id, signed)
        r = client.get(f"/player/download/{yt_id}", follow_redirects=False)
        check(r.status_code == 302, f"a YouTube download is a redirect ({r.status_code})")
        check(r.headers.get("location") == signed,
              f"the redirect points at the CDN ({r.headers.get('location')})")

        print("\n== what cannot be downloaded ==")
        r = client.post("/api/console/media", json={
            "title": "A live stream", "source_type": "url",
            "url": "https://example.com/live/stream.m3u8",
            "group_id": movies_id, "output_web": True})
        hls_id = r.json()["id"]
        r = client.get(f"/player/download/{hls_id}", follow_redirects=False)
        check(r.status_code == 409, f"an HLS stream cannot be downloaded ({r.status_code})")

        r = client.post("/api/console/media", json={
            "title": "A local file by URL", "source_type": "url",
            "url": "file:///etc/passwd", "group_id": movies_id, "output_web": True})
        scheme_id = r.json()["id"]
        r = client.get(f"/player/download/{scheme_id}", follow_redirects=False)
        check(r.status_code == 400, f"only http(s) sources are handed out ({r.status_code})")

        print("\n== the proxy mode streams the file through the NAS ==")
        server = start_file_server(MEDIA)
        port = server.server_address[1]
        os.environ["WEB_PLAYER_DOWNLOAD_MODE"] = "proxy"
        try:
            r = client.post("/api/console/media", json={
                "title": "Served through the proxy", "source_type": "url",
                "url": f"http://127.0.0.1:{port}/clip.mp4",
                "group_id": movies_id, "output_web": True})
            proxy_id = r.json()["id"]
            r = client.get(f"/player/download/{proxy_id}", follow_redirects=False)
            check(r.status_code == 200, f"the proxy answers with the file ({r.status_code})")
            check("attachment" in r.headers.get("content-disposition", ""),
                  f"the proxy adds the attachment header ({r.headers.get('content-disposition')})")
            check(len(r.content) > 0, f"the bytes arrive ({len(r.content)})")
        finally:
            os.environ["WEB_PLAYER_DOWNLOAD_MODE"] = "redirect"
            server.shutdown()
            server.server_close()

        print("\n== the file browser ==")
        r = client.get("/api/console/browse")
        check(r.status_code == 200, f"the browser lists the media root ({r.status_code})")
        listing = r.json()
        check(listing["path"] == "", "the browser starts at the media root")
        check(listing["parent"] is None, "there is nothing above the media root")
        check(listing["breadcrumbs"][0]["name"] == "media root",
              "the breadcrumbs start at the media root")
        names = [e["name"] for e in listing["entries"]]
        check("clip.mp4" in names, f"the clip is listed ({names})")
        clip = next((e for e in listing["entries"] if e["name"] == "clip.mp4"), None)
        check(clip is not None and clip["media"] is True, "the clip is marked as a media file")
        check(clip is not None and clip["in_library"] is True,
              "the clip is marked as being in the library")
        r = client.get("/api/console/browse?path=..%2F..")
        check(r.status_code == 400, f"the browser stays inside the media root ({r.status_code})")
        r = client.get("/api/console/browse?path=nope")
        check(r.status_code == 404, f"an unknown folder is refused ({r.status_code})")

        print("\n== a folder can be added to the library ==")
        (MEDIA / "folder").mkdir(exist_ok=True)
        shutil.copy(MEDIA / "clip.mp4", MEDIA / "folder" / "nested.mp4")
        r = client.post("/api/console/media/scan", json={"folder": "folder"})
        check(r.status_code == 200, f"a folder can be scanned ({r.status_code})")
        check(r.json()["added"] == 1, f"the file in the folder was added ({r.json()})")
        r = client.get("/api/console/browse?path=folder")
        nested = next((e for e in r.json()["entries"] if e["name"] == "nested.mp4"), None)
        check(nested is not None and nested["in_library"] is True,
              "the browser shows that the file is in the library now")
        r = client.post("/api/console/media/scan", json={"folder": ".."})
        check(r.status_code == 400, f"a scan stays inside the media root ({r.status_code})")

        print("\n== the Enigma2 bouquet ==")
        r = client.get("/api/console/bouquet/preview")
        check(r.status_code == 200, f"the bouquet preview answers ({r.status_code})")
        preview = r.json()
        check(preview["entries"] == 1, f"the item is in the bouquet ({preview['entries']})")
        check("%3a" in preview["content"], "the colons in the URL are escaped")
        check(preview["filename"] in preview["bouquets_tv"],
              f"the bouquets.tv line points at the generated file ({preview['filename']})")
        check(preview["filename"].startswith("userbouquet."), "the file is a userbouquet")
        check(preview["content"].startswith("#NAME"), "the bouquet has a name")

        print("\n== a receiver ==")
        r = client.post("/api/console/receivers", json={
            "name": "Living room", "host": "192.168.1.50", "port": 21,
            "username": "root", "password": "x", "service_type": 4097,
            "bouquet_name": "web-player"})
        check(r.status_code == 200, "a receiver can be added")
        r = client.get("/api/console/bouquet/preview?receiver_id=1")
        check(r.status_code == 200, "the preview works with a receiver")
        r = client.post("/api/console/bouquet/push", json={"receiver_id": 1})
        check(r.status_code in (502, 500), f"pushing to a box that is not there fails cleanly ({r.status_code})")
        check("could not reach" in r.text.lower(), f"the error explains itself ({r.text[:120]})")

        print("\n== the overview ==")
        r = client.get("/api/console/overview")
        check(r.status_code == 200, "the dashboard answers")
        data = r.json()
        check(data["media_count"] == 7, f"the dashboard counts the media ({data['media_count']})")
        check(data["user_count"] == 3, f"the dashboard counts the users ({data['user_count']})")
        check("log_counts" in data, "the dashboard counts the trouble in the log")
        check(sum(data["log_counts"].values()) > 0, "the log holds the lines of this test run")

        print("\n== a disabled user loses access at once ==")
        client.put(f"/api/console/users/{family_id}", json={"enabled": False})
        r = client.get("/api/player/playlist")
        check(r.status_code == 401, f"the disabled account is refused ({r.status_code})")
        client.put(f"/api/console/users/{family_id}", json={"enabled": True})

        print("\n== the log ==")
        r = client.get("/api/console/logs")
        check(r.status_code == 200, f"the log answers ({r.status_code})")
        data = r.json()
        check(isinstance(data.get("records"), list), "the log brings its lines")
        check(bool(data.get("records")), f"there is something to read ({len(data.get('records', []))} lines)")
        first = data["records"][0]
        check(all(key in first for key in ("ts", "level", "logger", "msg")),
              f"a line has a time, a level, a source and a text ({sorted(first)})")
        access = [x for x in data["records"] if x["logger"] == "web-player.access"]
        check(bool(access), "every request is logged")
        if access:
            check("/api/player/playlist" in access[-1]["msg"],
                  f"the request is named ({access[-1]['msg'][:70]})")
        check("web-player.log" in json.dumps(data.get("file", {})),
              "the log file is named")
        check(bool(data.get("counts")), "the levels are counted")
        r = client.get("/api/console/logs?level=ERROR")
        check(r.status_code == 200, "the log can be filtered")
        check(all(x["level"] == "ERROR" for x in r.json()["records"]),
              "only the wanted level comes back")
        r = client.get("/api/console/logs?search=playlist")
        check(r.status_code == 200, "the log can be searched")
        check(all("playlist" in x["msg"].lower() for x in r.json()["records"]),
              "only the lines that match come back")

        r = client.get("/api/console/logs/download")
        check(r.status_code == 200, f"the log file can be downloaded ({r.status_code})")
        check("attachment" in r.headers.get("content-disposition", ""),
              "it is an attachment")
        check(len(r.content) > 0, f"the file has content ({len(r.content)} bytes)")

        r = client.delete("/api/console/logs")
        check(r.status_code == 200, f"the log can be cleared ({r.status_code})")
        check(r.json().get("removed", 0) > 0, "the lines are gone")
        r = client.get("/api/console/logs")
        check(len(r.json()["records"]) == 0, "the buffer is empty")

        client.post("/console/logout")
        r = client.get("/api/console/logs")
        check(r.status_code == 401, f"the log is private ({r.status_code})")
        client.post("/console/login", data={"username": "admin", "password": "admin-secret"})

        check_hls_seek(client, movies_id, family_id)
        check_vaapi_pipeline(client, media_id, family_id)

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n" + ("ALL TESTS PASSED" if failures == 0 else f"{failures} FAILURE(S)"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
