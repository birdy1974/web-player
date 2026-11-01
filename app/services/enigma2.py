"""web-player — Enigma2 bouquets.

Based on the two reference projects:

* `birdy1974/vu-movie` → `src/enigma2/index.js` — the service-reference format
  and the FTP push to `/etc/enigma2/`;
* `birdy1974/stalker-proxy-manager` → `app/services/enigma2_bouquets.py` — the
  `userbouquet.*.tv` / `subbouquet.*.tv` namespacing, the `#SERVICE` +
  `#DESCRIPTION` pairs and the `1:64:…` marker separators.

WHAT A BOUQUET LOOKS LIKE

    #NAME web-player (TV)
    #SERVICE 4097:0:1:0A2F:0:0:0:0:0:0:http%3a//nas%3a8088/player%2fstream%2f12.ts:Some Movie
    #DESCRIPTION Some Movie

The details that bite:

* Enigma2 splits a service reference on ":", so every colon **inside** the URL
  must be `%3a` and the display name after the last colon must not contain
  one at all.
* The 4th field is the SID. It must be **stable** across pushes, or the box
  loses the user's favourites, picons and EPG mapping for that channel. We
  derive it from the media id.
* Every `#SERVICE` needs its own `#DESCRIPTION`.
* `1:64:…::Label` is a MARKER — a non-playable separator line.
* The leading number picks the player on the box: `1` DVB pipeline, `4097`
  servicemp3/GStreamer, `5001` ServiceApp gstplayer, `5002`
  ServiceApp/exteplayer3 (the one that demuxes Matroska itself).
* `bouquets.tv` is **edited, never overwritten** — our line is replaced and
  every other bouquet on the box is kept.
"""
from __future__ import annotations

import ftplib
import io
import logging
import re
import shlex
from dataclasses import dataclass

from ..models import Media

log = logging.getLogger("web-player.enigma2")

E2_DIR = "/etc/enigma2"
BOUQUETS_TV = "bouquets.tv"
MARKER_REF = "1:64:0:0:0:0:0:0:0:0:"

PLAYERS = {
    1: "DVB pipeline (raw MPEG-TS only)",
    4097: "servicemp3 / GStreamer (the safe default)",
    5001: "ServiceApp - gstplayer",
    5002: "ServiceApp - exteplayer3 (demuxes Matroska, can show soft subtitles)",
}

CONTAINERS = ("ts", "mkv")


def slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return out or "x"


def escape_url(url: str) -> str:
    """A URL as an Enigma2 service-reference field.

    Only two characters may not survive: ":" ends the field and whitespace
    ends the line. Everything else (including "?" and "&") is passed through.
    """
    return str(url).replace(":", "%3a").replace(" ", "%20")


def clean_name(name: str) -> str:
    """A display name for a service line: one line, no colon."""
    out = re.sub(r"\s+", " ", (name or "").replace(":", " -")).strip()
    return out or "Unnamed"


def sid_for(seed: str) -> int:
    """A stable SID (the 4th field) for a bouquet entry."""
    h = 0
    for ch in str(seed):
        h = (h * 31 + ord(ch)) % 0xFFFF
    return h or 1


def service_line(player: int, sid: int, url: str, name: str) -> list[str]:
    ref = (f"{player}:0:1:{sid:X}:0:0:0:0:0:0:"
           f"{escape_url(url)}:{clean_name(name)}")
    return [f"#SERVICE {ref}", f"#DESCRIPTION {clean_name(name)}"]


def marker_line(label: str) -> list[str]:
    nm = clean_name(label)
    return [f"#SERVICE {MARKER_REF}:{nm}", f"#DESCRIPTION {nm}"]


def bouquet_filename(prefix: str) -> str:
    """Top-level bouquets live in the `userbouquet.` namespace."""
    return f"userbouquet.{slugify(prefix)}.tv"


def bouquets_tv_line(filename: str) -> str:
    return (f'#SERVICE 1:7:1:0:0:0:0:0:0:0:'
            f'FROM BOUQUET "{filename}" ORDER BY bouquet')


@dataclass
class Entry:
    media_id: int
    title: str
    url: str
    group: str
    kind: str
    delivery: str


def build_bouquet(*, name: str, service_type: int, entries: list[Entry],
                  group_markers: bool = True) -> tuple[str, str, int]:
    """Return (file content, bouquets.tv line, entry count)."""
    lines = [f"#NAME {clean_name(name)} (TV)"]
    used: set[str] = set()
    last_group = None
    count = 0

    for entry in entries:
        if group_markers and entry.group != last_group:
            last_group = entry.group
            lines += marker_line(entry.group)
        ref_lines = service_line(service_type, sid_for(str(entry.media_id)),
                                 entry.url, entry.title)
        # Enigma2 silently drops duplicate service references.
        if ref_lines[0] in used:
            ref_lines[0] = f"{ref_lines[0]}{count}"
        used.add(ref_lines[0])
        lines += ref_lines
        count += 1

    filename = bouquet_filename(name)
    return "\n".join(lines) + "\n", bouquets_tv_line(filename), count


# --------------------------------------------------------------------------
# pushing
# --------------------------------------------------------------------------
class PushError(RuntimeError):
    pass


def _ftp_connect(host: str, port: int, user: str, password: str, timeout: int = 20):
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=timeout)
    ftp.login(user, password or "")
    return ftp


def push_ftp(*, host: str, port: int, username: str, password: str,
             filename: str, content: str, bouquets_line: str,
             backup: bool = True) -> dict:
    """Write the bouquet file (and update bouquets.tv) on the receiver.

    The file is staged under a temporary name and renamed in place, and the
    previous bouquets.tv is kept as one restore point.
    """
    written = []
    try:
        ftp = _ftp_connect(host, port, username, password)
    except (OSError, ftplib.Error) as exc:
        raise PushError(f"could not reach {host}:{port} — {exc}") from exc

    try:
        ftp.cwd(E2_DIR)

        # bouquets.tv: edit, never overwrite.
        existing = ""
        try:
            with io.BytesIO() as buf:
                ftp.retrbinary(f"RETR {BOUQUETS_TV}", buf.write)
                existing = buf.getvalue().decode("utf-8", "replace")
        except ftplib.error_perm:
            existing = ""

        new_bouquets = _merge_bouquets_tv(existing, bouquets_line)
        if backup and existing:
            _try_stow(ftp, BOUQUETS_TV, existing)
        _put(ftp, BOUQUETS_TV, new_bouquets)
        written.append(BOUQUETS_TV)

        _put(ftp, filename, content)
        written.append(filename)
    except ftplib.Error as exc:
        raise PushError(f"FTP error while writing the bouquet: {exc}") from exc
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass
    return {"written": written, "transport": "ftp"}


def _merge_bouquets_tv(existing: str, our_line: str) -> str:
    """Replace our line, keep every other bouquet on the box."""
    lines = existing.splitlines()
    target = our_line.split("FROM BOUQUET ")[-1]
    out, replaced = [], False
    for line in lines:
        if "FROM BOUQUET " in line and target in line:
            out.append(our_line)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(our_line)
    return "\n".join(out).rstrip() + "\n"


def _put(ftp, name: str, content: str) -> None:
    tmp = f".{name}.web-player.tmp"
    data = content.encode("utf-8")
    ftp.storbinary(f"STOR {tmp}", io.BytesIO(data))
    try:
        ftp.rename(tmp, name)
    except ftplib.error_perm:
        # Some receivers refuse STOR-then-rename; write directly instead.
        ftp.storbinary(f"STOR {name}", io.BytesIO(data))
        try:
            ftp.delete(tmp)
        except ftplib.Error:
            pass


def _try_stow(ftp, name: str, content: str) -> None:
    try:
        ftp.storbinary(f"STOR .{name}.web-player.bak", io.BytesIO(content.encode("utf-8")))
    except ftplib.Error:
        pass


def push_download(*, host: str, port: int, username: str, password: str,
                  filename: str, content: str, bouquets_line: str,
                  backup: bool = True) -> dict:
    """Write the bouquet over the receiver's HTTP interface (OpenWebif-style).

    Used when the box exposes no FTP server; the files are POSTed to a small
    endpoint. Kept separate so a deployment can choose.
    """
    import httpx

    base = f"http://{host}:{port}"
    auth = (username, password or "")
    written = []
    async def _do():
        async with httpx.AsyncClient(timeout=30, auth=auth) as client:
            r = await client.post(f"{base}/api/bouquet", data={
                "filename": filename, "content": content})
            r.raise_for_status()
            written.append(filename)
            r = await client.post(f"{base}/api/bouquets-tv", data={
                "content": bouquets_line})
            r.raise_for_status()
            written.append(BOUQUETS_TV)
    try:
        asyncio_run(_do())
    except Exception as exc:  # noqa: BLE001
        raise PushError(f"could not write to {base}: {exc}") from exc
    return {"written": written, "transport": "download"}


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def servicelist_reload_url(host: str, port: int = 80) -> str:
    return f"http://{host}:{port}/web/servicelistreload?mode=2"
