#!/bin/sh
# web-player entrypoint.
#
# 1. drop privileges to PUID/PGID when the container runs as root
# 2. make sure the data directory is writable
# 3. report what the hardware can do (VAAPI) in the log
# 4. hand over to the CMD (uvicorn)
set -e

PUID="${PUID:-1026}"
PGID="${PGID:-100}"
DATA_DIR="${WEB_PLAYER_DATA:-/config}"
MEDIA_DIR="${WEB_PLAYER_MEDIA:-/media}"

if [ "$(id -u)" = "0" ]; then
    # The render group owns /dev/dri/renderD128; joining it is what makes
    # VAAPI work inside the container.
    if [ "${WEB_PLAYER_JOIN_RENDER:-1}" = "1" ] && [ -e /dev/dri/renderD128 ]; then
        RENDER_GID="$(stat -c '%g' /dev/dri/renderD128 2>/dev/null || echo '')"
        if [ -n "${RENDER_GID}" ]; then
            groupadd -g "${RENDER_GID}" -o render 2>/dev/null || true
            usermod -aG render webplayer 2>/dev/null || true
        fi
    fi

    groupadd -g "${PGID}" -o webplayer 2>/dev/null || true
    usermod -u "${PUID}" -g "${PGID}" webplayer 2>/dev/null || true

    mkdir -p "${DATA_DIR}" "${MEDIA_DIR}"
    chown -R "${PUID}:${PGID}" "${DATA_DIR}" 2>/dev/null || true
    # Only the mount point, not the whole library: a recursive chown over a
    # large share takes minutes on a NAS.
    chown "${PUID}:${PGID}" "${MEDIA_DIR}" 2>/dev/null || true

    exec gosu "${PUID}:${PGID}" "$@"
fi

exec "$@"
