"""web-player — the public web player.

Password protected: the player session is a signed cookie that carries the
user id, and **every** playlist and stream request re-checks it against the
database (an account that was disabled or has expired loses access at once).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import BASE_URL, MAX_STREAMS, MEDIA_ROOT, PUBLIC_URL, SESSION_SECRET
from ..database import get_db
from ..models import Media, PlaybackProgress, User
from ..security import verify_password
from ..services import hls, library, resolver
from ..services.visibility import load_visibility, visible_media

log = logging.getLogger("web-player.player")

router = APIRouter(tags=["player"])
templates = Jinja2Templates(directory="app/templates")


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
async def session_user(request: Request, db: AsyncSession) -> User:
    user_id = request.session.get("player_user")
    if not isinstance(user_id, int):
        raise HTTPException(401, "sign in first")
    user = await db.get(User, user_id)
    if user is None or not user.enabled:
        request.session.pop("player_user", None)
        raise HTTPException(401, "this account is disabled")
    if user.expires_at and user.expires_at < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        request.session.pop("player_user", None)
        raise HTTPException(401, "this account has expired")
    return user


def base_url(request: Request) -> str:
    if BASE_URL:
        return BASE_URL
    if PUBLIC_URL:
        return PUBLIC_URL
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
@router.get("/player/login", response_class=HTMLResponse)
async def player_login_page(request: Request):
    if request.session.get("player_user"):
        return RedirectResponse("/player", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "kind": "player",
        "title": "web-player — sign in",
        "action": "/player/login",
        "hint": "Sign in with the account the administrator made for you.",
    })


@router.post("/player/login")
async def player_login(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    name = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = (await db.execute(select(User).where(User.name == name))).scalar_one_or_none()

    error = None
    if user is None:
        error = "Unknown user"
    elif not verify_password(user.password_hash, password):
        error = "Wrong username or password"
    elif not user.enabled:
        error = "This account is disabled"
    elif user.expires_at and user.expires_at < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        error = "This account has expired"

    if error:
        return templates.TemplateResponse(request, "login.html", {
            "kind": "player", "title": "web-player — sign in",
            "action": "/player/login", "error": error,
            "hint": "Sign in with the account the administrator made for you.",
        }, status_code=401)

    request.session["player_user"] = user.id
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse("/player", status_code=302)


@router.post("/player/logout")
async def player_logout(request: Request):
    request.session.pop("player_user", None)
    return RedirectResponse("/player/login", status_code=302)


@router.get("/player", response_class=HTMLResponse)
async def player_page(request: Request):
    if not request.session.get("player_user"):
        return RedirectResponse("/player/login", status_code=302)
    return templates.TemplateResponse(request, "player.html", {"title": "web-player"})


# --------------------------------------------------------------------------
# playlist
# --------------------------------------------------------------------------
@router.get("/api/player/playlist")
async def playlist(request: Request, db: AsyncSession = Depends(get_db)):
    user = await session_user(request, db)
    items = await visible_media(db, user.id)
    groups = {g.id: g.name for g in (
        await db.execute(select(__import__("app.models", fromlist=["Group"]).Group))).scalars().all()}

    progress_rows = (await db.execute(
        select(PlaybackProgress).where(PlaybackProgress.user_id == user.id))).scalars().all()
    progress = {p.media_id: p.position_s for p in progress_rows}

    out = []
    for item in items:
        delivery = resolver.fallback_delivery(item)
        probe = {}
        if item.probe:
            try:
                probe = json.loads(item.probe)
            except (TypeError, ValueError):
                probe = {}
        out.append({
            "id": item.id,
            "title": item.title,
            "year": item.year,
            "kind": item.kind,
            "group": groups.get(item.group_id),
            "group_id": item.group_id,
            "poster": f"/poster/{item.poster}" if item.poster else None,
            "duration_s": item.duration_s,
            "delivery": delivery,
            "container": probe.get("container"),
            "video": probe.get("video"),
            "width": probe.get("width"),
            "height": probe.get("height"),
            "progress_s": progress.get(item.id, 0),
            "src": stream_url(request, item, delivery),
        })
    return {"items": out, "user": {"id": user.id, "name": user.name},
            "max_streams": MAX_STREAMS}


def stream_url(request: Request, item: Media, delivery: str) -> str | None:
    """The URL the browser should play.

    The id is enough here because the playlist is built per session and the
    stream endpoint re-checks the session and the item's visibility.
    """
    if delivery == "embed":
        return item.url
    if delivery == "hls":
        return f"/player/stream/{item.id}/stream.m3u8"
    if delivery == "direct":
        return f"/player/stream/{item.id}/direct"
    return None


@router.get("/api/player/progress")
async def get_progress(request: Request, db: AsyncSession = Depends(get_db)):
    user = await session_user(request, db)
    rows = (await db.execute(
        select(PlaybackProgress).where(PlaybackProgress.user_id == user.id))).scalars().all()
    return {"items": {str(p.media_id): p.position_s for p in rows}}


@router.post("/api/player/progress")
async def set_progress(request: Request, db: AsyncSession = Depends(get_db)):
    user = await session_user(request, db)
    payload = await request.json()
    media_id = int(payload.get("media_id", 0))
    position = float(payload.get("position_s", 0) or 0)
    if not media_id:
        raise HTTPException(400, "media_id is required")
    row = (await db.execute(select(PlaybackProgress).where(
        PlaybackProgress.user_id == user.id,
        PlaybackProgress.media_id == media_id))).scalar_one_or_none()
    if row is None:
        db.add(PlaybackProgress(user_id=user.id, media_id=media_id, position_s=position))
    else:
        row.position_s = position
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# streaming
# --------------------------------------------------------------------------
async def playable_media(request: Request, db: AsyncSession, media_id: int) -> Media:
    """The item, if this session's user may watch it."""
    user = await session_user(request, db)
    vis = await load_visibility(db, user.id)
    item = await db.get(Media, media_id)
    if item is None or not vis.allowed(item):
        raise HTTPException(404, "not in your playlist")
    return item


@router.get("/player/stream/{media_id}/direct")
async def stream_direct(media_id: int, request: Request,
                        db: AsyncSession = Depends(get_db)):
    """Serve the file itself with HTTP Range support (exact seeking, 0 % CPU)."""
    item = await playable_media(request, db, media_id)

    source: str | None = None
    filename = "video.mp4"
    if item.source_type == "local":
        path = library.absolute_path(item)
        if path is None or not path.exists():
            raise HTTPException(404, "the file is gone")
        source = str(path)
        filename = path.name
    elif item.source_type == "youtube":
        if item.resolved_state != "resolved" or not item.resolved_url:
            await resolver.resolve(db, item, force=True)
        if item.resolved_state != "resolved" or not item.resolved_url:
            raise HTTPException(502, f"yt-dlp could not resolve this link: {item.resolved_error}")
        source = item.resolved_url
        filename = f"{item.title}.mp4".replace(" ", "_")
    else:
        if not item.url:
            raise HTTPException(404, "no source")
        source = item.url
        filename = Path(item.url.split("?")[0]).name or "video.mp4"

    if source.startswith("http"):
        # A remote file: let the client fetch it itself (a redirect keeps the
        # NAS out of the path and the browser can seek when the host allows it).
        return RedirectResponse(source, status_code=302)

    return _ranged_file(Path(source), request, filename=filename)


def _ranged_file(path: Path, request: Request, filename: str) -> Response:
    """A FileResponse with a proper Content-Type and Range support.

    Starlette's FileResponse answers Range requests itself in recent
    versions; the Accept-Ranges header is set either way so the browser
    knows it may seek.
    """
    media_type = library.media_mime(str(path))
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="{filename}"',
    }
    return FileResponse(str(path), media_type=media_type, headers=headers)


@router.get("/player/stream/{media_id}/stream.m3u8")
async def stream_playlist(media_id: int, request: Request,
                           db: AsyncSession = Depends(get_db)):
    """The on-demand HLS playlist for an item the browser cannot play."""
    item = await playable_media(request, db, media_id)
    user = await session_user(request, db)

    source = await _hls_source(db, item)
    if source is None:
        raise HTTPException(404, "this item cannot be served as HLS")

    duration = item.duration_s or 0.0
    if not duration:
        info = await library.probe_one(db, item)
        duration = (info or {}).get("duration_s") or 0.0

    session = await hls.get_session(media_id, user.id, source, duration,
                                    _codec_of(item))
    playlist = hls.build_playlist(session)
    return Response(playlist, media_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"})


def _codec_of(item: Media) -> str | None:
    try:
        probe = json.loads(item.probe or "{}")
    except (TypeError, ValueError):
        return None
    return probe.get("video")


async def _hls_source(db: AsyncSession, item: Media) -> str | None:
    if item.source_type == "local":
        path = library.absolute_path(item)
        if path is None or not path.exists():
            return None
        return str(path)
    if item.source_type == "youtube":
        if item.resolved_state != "resolved" or not item.resolved_url:
            await resolver.resolve(db, item, force=True)
        return item.resolved_url if item.resolved_state == "resolved" else None
    return item.url


@router.get("/player/segment/{media_id}/{index}.ts")
async def stream_segment(media_id: int, index: int, request: Request,
                         db: AsyncSession = Depends(get_db)):
    """One HLS segment — produced on demand by ffmpeg."""
    item = await playable_media(request, db, media_id)
    user = await session_user(request, db)

    source = await _hls_source(db, item)
    if source is None:
        raise HTTPException(404, "this item cannot be served as HLS")
    duration = item.duration_s or 0.0
    session = await hls.get_session(media_id, user.id, source, duration, _codec_of(item))

    path = await hls.ensure_segment(session, index)
    if path is None or not path.exists():
        raise HTTPException(504, session.failed or "the segment could not be produced")
    return FileResponse(str(path), media_type="video/mp2t",
                        headers={"Cache-Control": "no-store"})


@router.get("/player/poster/{media_id}")
async def item_poster(media_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """The poster of one item (checked against the session)."""
    item = await playable_media(request, db, media_id)
    if not item.poster:
        raise HTTPException(404, "no poster")
    from ..config import POSTER_DIR
    path = POSTER_DIR / item.poster
    if not path.exists():
        raise HTTPException(404, "no poster")
    return FileResponse(str(path), media_type="image/jpeg")
