#!/usr/bin/env python3
"""End-to-end test of the web-player application.

Runs against a throw-away data directory and a throw-away media directory, so
it never touches the real installation.

    .venv/bin/python tools/test_app.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="web-player-test-"))
DATA = TMP / "config"
MEDIA = TMP / "media"
DATA.mkdir(parents=True)
MEDIA.mkdir(parents=True)

# A small clip so the streaming paths have something to serve.
SAMPLE = ROOT / "mockup" / "media" / "coffee-and-code.mp4"
if SAMPLE.exists():
    shutil.copy(SAMPLE, MEDIA / "clip.mp4")
else:
    (MEDIA / "clip.mp4").write_bytes(b"not a real video - the probe will fail, that is fine")

os.environ.update({
    "WEB_PLAYER_DATA_DIR": str(DATA),
    "WEB_PLAYER_MEDIA_ROOT": str(MEDIA),
    "WEB_PLAYER_ADMIN_PASSWORD": "admin-secret",
    "WEB_PLAYER_BASE_URL": "http://nas.local:8088",
    "WEB_PLAYER_LOG_LEVEL": "WARNING",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

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
        r = client.post("/player/login", data={"username": "family", "password": "wrong"})
        check(r.status_code == 401, "a wrong password is refused")
        r = client.post("/player/login", data={"username": "family", "password": "family-secret"},
                        follow_redirects=False)
        check(r.status_code in (302, 307), "the right password signs in")
        r = client.get("/api/player/playlist")
        check(r.status_code == 200, "the playlist answers")
        playlist = r.json()["items"]
        check(len(playlist) == 1, f"the family sees the one shared item ({len(playlist)})")
        check(playlist[0]["src"].startswith("/player/stream/"), "the item carries a stream URL")

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
        r = client.get(f"/player/stream/{media_id}/direct", headers={"Range": "bytes=0-1023"})
        check(r.status_code == 206, f"a Range request answers 206 ({r.status_code})")
        check(len(r.content) == 1024, f"the Range request returns the right number of bytes ({len(r.content)})")

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
        check(data["media_count"] == 1, f"the dashboard counts the media ({data['media_count']})")
        check(data["user_count"] == 3, f"the dashboard counts the users ({data['user_count']})")

        print("\n== a disabled user loses access at once ==")
        client.put(f"/api/console/users/{family_id}", json={"enabled": False})
        r = client.get("/api/player/playlist")
        check(r.status_code == 401, f"the disabled account is refused ({r.status_code})")
        client.put(f"/api/console/users/{family_id}", json={"enabled": True})

    shutil.rmtree(TMP, ignore_errors=True)
    print("\n" + ("ALL TESTS PASSED" if failures == 0 else f"{failures} FAILURE(S)"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
