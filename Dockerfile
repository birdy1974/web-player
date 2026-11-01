# syntax=docker/dockerfile:1
#
# web-player — image for a Synology DS918+ (amd64, Celeron J3455, VAAPI).
#
#   docker compose up -d --build
#   http://<nas-ip>:8088            management console (private)
#   http://<nas-ip>:8088/player     public web player
#
# The image carries:
#   * ffmpeg/ffprobe            — probing, remux, on-demand HLS, VAAPI transcode
#   * intel-media-va-driver     — the iHD VAAPI driver (VAAPI is optional at runtime)
#   * mesa-va-drivers           — the i965 driver, a fallback for older kernels
#   * yt-dlp                    — resolves YouTube links into a direct stream
#
FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive
ARG UID=1026
ARG GID=100

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_PLAYER_DATA=/config \
    WEB_PLAYER_MEDIA=/media \
    WEB_PLAYER_PORT=8088

# ---------------------------------------------------------------- packages
#
# The Intel VAAPI drivers live in Debian's "non-free" component, so that
# component has to be enabled. Depending on the base image the sources are
# either the classic /etc/apt/sources.list or the newer deb822 file
# (/etc/apt/sources.list.d/debian.sources) — both are patched here.
#
# The Intel drivers themselves are optional: without them the transcode falls
# back to libx264 (slower, but it works). A package that this Debian release
# does not know must not stop the image from being built.
RUN set -eux; \
    for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
        [ -f "$f" ] || continue; \
        sed -i -e 's/^deb \(.*\)$/deb \1 contrib non-free/' \
               -e 's/^\(Components: *\)main[[:space:]]*$/\1main contrib non-free/' "$f"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        vainfo \
        libva2 \
        libva-drm2 \
        mesa-va-drivers \
        libgbm1 \
        ca-certificates \
        curl \
        gosu \
        tini \
        tzdata; \
    for pkg in intel-media-va-driver i965-va-driver; do \
        apt-get install -y --no-install-recommends "$pkg" \
            || echo "WARNING: $pkg is not in this release — VAAPI may not work"; \
    done; \
    rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------- app code
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# The image runs as a non-root user. On DSM the bind-mounted media folder
# belongs to the account you set with PUID/PGID, so the entrypoint re-owns
# itself to it (see the README).
RUN set -eux; \
    groupadd -g "${GID}" -o webplayer 2>/dev/null || true; \
    useradd -u "${UID}" -g "${GID}" -o -m -d /home/webplayer -s /bin/bash webplayer 2>/dev/null || true; \
    mkdir -p /config /media /app/var

EXPOSE 8088
VOLUME ["/config", "/media"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${WEB_PLAYER_PORT:-8088}/api/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088", "--proxy-headers", "--forwarded-allow-ips", "*"]
