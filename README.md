# web-player

A private **management console** and a password-protected **public web player** for the files and
links on a Synology NAS — with **Enigma2 bouquet** output for a satellite box.

> **Status:** the interactive mock-up, the design proposal and the application are all in place.
> See [docs/MOCKUP.md](docs/MOCKUP.md) for the design and the decisions, and
> [docs/SYNO.md](docs/SYNO.md) for the Synology installation.

```
web-player/
├─ app/                  the application (FastAPI + SQLAlchemy async + SQLite)
│  ├─ main.py            the ASGI app, the session middleware, the health check
│  ├─ models.py          the schema
│  ├─ routers/           console.py (admin) · player.py (public + streaming)
│  ├─ services/          visibility · library · hls · resolver · enigma2
│  ├─ templates/         console.html · player.html · login.html
│  └─ static/            css · js · the vendored Video.js
├─ mockup/               the interactive prototype (fake data, no backend)
├─ docs/                 MOCKUP.md · SYNO.md
├─ tools/                devserver.py · make-demo-media.sh · the two test suites
├─ Dockerfile            python:3.12-slim + ffmpeg + the Intel VAAPI drivers + yt-dlp
└─ docker-compose.yml    one container: web-player
```

---

## What it does

**① Management console — private** (`/console`)

* The media library: local files on the NAS, direct URLs and YouTube links.
  A background `ffprobe` classifies every item as **direct** (playable as it
  is, 0 % CPU) or **HLS** (needs a remux / VAAPI transcode).
* **Per-user playlists**: groups plus per-item exceptions. The grid shows the
  *computed* result per user and one click writes an exception.
* **Output selection per item**: the public web player and/or an Enigma2 bouquet.
* **Users**: username, hashed password, groups, max simultaneous streams, expiry.
* **Enigma2**: receivers, service type, a live preview of the generated
  `userbouquet.*.tv` and a push to the box.

**② Public web player — password protected** (`/player`)

* The playlist of the signed-in user and nothing else.
* Video.js: play/pause, ±10 s, speed, picture-in-picture, fullscreen, keyboard control.
* **Jump to a timestamp** (`01:23:45`) — exact for a progressive file, accurate
  to one segment (4 s) for an on-demand HLS stream.
* **Download the file** — the button next to the title hands over the *original*
  file (an MKV stays an MKV).
  * A file on the NAS is streamed by the NAS: our file name, `Range` support,
    an interrupted download can be resumed.
  * A **remote** file (a YouTube link, a URL) is **not** copied through the
    NAS. The browser gets a redirect and downloads it from the source (the
    CDN), so the NAS uses no bandwidth for it. The source then also names the
    file (a YouTube download is called `videoplayback.mp4`) — a browser drops
    our `download` attribute as soon as the download leaves the origin.
  * `WEB_PLAYER_DOWNLOAD_MODE=proxy` switches remote downloads back to the
    NAS streaming them (our file name, but the traffic runs through the NAS).
* Search the playlist, resume where you left off, auto-play the next title.

---

## Run it on the NAS

```bash
cp .env.example .env      # set WEB_PLAYER_ADMIN_PASSWORD and WEB_PLAYER_MEDIA_HOST
docker compose up -d --build

# http://<nas-ip>:8088/console    the management console
# http://<nas-ip>:8088/player     the public web player
```

Full instructions, the VAAPI notes and the troubleshooting table: **[docs/SYNO.md](docs/SYNO.md)**.

---

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
WEB_PLAYER_DATA_DIR=/tmp/wp/config \
WEB_PLAYER_MEDIA_ROOT=/path/to/your/media \
WEB_PLAYER_ADMIN_PASSWORD=admin-secret \
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8088
```

`ffmpeg` and `ffprobe` must be on the `PATH`; without them the library still
works, but nothing can be probed, remuxed or resolved.

---

## Try the mock-up

The prototype (fake data, no backend) is still in the repository — it is how
the interface was designed and it is the fastest way to look around:

```bash
sh tools/make-demo-media.sh                       # synthetic demo clips (needs ffmpeg)
python3 tools/devserver.py --root mockup --port 8088
```

| Page | What to look at |
|---|---|
| `mockup/console.html` | Dashboard → *what each user can watch*; Media → the per-user grid (click a cell to cycle **group → grant → hide → group**) and the **▶ web / 📺 e2** output switches; Enigma2 → the bouquet preview and the push button. |
| `mockup/player.html` | Sign in as `family`, `kids` or `guest` with any password — each sees a different playlist. Search, play, jump to a timestamp, keyboard shortcuts, resume. |

---

## Tests

```bash
npm install && npm test                    # the mock-up (jsdom): both pages, driven
.venv/bin/python tools/test_app.py         # the application: the whole API, end to end
```

---

## Design

The full proposal — the analysis of the two reference projects, the
player-library research, the proposed schema, **where the schema and the GUI
disagree** and the decisions that were taken — is in
**[docs/MOCKUP.md](docs/MOCKUP.md)**.

Built on the two reference projects:

* [`birdy1974/vu-movie`](https://github.com/birdy1974/vu-movie) — the Enigma2 bouquet format and
  the relay/transcode pattern.
* [`birdy1974/stalker-proxy-manager`](https://github.com/birdy1974/stalker-proxy-manager) — the
  application shape (FastAPI + SQLAlchemy async + Jinja), the public `/player` and the
  per-user visibility model.

## Licence

MIT.
