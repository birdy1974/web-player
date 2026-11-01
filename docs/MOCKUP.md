# web-player — design proposal, data model & UI mock-up (v0.1)

> **Status:** the UI is mocked up and interactive (`mockup/`), the database design is proposed below,
> the code is **not** written yet. Open questions are in §7 — answer those and the implementation starts.
>
> **See the UI:** `mockup/index.html` (served by `tools/devserver.py`, live preview in this session).

---

## 1. What I verified before designing anything

| Check | Result | What it changes for the design |
|---|---|---|
| `birdy1974/vu-movie` (Node/Express + Postgres) | A **scraper → relay → transcode** engine: it turns a scraped URL into *one* stable MPEG-TS that VLC, a browser and a VU+ Duo2 can all play, publishes it as an **Enigma2 bouquet**, and ships a browser player at `/player` with a poster grid, filters, auto-play-next and live logs. Its Enigma2 half (`src/enigma2/index.js`) builds the `.tv` file, writes it over **FTP** to `/etc/enigma2/` and asks the box to reload. | Reuse the **bouquet format** and the **relay/transcode** pattern; do **not** reuse the scraper (this project starts from files and links the admin already has). |
| `birdy1974/stalker-proxy-manager` (FastAPI + SQLAlchemy async + Postgres + Jinja + Bootstrap) | Much closer to this request: **users** with a **per-type group whitelist** (`User.groups_json = {live:[],vod:[],series:[],local:[]}`), a **local-file library**, a **public web player** (`/player`, signed session cookie, per-user playlist, template picker), and **bouquet rendering** (`app/services/enigma2_bouquets.py`) with `userbouquet.*.tv` / `subbouquet.*.tv` namespacing, `#SERVICE`/`#DESCRIPTION` pairs and `1:64:…` marker separators. | Reuse the **application shape** (FastAPI, SQLAlchemy async, Jinja templates, `/player` router), the **enigma2 renderer** and the **per-user visibility** idea — but fix the parts listed in §6. |
| The reference stores the web-player **password in plain text** (`User.password: Mapped[str]`) | Needed there, because the same password is handed to VLC in an M3U URL. | A web player does **not** need that — unless we also ship M3U/Xtream output. See **M5**. |
| The reference keeps the group whitelist as **JSON inside the user row** | A JSON column cannot be foreign-keyed; renaming a group leaves dangling entries. The reference had to grow a `groups_stale` repair to hide them. | Use real tables. See **M6**. |
| Enigma2 service reference | `<player>:0:1:<sid>:0:0:0:0:0:0:<url>:<name>` — every `:` **inside** the URL must be `%3a`, the display name may not contain a colon, the 4th field is a **stable** SID (the reference puts the playlist row id there so favourites/picons/EPG keep pointing at the same service), and `bouquets.tv` is **edited, never overwritten**. Player numbers: `1` DVB, `4097` servicemp3/GStreamer, `5001` gstplayer, `5002` exteplayer3. | Copied verbatim into the bouquet renderer. See the "How the bouquet is built" panel in the mock-up. |
| DS918+ (Celeron J3455, Apollo Lake, HD Graphics 500) | VAAPI **decode**: H.264, HEVC 8/10-bit, VP9. VAAPI **encode**: **H.264 only**. 4 GB RAM, 1–2 concurrent transcodes at most. | HLS is the universal fallback; a real transcode is the last resort. |
| Browser playback | A browser plays **MP4/H.264** and **WebM/VP8-9** — nothing else. **MKV, HEVC, AVI, MP4/HEVC are not playable** in `<video>`. | Most NAS movies need a server-side path. See **M3**. |
| Player libraries | **Video.js** (Apache-2.0, ~200 KB): the most deployed open-source player — built-in **HLS (VHS)** and DASH, plugins, skins, chapters, playback-rate, PiP, keyboard control. **Plyr** (MIT, ~50 KB): polished, but no HLS engine and no plugin scene. **Shaka Player** (Apache-2.0): the best streaming engine, but a basic UI and a heavy setup. | **Video.js** as the player core (the mock-up already runs it), **hls.js** only as a fallback if VHS is not wanted, and the YouTube IFrame API for YouTube items. See §4. |

---

## 2. The mock-up (this is what you can test now)

```
tools/devserver.py      # static server with HTTP Range support (needed for seeking)
mockup/
  index.html            # entry page
  console.html          # ① management console  (private)
  player.html           # ② public web player  (password protected)
  assets/fake-data.js   # the fake "database" — same shape as the proposed schema
  assets/console.js     # console logic
  assets/player.js      # player logic (Video.js 8.24.2, vendored)
  media/                # generated demo clips (git-ignored)
docs/MOCKUP.md          # this document
```

**Run it:**

```bash
python3 tools/devserver.py --root mockup --port 8088
# → http://<host>:8088/
```

**What to try**

*Console* — Dashboard (the computed "who can watch what" table) · Media library (the
per-user grid: click a cell to cycle *group → grant → hide → group*, toggle the ▶web / 📺e2
outputs) · Users (groups + per-item exceptions) · Enigma2 (bouquet preview + push, including
a *one bouquet per user* layout) · Settings.

*Player* — sign in with any password as `family`, `kids` or `guest` (each sees a different
playlist) · search the playlist · play · jump to a timestamp (`01:23:45`) · ±10 s · keyboard
shortcuts · resume · **download the file** (the ⬇ button: the original file, not the
transcoded stream — a file on the NAS is streamed by the NAS, a remote file is
redirected to its source so the browser downloads it from there).

The demo clips are generated with ffmpeg (test patterns), so the seek really works: the dev
server answers `Range:` requests, which is exactly what the real backend must do.

---

## 3. Architecture

```
                   ┌──────────────────── Synology DS918+ (DSM 7, Container Manager) ────────────────────┐
  browser  ───────►│  web-player  (python:3.12-slim + ffmpeg + nginx)                                 │
  VU+ box  ───────►│                                                                                    │
                   │   ┌──────────────┐   /console/*   private management console (admin session)    │
                   │   │  FastAPI app │   /player/*    public web player  (user session)              │
                   │   │  SQLAlchemy  │   /api/media   library + probe + per-user visibility         │
                   │   │  async       │   /api/stream  direct (Range) · on-demand HLS · yt-dlp        │
                   │   └──────┬───────┘   /api/e2      bouquet render + FTP push + servicelistreload  │
                   │          │           /player/api  playlist of the signed-in user only            │
                   │   ┌──────▼───────┐   /media/…     local files (Range)                              │
                   │   │  SQLite      │  one file: /config/web-player.db                              │
                   │   └──────────────┘   /dev/dri ──► Intel VAAPI (H.264 encode)                      │
                   │   ┌──────────────┐                                                          │
                   │   │  ffmpeg      │  remux → HLS (seekable) · VAAPI transcode → 1080p H.264        │
                   │   └──────────────┘                                                          │
                   └───────────────────────────────────────────────────────────────────────────────┘
```

One image, nginx in front of uvicorn, so the media directory can be served with `sendfile` +
`Range` support while the API stays on FastAPI. **One container** (no database container —
SQLite lives in the `/config` volume), which is what a DS918+ with 4 GB RAM wants.

**VAAPI on the DS918+ (J3455, HD Graphics 500):** decode H.264/HEVC/VP9, **encode H.264
only**. So the on-demand HLS path is: *copy* when the source is H.264 (0 % CPU), *VAAPI
transcode* to 1080p H.264 when the source is HEVC/VP9 or the browser refuses the container.
`/dev/dri` is passed into the container and the `render` group is joined at start-up.

---

## 4. The public web player: which library, and why

| Option | Verdict |
|---|---|
| **Video.js 8** — **chosen** | Apache-2.0, vendored (no CDN, works offline on the NAS), HLS built in via VHS, plugin ecosystem (chapters, quality levels, markers), full keyboard control, and it degrades to the native `<video>` for plain MP4. The mock-up already runs it, so the UX you test now is the UX you get. |
| hls.js alone | Lighter, but it is an *engine*, not a player — we would hand-build the whole control bar. Only worth it if the image size matters more than the UI. |
| Plyr | Nicest default skin, but no HLS engine and no plugins; we would lose quality selection and chapters. |
| Shaka Player | The strongest engine (DASH + HLS + DRM), but a bare UI and a heavy setup — a lot of work for DRM we do not need. |
| YouTube IFrame API | The only sane way to play a YouTube link in the browser without running `yt-dlp`. It is a *different player*: its own controls, its own ads, and it cannot be pushed to the Enigma2 box. See **M4**. |

**Delivery modes** (the item decides, the admin can override it):

| Mode | When | Seeking | CPU |
|---|---|---|---|
| `direct` | MP4/H.264 (or any browser-native format), and every `yt-dlp`-resolved link | native, exact, via HTTP `Range` | 0 % |
| `hls` | MKV / HEVC / AVI / anything the browser refuses | segment-accurate (4 s default) | ffmpeg **copy** for H.264, **VAAPI transcode** to 1080p H.264 for HEVC/VP9 |
| `embed` | *(fallback)* a YouTube link that `yt-dlp` cannot resolve — the item is shown with the YouTube IFrame player instead | by YouTube | 0 % |

**YouTube (D4 = resolve with `yt-dlp`).** The resolver runs when the admin adds the link
(background job) and on demand when the resolved URL has expired. The result is cached in
`media.resolved_url`; the console shows the resolution state (`resolved 2 h ago`, `failed`,
`re-resolve`). A resolved link is an ordinary item: it plays in our player, it seeks, **and it
can be written into the Enigma2 bouquet** — which is exactly what the IFrame-embed approach
could never do. If the resolution fails, the item falls back to the embed mode instead of
disappearing.

---

## 5. Proposed database (SQLite)

```
users                id · name · password_hash · role(admin|user) · enabled · max_streams
                     · expires_at · last_login_at · created_at
groups               id · name · color · sort_order
media                id · title · year · kind(movie|series|clip|audio) · group_id→groups
                     · source_type(local|url|youtube) · path · url · poster · description
                     · duration_s · size_bytes · enabled · sort_order
                     · delivery(auto|direct|hls) · probe(json) · resolved_url · resolved_at
                     · outputs(json {"web":bool,"enigma2":bool}) · created_at · updated_at
user_groups          user_id→users · group_id→groups · PRIMARY KEY(user_id, group_id)
user_media           user_id→users · media_id→media · state(allow|deny) · PRIMARY KEY(user_id, media_id)
receivers            id · name · host · port · transport(ftp|ssh|download) · username · password_enc
                     · service_type(1|4097|5001|5002) · bouquet_name · enabled
bouquets             id → receivers · name · item_count · pushed_at · payload(text of the .tv file)
playback_progress    user_id→users · media_id→media · position_s · updated_at   (PRIMARY KEY user+media)
settings             key · value(json) · updated_at
```

The mock-up's `fake-data.js` has exactly these objects with exactly these fields, so what you
click in the mock-up is what the tables will hold.

**SQLite notes.** The database is one file in the `/config` volume
(`/config/web-player.db`): `docker cp`/DSM file explorer is the whole backup procedure, and
there is no second container eating RAM on a 4 GB NAS. WAL mode is on, so a reader never
blocks the writer. `AUTOINCREMENT` is avoided (SQLite's `rowid` is enough) and every
foreign key is declared with `ON DELETE CASCADE` plus `PRAGMA foreign_keys=ON`. The schema is
plain SQL, so moving to PostgreSQL later is a rewrite of the DDL and nothing else — the ORM
layer is the same SQLAlchemy models.

---

## 6. ⚠ Where the proposed database and the GUI disagree

These are the real problems. Each one is a decision, not a bug.

### M1 — "enable/disable every file per user" does not scale to a GUI
The literal requirement is an N×M matrix: 2 000 items × 15 users = 30 000 rows and a
2 000-column table in the browser. Nobody can operate that.
*Reference:* `stalker-proxy-manager` solves it with **named groups** (`User.groups_json`) — 5 chips
per user instead of a matrix.
**Options:** **(a)** groups only · **(b)** groups + per-item exceptions *(recommended — the
mock-up does this: the grid shows the computed result, a click writes one exception row)* ·
**(c)** the full matrix with pagination and "select all visible".

### M2 — "choose the output: web player and/or enigma2" is not a per-user decision
A bouquet lives in `/etc/enigma2/userbouquet.*.tv` **on the box** and is global for that
receiver. There is no "per web-user" concept on the box, so a per-user output flag has nothing
to attach to.
*Reference:* `vu-movie` pushes **one** bouquet; `stalker-proxy-manager` supports several prefixes
and a bouquet per profile.
**Options:** **(a)** one global bouquet from all items with `output.enigma2` on · **(b)** one
bouquet **per user**, filled with exactly the items that user may watch — this is what makes
"the user's playlist" and "the box's playlist" the same thing *(the mock-up has this as a
layout)* · **(c)** both: a global bouquet + optional per-user bouquets.

### M3 — "jump to a specific timestamp" implies every item is seekable — it is not
The `media` table stores a path or a URL; the browser can only seek what the server serves
with `Range`. MKV/HEVC/AVI cannot be played at all, and a remote URL may refuse `Range`.
*Reference:* `stalker-proxy-manager` probes every local file with `ffprobe` and stores the result;
`vu-movie` offers a progressive link *and* a live `.ts` that cannot seek.
**Impact:** the GUI must show a per-item **playability** state (Direct / HLS / Embed / unknown)
and the console must warn about items that need CPU. The DB needs `delivery` + `probe`.
**Options:** **(a)** on-demand HLS for everything the browser refuses *(recommended)* ·
**(b)** always HLS · **(c)** refuse non-playable items.

### M4 — a YouTube item cannot be "an item"
Two different players: the YouTube IFrame player (its own UI, its own ads, works only in a
browser, **cannot** be pushed to the Enigma2 box) versus our own player.
**Options:** **(a)** IFrame embed only *(simplest, no `yt-dlp`, no breakage)* · **(b)** resolve
with `yt-dlp` into a direct stream we serve ourselves — then it *is* an item like any other,
but the resolver breaks whenever YouTube changes and the legality is yours ·
**(c)** both, chosen per item in the console.

### M5 — password storage vs. extra outputs
`stalker-proxy-manager` keeps the password **in plain text** because the same password is
handed to VLC in an M3U URL (`/playlist.m3u?u=…&p=…`). A web player alone does not need that.
**Options:** **(a)** hash only — no M3U/Xtream output · **(b)** hash + a separate plain-text
field, filled only when M3U output is switched on · **(c)** plain text like the reference.
*(The mock-up shows the M3U/Xtream output **off** by default.)*

### M6 — a group whitelist stored as JSON on the user row
`User.groups_json` cannot be foreign-keyed: renaming a group leaves dangling entries, and the
reference needed a `groups_stale` repair to hide them in the editor.
**Decision:** real `groups` + `user_groups` + `user_media` tables (see §5). *No user input
needed — I will do it this way unless you object.*

### M7 — playlist order is in the GUI but nowhere in the data
The player offers "Playlist order" and the console shows a sort column, but a group whitelist
has no order at all. **Decision:** `media.sort_order` (one global order) plus an optional
per-user order table later. *The mock-up uses a global order — drag-and-drop is not implemented
in the mock-up, the number field is.*

### M8 — "resume where I left off" needs a table nobody has
`vu-movie` keeps the resume point **in memory** (lost on restart); `stalker-proxy-manager` does
not have it at all. The mock-up shows it in the playlist. **Decision:** `playback_progress`
(user + media + position). *No user input needed.*

### M9 — the SID must be stable, and unique per bouquet
Enigma2 silently drops duplicate service references, and the SID is what keeps the user's
favourites, picons and EPG mapping pointing at the same channel after a re-push.
**Decision:** the SID is a hash of `(user, media_id)` — stable across pushes, and different per
user so two users' bouquets do not collide. *Implemented in the mock-up's preview.*

### M10 — the console must show the **computed** visibility, not the raw flags
Visibility is `global enabled ∩ output.web ∩ (group allowed ∪ per-item grant) ∩ not per-item
denied`. A console that shows only one of those is misleading — the reference's `groups_stale`
is exactly this bug showing up.
**Decision:** the media grid shows the computed state per user (✓ / G / · / ×) and the
"needs attention" panel lists items that are enabled but unreachable. *Implemented in the
mock-up.*

### M11 — probing 2 000 files takes time
`ffprobe` on a NAS over SMB is slow; the console cannot block on it.
**Decision:** the probe is a background job, the item shows `probing…` until it is done, and
the duration is filled in afterwards (the reference does the same with `fill_local_durations`).

### M12 — a URL item needs two URLs
The URL the admin typed is not necessarily the URL the box can play (a YouTube page is not a
media URL; a panel link may need headers). The bouquet needs the **resolved** media URL.
**Decision:** `media.url` (what the admin typed) and `media.resolved_url` (what we serve).
*Not visible in the mock-up beyond the `delivery` field.*

---

## 7. Decisions taken

| # | Decision | Choice | Consequence for the code |
|---|---|---|---|
| **D1** | Per-user playlist control | **groups + per-item exceptions** | `groups` + `user_groups` + `user_media` tables. The console grid shows the computed result; a click writes one exception row. |
| **D2** | Enigma2 output | **one global bouquet** | A single `userbouquet.web-player.tv` with every item whose `outputs.enigma2` is on. No per-user bouquets (the code keeps the layout switch, so it can be turned on later). |
| **D3** | Items the browser cannot play | **on-demand HLS, with VAAPI** | H.264 sources are **copied** (0 % CPU); HEVC/VP9/odd containers are **transcoded with Intel VAAPI** to 1080p H.264. `/dev/dri` is required for the transcode path; a software fallback (libx264) keeps it working without the device. |
| **D4** | YouTube | **`yt-dlp` resolves it into our own stream** | A YouTube link becomes a normal item: our player, our controls, our seek bar — **and** it can be pushed to the Enigma2 bouquet. The resolver runs in the background, the result is cached in `media.resolved_url`, and a failed resolution is retried on demand and reported in the console. yt-dlp is asked for the best **single** file that has audio *and* video (`b[ext=mp4]/b`), because a redirect can only point at one URL. |
| **D5** | Extra outputs | **web player only** | Passwords are **hashed** (Argon2/bcrypt) and never stored in plain text. No M3U, no Xtream Codes. |
| **D6** | Database | **SQLite** | One file in the `/config` volume. No second container, ~0 MB extra RAM, backup = copy one file. The schema stays PostgreSQL-portable. |
| **D7** | UI stack | **plain server-rendered HTML + vanilla JS** (decided by me, unless you object) | No build step in the image, no node_modules at runtime, easy to debug on a NAS. Video.js is vendored as a static file. |
| **D8** | VAAPI | **on, with a software fallback** (decided by me) | The container detects `/dev/dri` at start-up and says so in the log; without it, libx264 takes over. |
| **D9** | Downloading a remote file | **a redirect to the source** (2026-11-01) | A file on the NAS is streamed by the NAS (our name, `Range`, resumable). A YouTube link or a URL is **not** copied through the NAS: the browser is redirected to the source and downloads it from there, so the NAS uses no bandwidth. The source then also names the file (`videoplayback.mp4` for YouTube) and decides whether a download can be resumed — a browser drops our `download` attribute as soon as the download leaves the origin. `WEB_PLAYER_DOWNLOAD_MODE=proxy` switches back to the NAS streaming it. |

**Open items that are *not* decisions but consequences to be aware of:**

* **yt-dlp needs to be kept current.** It breaks whenever YouTube changes. The console shows the
  resolution state and the age of the resolved URL, and a "re-resolve" button.
* **VAAPI on a DS918+ encodes H.264 only** — a 4K HEVC source becomes 1080p H.264. One
  concurrent transcode; a second one waits in a queue.
* **A global bouquet means every user on the box sees the same list** — the bouquet is not
  filtered per user (D2). The web player *is* filtered per user.
* **HLS seeking is accurate to one segment** (4 s by default, configurable in Settings).

---

## 8. What happens next

1. **Answer §7** → the schema is frozen and the API is written down.
2. `Dockerfile` + `docker-compose.yml` (`web-player` + `postgres`), DSM Container Manager
   instructions, `/volume1/media` bind mount, PUID/PGID, `/dev/dri` for VAAPI.
3. **Backend**: FastAPI + SQLAlchemy async + Postgres, migrations on boot, the admin API
   (media, users, groups, receivers, bouquets) and the player API (per-user playlist, stream URLs).
4. **Media pipeline**: library scan + `ffprobe`, `direct` delivery with `Range`, on-demand HLS
   with a segment index, VAAPI transcode fallback.
5. **Front-end**: the mock-up turned into the real pages (console + player), Video.js wired to
   the real stream endpoints.
6. **Enigma2**: bouquet renderer (from the reference), FTP/SSH push, `servicelistreload`,
   `bouquets.tv` editing, verify step.
7. **Packaging**: build on the DS918+ (or `docker buildx` for `linux/amd64`), a `docker-compose.yml`
   that Container Manager imports as a *project* named **web-player**.
