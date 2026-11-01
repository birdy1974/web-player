# web-player on a Synology DS918+ (DSM 7)

Everything below is what a **Container Manager → Project** needs. The image is
built for `linux/amd64` — the DS918+ is an Intel Celeron J3455 (Apollo Lake).

---

## 1. Before you start

| | |
|---|---|
| **DSM** | 7.0 or newer, with **Container Manager** (Package Center → install "Container Manager" on older DSM 6.2 installs) |
| **CPU / RAM** | J3455, 4 GB. The container is capped at 1 GB; ffmpeg peaks around 300 MB per transcode |
| **Ports** | 8088 (the web interface). Nothing else is opened on the LAN |
| **Disk** | the media library (read) + a small volume for the configuration (about 50 MB) |

**What the DS918+ can and cannot do with VAAPI** — this decides what your users see:

* **decode**: H.264, HEVC (8- and 10-bit), VP9, VC-1 — fine
* **encode**: **H.264 only**. There is no HEVC encoder in this chip
* so a 4K HEVC movie is **transcoded to 1080p H.264** for the browser, and a
  1080p H.264 movie is **copied** (no CPU at all)
* one transcode at a time. A second one queues behind it

Without `/dev/dri` the container still works — ffmpeg falls back to libx264
(slower, but correct). The console's *Settings → Hardware* tells you which mode
you are in.

---

## 2. Install

### 2.1 As a Container Manager project (recommended)

1. Put the repository somewhere on the NAS, e.g.
   `/volume1/docker/web-player` (via File Station or `ssh`).
2. Copy `.env.example` to `.env` and edit it:

   ```bash
   cd /volume1/docker/web-player
   cp .env.example .env
   vi .env
   ```

   At least set:

   ```ini
   WEB_PLAYER_ADMIN_PASSWORD=choose-a-real-password
   WEB_PLAYER_MEDIA_HOST=/volume1/video        # your media share
   WEB_PLAYER_BASE_URL=http://192.168.1.10:8088
   ```

3. In **Container Manager → Project → Create**, point it at that folder.
   DSM reads the `docker-compose.yml` and the `.env` next to it.
4. **Build** the project (the first build takes a few minutes: it installs
   ffmpeg and the Intel drivers).
5. Start it and open `http://<nas-ip>:8088/console`.

### 2.2 From the shell

```bash
cd /volume1/docker/web-player
cp .env.example .env && vi .env
sudo docker compose up -d --build
sudo docker compose logs -f web-player
```

### 2.3 Pull the image instead of building it on the NAS (faster)

GitHub Actions builds the image on every push to `main` and publishes it to
GitHub Container Registry. Pulling it takes seconds; building it on the NAS
takes several minutes and loads the J3455.

```bash
cd /volume1/docker/web-player
cp .env.example .env && vi .env
# in .env:
#   WEB_PLAYER_IMAGE=ghcr.io/birdy1974/web-player:latest

sudo docker compose pull
sudo docker compose up -d
sudo docker compose logs -f web-player
```

> **Pull first.** The compose file also carries a `build:` section, so a plain
> `docker compose up -d` would build the image on the NAS and tag it with the
> registry name. `docker compose pull` fetches the published image instead.

The image is built for `linux/amd64` — the DS918+ is x86-64, so no
`--platform` is needed. An ARM NAS needs an `linux/arm64` build as well.

To go back to building on the NAS: empty `WEB_PLAYER_IMAGE` again and run
`sudo docker compose up -d --build`.

> The package is created by the first CI run and is **private by default**:
> open the repository's **Packages** tab → *web-player* → *Package settings* →
> *Change visibility* → **Public** if the NAS should pull it without a token.
> Otherwise log in on the NAS with `sudo docker login ghcr.io` and a GitHub
> token that has the `read:packages` scope.

---

## 3. The media library

The container sees your media at `/media` (whatever `WEB_PLAYER_MEDIA_HOST`
points at). Two things matter:

1. **The user id.** The container runs as `PUID:PGID` (default `1026:100`).
   It must be the owner of the media folder, otherwise the scan reports
   `PermissionError: '/media'`:

   ```bash
   stat -c '%u:%g' /volume1/video      # put these two numbers in .env
   ```

2. **Only the mount point is chowned**, never the whole library — a recursive
   chown over a large share takes minutes on a NAS.

Then, in the console: **Media library → Scan media root**. Every video file
that is not in the library yet is added, `ffprobe`d, given a poster frame and
classified as *direct* (playable as it is) or *HLS* (needs a remux/transcode).

### Subtitles

An MKV with soft subtitle tracks is delivered through the **exteplayer3**
service type (5002) on the Enigma2 box; in the browser the subtitle track is
burned in by the transcode or dropped — the browser player does not read the
subtitle sidecar of an MKV.

### Downloads

The ⬇ button in the public player hands over the **original** file — never the
transcoded stream. How it gets to the user depends on where the file lives:

| the file | what happens | the file name |
|---|---|---|
| on the NAS | the NAS streams it (`Range` support, resumable) | our name (`Movie.mkv`) |
| a URL / YouTube | **redirect** — the browser downloads it from the source | the source decides (`videoplayback.mp4` for YouTube) |

The redirect keeps the NAS out of the data path: a remote download costs no
bandwidth on the NAS at all. The price is the file name and the resuming —
both are then up to the source server.

Two settings in `.env`:

* `WEB_PLAYER_DOWNLOAD_MODE=redirect` (default) or `proxy`. With `proxy` the
  NAS streams the remote file itself: our file name, but the traffic runs
  through the NAS. Use it for hosts that refuse a direct browser request.
* `WEB_PLAYER_YTDLP_FORMAT=b[ext=mp4]/b` — the format yt-dlp picks for a
  YouTube link. The default is the best **single** file that has both audio
  and video, because a redirect can only point at one URL. YouTube offers
  such a file up to 720p; higher resolutions need the audio and the video
  merged first, which only the NAS could do.

---

## 4. Users and playlists

* **Users → + Add user**: a username, a password, and the **groups** whose
  media this person may watch.
* A **group** is a shelf of media (*Movies*, *Kids*, *Documentaries* …).
* **Media library → the "Visible to" grid**: the computed result per user.
  Click a cell to cycle *group rule → granted → hidden → group rule*. That is
  the per-item exception.
* The public player shows **only** what the signed-in user may watch — the
  playlist is built per session, and the stream endpoint re-checks it on every
  request.

---

## 5. The Enigma2 box

1. **Receivers → + Add**: the IP of the box, the FTP credentials, the service
   type and the bouquet name.
2. The service type picks the player on the box:

   | Type | Player | When |
   |---|---|---|
   | `4097` | servicemp3 / GStreamer | the safe default — use this |
   | `5001` | ServiceApp gstplayer | for receivers without servicemp3 |
   | `5002` | ServiceApp exteplayer3 | the only one that shows **soft subtitles** inside a Matroska stream |
   | `1` | DVB pipeline | raw MPEG-TS only |

3. **Push to receiver** writes `/etc/enigma2/userbouquet.<name>.tv` over FTP and
   edits `bouquets.tv` — your own bouquets are never touched. The previous
   `bouquets.tv` is kept as `.bouquets.tv.web-player.bak`.
4. The box has to be told to reload:

   ```bash
   curl "http://<box-ip>/web/servicelistreload?mode=2"
   ```

   (or use the *Reload service list* button in OpenWebif).

### The bouquet that is written

```
#NAME web-player (TV)
#SERVICE 1:64:0:0:0:0:0:0:0:0::Movies
#DESCRIPTION Movies
#SERVICE 4097:0:1:31:0:0:0:0:0:0:http%3a//192.168.1.10%3a8088/player/stream/2/stream.m3u8:Nightfall
#DESCRIPTION Nightfall
```

Two details that bite, and that the code already handles:

* every `:` **inside** the URL is written `%3a` — Enigma2 splits the reference
  on `:` and a raw colon silently truncates the URL;
* the 4th field (the SID) is **stable** — it is derived from the media id, so
  the box keeps the user's favourites, picons and EPG mapping when the bouquet
  is regenerated.

---

## 6. Backups

Everything that matters is in the `/config` volume:

| File | What it is |
|---|---|
| `web-player.db` | the whole database (SQLite) |
| `session.key` | the cookie signing key (deleting it signs everybody out) |
| `posters/` | the generated poster frames (they can be regenerated) |
| `hls/` | the HLS work directories (temporary) |

```bash
sudo docker run --rm -v web-player_web-player-config:/data -v /volume1/docker:/out \
  alpine tar czf /out/web-player-config-$(date +%F).tgz -C /data .
```

The media itself is not touched by web-player.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `PermissionError: '/media'` | `PUID`/`PGID` in `.env` do not match the owner of the media folder (`stat -c '%u:%g' <dir>`) |
| *no VAAPI* in Settings → Hardware | `/dev/dri` is not passed to the container, or the `render` group is not joined (the entrypoint does it when the device exists) |
| ffmpeg is missing | the image was built without the Debian `contrib`/`non-free` sources — rebuild, or install ffmpeg in the image yourself |
| a file plays black on the box | the service type does not match the container: `4097` cannot demux Matroska, use `5002` |
| the box does not show the new bouquet | the service list was not reloaded — `curl "http://<box-ip>/web/servicelistreload?mode=2"` |
| the player shows nothing | the user has no group and no per-item grant — check *Dashboard → what each user can watch* |
| `yt-dlp` fails | YouTube changed something; the console shows the exact error. Update the image (`docker compose build --pull`) |
| the browser will not seek in an MKV | it is served as HLS, so the jump is accurate to one segment (4 s by default, `WEB_PLAYER_HLS_SEGMENT`) |

---

## 8. Updating

```bash
cd /volume1/docker/web-player
git pull
sudo docker compose up -d --build
```

The database is created on first start and migrated automatically; the media
library and the posters stay where they are.
