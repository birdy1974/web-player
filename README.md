# web-player

A private **management console** and a password-protected **public web player** for the files and
links on a Synology NAS — with **Enigma2 bouquet** output for a satellite box.

> **Status: mock-up stage.** The interactive UI prototype and the data model are ready
> ([docs/MOCKUP.md](docs/MOCKUP.md)); the application code is not written yet. The open decisions
> are in §7 of that document.

---

## What it does

**① Management console (private)**

* The media library: local files on the NAS, direct URLs (mp4/m3u8/…) and YouTube links.
* Every item gets a **playability probe** (`ffprobe`): what the browser can play directly, what
  needs an on-demand HLS remux, what is an embed.
* **Per-user playlists**: the admin decides which items each user sees — by group, with
  per-item exceptions on top.
* **Output selection per item**: the public web player and/or an Enigma2 bouquet.
* **Users**: username, password (hashed), groups, max simultaneous streams, expiry date.
* **Enigma2**: receivers, service type, a live preview of the generated `userbouquet.*.tv`
  file and a push to the box.

**② Public web player (password protected)**

* The playlist of the signed-in user and nothing else.
* Video.js player: play/pause, ±10 s, speed, picture-in-picture, fullscreen, keyboard control.
* **Jump to a timestamp** (`01:23:45`).
* Search the playlist, resume where you left off, auto-play the next title.

---

## Try the mock-up now

```bash
# 1. demo clips (synthetic ffmpeg test patterns, nothing copyrighted)
sh tools/make-demo-media.sh          # needs ffmpeg on PATH

# 2. serve the mock-up (Range support, so seeking really works)
python3 tools/devserver.py --root mockup --port 8088 --host 0.0.0.0
```

Open <http://localhost:8088/>.

| Page | What to look at |
|---|---|
| `mockup/console.html` | Dashboard → *what each user can watch*; Media → the per-user grid (click a cell to cycle **group → grant → hide → group**) and the **▶ web / 📺 e2** output switches; Users → groups + per-item exceptions; Enigma2 → bouquet preview (including a *one bouquet per user* layout) and the push button. |
| `mockup/player.html` | Sign in as `family`, `kids` or `guest` with any password — each sees a different playlist. Search, play, jump to a timestamp, ±10 s, keyboard shortcuts, resume. |

Automated smoke test of the mock-up (loads both pages in jsdom and drives them):

```bash
npm install && npm test
```

---

## Design

The full design proposal — architecture, the player-library research, the proposed PostgreSQL
schema, **where the schema and the GUI disagree** and the open decisions — is in
**[docs/MOCKUP.md](docs/MOCKUP.md)**.

Built on the two reference projects:

* [`birdy1974/vu-movie`](https://github.com/birdy1974/vu-movie) — the Enigma2 bouquet format and
  the relay/transcode pattern.
* [`birdy1974/stalker-proxy-manager`](https://github.com/birdy1974/stalker-proxy-manager) — the
  application shape (FastAPI + SQLAlchemy async + Postgres + Jinja), the public `/player` and the
  per-user visibility model.

## Repository layout

```
web-player/
├─ docs/MOCKUP.md            design proposal, data model, mismatch report, open decisions
├─ mockup/                   the interactive prototype (no build step)
│  ├─ index.html  console.html  player.html
│  ├─ assets/                app.css, fake-data.js, console.js, player.js, vendor/video.js
│  └─ media/                 generated demo clips (git-ignored)
├─ tools/
│  ├─ devserver.py           static server with HTTP Range support
│  ├─ make-demo-media.sh     generates the demo clips
│  └─ mockup-smoke.cjs       jsdom smoke test of the prototype
├─ package.json
└─ requirements.md           the original request
```
