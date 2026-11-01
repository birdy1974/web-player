"""web-player — the private management console (API + page).

Everything under /api/console needs an administrator session. The session is
a signed cookie, so the password never travels in a URL.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ADMIN_USER, BASE_URL, MEDIA_ROOT, PUBLIC_URL
from ..database import get_db
from ..models import (
    Bouquet, Group, Media, PlaybackProgress, Receiver, Setting, User, UserGroup,
    UserMedia, outputs_json,
)
from ..security import hash_password, new_password, verify_password
from ..services import enigma2, library, resolver
from ..services.visibility import (
    groups_with_counts, load_visibility, media_row, set_groups, set_override,
    user_row,
)

log = logging.getLogger("web-player.console")

router = APIRouter(tags=["console"])
templates = Jinja2Templates(directory="app/templates")


# --------------------------------------------------------------------------
# session helpers
# --------------------------------------------------------------------------
def require_admin(request: Request) -> str:
    user = request.session.get("admin")
    if not user:
        raise HTTPException(401, "the console needs an administrator session")
    return user


async def admin_user(db: AsyncSession, request: Request) -> User:
    require_admin(request)
    name = request.session.get("admin")
    user = (await db.execute(select(User).where(User.name == name))).scalar_one_or_none()
    if user is None or not user.enabled:
        request.session.pop("admin", None)
        raise HTTPException(401, "the administrator account is gone or disabled")
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
@router.get("/console/login", response_class=HTMLResponse)
async def console_login_page(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/console", status_code=302)
    return templates.TemplateResponse(request, "login.html", {
        "kind": "admin",
        "title": "web-player — management console",
        "action": "/console/login",
        "hint": "The console is private. Only the administrator can enter.",
    })


@router.post("/console/login")
async def console_login(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    name = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = (await db.execute(select(User).where(User.name == name))).scalar_one_or_none()
    ok = user is not None and user.role == "admin" and verify_password(user.password_hash, password)
    if not ok and name == ADMIN_USER and not user:
        # The administrator from the environment was never created (the DB was
        # built before the password was set) — accept the env password.
        from ..config import ADMIN_PASSWORD
        ok = bool(ADMIN_PASSWORD) and password == ADMIN_PASSWORD
    if not ok:
        return templates.TemplateResponse(request, "login.html", {
            "kind": "admin", "title": "web-player — management console",
            "action": "/console/login", "error": "Wrong username or password.",
            "hint": "The console is private. Only the administrator can enter.",
        }, status_code=401)
    user = user or (await db.execute(select(User).where(User.name == ADMIN_USER))).scalar_one_or_none()
    if user:
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
    request.session["admin"] = name
    return RedirectResponse("/console", status_code=302)


@router.post("/console/logout")
async def console_logout(request: Request):
    request.session.pop("admin", None)
    return RedirectResponse("/console/login", status_code=302)


@router.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/console/login", status_code=302)
    return templates.TemplateResponse(request, "console.html", {"title": "web-player — console"})


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------
@router.get("/api/console/overview")
async def overview(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    media_rows = (await db.execute(select(Media))).scalars().all()
    users = (await db.execute(select(User).order_by(User.name))).scalars().all()
    receivers = (await db.execute(select(Receiver).order_by(Receiver.name))).scalars().all()

    per_user = []
    for user in users:
        vis = await load_visibility(db, user.id)
        visible = [m for m in media_rows if vis.allowed(m)]
        per_user.append({
            "id": user.id, "name": user.name, "role": user.role,
            "enabled": user.enabled, "visible": len(visible),
            "overrides": len(vis.overrides),
            "group_ids": sorted(vis.group_ids),
            "expires_at": user.expires_at,
            "max_streams": user.max_streams,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        })

    return {
        "media_count": len(media_rows),
        "enabled_count": sum(1 for m in media_rows if m.enabled),
        "playable_direct": sum(1 for m in media_rows
                               if (json.loads(m.probe or "{}").get("delivery") == "direct")),
        "needs_hls": sum(1 for m in media_rows
                         if (json.loads(m.probe or "{}").get("delivery") == "hls")),
        "user_count": len(users),
        "group_count": (await db.execute(select(func.count(Group.id)))).scalar_one(),
        "receiver_count": len(receivers),
        "per_user": per_user,
        "vaapi": __import__("app.services.hls", fromlist=["x"]).vaapi_report(),
        "media_root": str(MEDIA_ROOT),
    }


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------
@router.get("/api/console/media")
async def list_media(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    users = (await db.execute(select(User).order_by(User.name))).scalars().all()
    visibility = {u.id: await load_visibility(db, u.id) for u in users}

    items = (await db.execute(select(Media).order_by(Media.sort_order, Media.id))).scalars().all()
    groups = {g.id: g for g in (await db.execute(select(Group))).scalars().all()}

    rows = []
    for item in items:
        row = media_row(item)
        row["group_name"] = groups[item.group_id].name if item.group_id in groups else None
        row["per_user"] = {
            str(u.id): {
                "visible": visibility[u.id].allowed(item),
                "reason": visibility[u.id].reason(item),
                "override": visibility[u.id].overrides.get(item.id),
            } for u in users
        }
        rows.append(row)
    return {"items": rows, "users": [{"id": u.id, "name": u.name} for u in users]}


@router.post("/api/console/media")
async def create_media(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    title = str(payload.get("title", "")).strip()
    if not title:
        raise HTTPException(400, "a title is required")
    source_type = payload.get("source_type", "local")
    if source_type not in ("local", "url", "youtube"):
        raise HTTPException(400, "unknown source type")

    item = Media(
        title=title,
        year=payload.get("year"),
        kind=payload.get("kind", "movie"),
        group_id=payload.get("group_id"),
        source_type=source_type,
        path=(payload.get("path") or "").strip() or None,
        url=(payload.get("url") or "").strip() or None,
        description=payload.get("description"),
        enabled=bool(payload.get("enabled", True)),
        sort_order=payload.get("sort_order", 0),
        delivery=payload.get("delivery", "auto"),
        outputs=outputs_json(payload.get("output_web", True),
                             payload.get("output_enigma2", False)),
        resolved_state="",
    )
    db.add(item)
    await db.commit()

    if item.source_type == "youtube":
        await resolver.resolve(db, item, force=True)
    elif item.source_type == "local":
        await library.probe_one(db, item)
    return {"id": item.id}


@router.put("/api/console/media/{media_id}")
async def update_media(media_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    item = await db.get(Media, media_id)
    if item is None:
        raise HTTPException(404, "media not found")
    payload = await request.json()

    for field in ("title", "year", "kind", "group_id", "description", "sort_order", "delivery"):
        if field in payload:
            setattr(item, field, payload[field])
    if "path" in payload:
        item.path = (payload.get("path") or "").strip() or None
    if "url" in payload:
        item.url = (payload.get("url") or "").strip() or None
    if "enabled" in payload:
        item.enabled = bool(payload["enabled"])
    if "output_web" in payload or "output_enigma2" in payload:
        item.outputs = outputs_json(
            bool(payload.get("output_web", item.output_web)),
            bool(payload.get("output_enigma2", item.output_enigma2)),
        )
    await db.commit()
    return {"ok": True}


@router.delete("/api/console/media/{media_id}")
async def delete_media(media_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    item = await db.get(Media, media_id)
    if item is None:
        raise HTTPException(404, "media not found")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


@router.get("/api/console/browse")
async def browse_media(request: Request, path: str = "",
                      db: AsyncSession = Depends(get_db)):
    """The file browser of the console: one folder of the media root.

    The path is relative to the media root and can never leave it. Every
    entry says whether it is already in the library.
    """
    await admin_user(db, request)
    try:
        listing = library.browse(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "no such folder in the media root") from exc
    except NotADirectoryError as exc:
        raise HTTPException(400, "that is a file, not a folder") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    known = {row for row in (await db.execute(
        select(Media.path).where(Media.source_type == "local"))).scalars().all() if row}
    for entry in listing["entries"]:
        entry["in_library"] = entry["path"] in known
    return listing


@router.post("/api/console/media/scan")
async def scan_media(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json() if request.headers.get("content-type") else {}
    try:
        report = await library.scan_root(db, group_id=payload.get("group_id"),
                                         folder=(payload.get("folder") or None))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    probed = await library.probe_pending(db, limit=200)
    return {**report, "probed": probed}


@router.post("/api/console/media/{media_id}/probe")
async def reprobe(media_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    item = await db.get(Media, media_id)
    if item is None:
        raise HTTPException(404, "media not found")
    info = await library.probe_one(db, item)
    return {"ok": info is not None, "probe": info}


@router.post("/api/console/media/{media_id}/resolve")
async def re_resolve(media_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    item = await db.get(Media, media_id)
    if item is None:
        raise HTTPException(404, "media not found")
    result = await resolver.resolve(db, item, force=True)
    return result


@router.post("/api/console/media/{media_id}/visibility")
async def set_item_visibility(media_id: int, request: Request,
                              db: AsyncSession = Depends(get_db)):
    """Set the per-item override for one user ('allow', 'deny' or None)."""
    await admin_user(db, request)
    payload = await request.json()
    user_id = payload.get("user_id")
    state = payload.get("state")
    if state not in (None, "allow", "deny"):
        raise HTTPException(400, "state must be allow, deny or null")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    await set_override(db, user_id, media_id, state)
    return {"ok": True}


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------
@router.get("/api/console/users")
async def list_users(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    users = (await db.execute(select(User).order_by(User.name))).scalars().all()
    rows = []
    for user in users:
        vis = await load_visibility(db, user.id)
        rows.append(user_row(user, vis))
    return {"items": rows, "groups": await groups_with_counts(db)}


@router.post("/api/console/users")
async def create_user(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    password = str(payload.get("password", ""))
    if not name:
        raise HTTPException(400, "a name is required")
    if not password:
        raise HTTPException(400, "a password is required")
    if (await db.execute(select(User).where(User.name == name))).scalar_one_or_none():
        raise HTTPException(409, "that user already exists")
    user = User(
        name=name,
        password_hash=hash_password(password),
        role="user",
        enabled=bool(payload.get("enabled", True)),
        max_streams=int(payload.get("max_streams", 2) or 2),
        expires_at=payload.get("expires_at") or None,
    )
    db.add(user)
    await db.commit()
    await set_groups(db, user.id, [int(g) for g in payload.get("group_ids", [])])
    return {"id": user.id, "generated_password": None}


@router.put("/api/console/users/{user_id}")
async def update_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    payload = await request.json()
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not name:
            raise HTTPException(400, "the name may not be empty")
        user.name = name
    if "enabled" in payload:
        user.enabled = bool(payload["enabled"])
    if "max_streams" in payload:
        user.max_streams = int(payload["max_streams"] or 1)
    if "expires_at" in payload:
        user.expires_at = payload["expires_at"] or None
    if payload.get("password"):
        user.password_hash = hash_password(str(payload["password"]))
    await db.commit()
    if "group_ids" in payload:
        await set_groups(db, user.id, [int(g) for g in payload["group_ids"]])
    return {"ok": True}


@router.delete("/api/console/users/{user_id}")
async def delete_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")
    if user.role == "admin":
        raise HTTPException(400, "the administrator cannot be deleted")
    await db.delete(user)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------
@router.get("/api/console/groups")
async def list_groups(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    return {"items": await groups_with_counts(db)}


@router.post("/api/console/groups")
async def create_group(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "a name is required")
    if (await db.execute(select(Group).where(Group.name == name))).scalar_one_or_none():
        raise HTTPException(409, "that group already exists")
    group = Group(name=name, color=payload.get("color", "#38bdf8"),
                  sort_order=int(payload.get("sort_order", 0) or 0))
    db.add(group)
    await db.commit()
    return {"id": group.id}


@router.put("/api/console/groups/{group_id}")
async def update_group(group_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(404, "group not found")
    payload = await request.json()
    if "name" in payload and payload["name"].strip():
        group.name = str(payload["name"]).strip()
    if "color" in payload:
        group.color = payload["color"]
    if "sort_order" in payload:
        group.sort_order = int(payload["sort_order"] or 0)
    await db.commit()
    return {"ok": True}


@router.delete("/api/console/groups/{group_id}")
async def delete_group(group_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    group = await db.get(Group, group_id)
    if group is None:
        raise HTTPException(404, "group not found")
    await db.delete(group)
    await db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# receivers and bouquets
# --------------------------------------------------------------------------
@router.get("/api/console/receivers")
async def list_receivers(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    rows = (await db.execute(select(Receiver).order_by(Receiver.name))).scalars().all()
    out = []
    for r in rows:
        last = (await db.execute(
            select(Bouquet).where(Bouquet.receiver_id == r.id)
            .order_by(Bouquet.pushed_at.desc()).limit(1))).scalar_one_or_none()
        out.append({
            "id": r.id, "name": r.name, "host": r.host, "port": r.port,
            "transport": r.transport, "username": r.username,
            "has_password": bool(r.password),
            "service_type": r.service_type, "bouquet_name": r.bouquet_name,
            "enabled": r.enabled,
            "last_push": last.pushed_at.isoformat() if last else None,
            "last_items": last.item_count if last else 0,
        })
    return {"items": out, "players": enigma2.PLAYERS}


@router.post("/api/console/receivers")
async def create_receiver(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    name = str(payload.get("name", "")).strip()
    host = str(payload.get("host", "")).strip()
    if not name or not host:
        raise HTTPException(400, "a name and a host are required")
    receiver = Receiver(
        name=name, host=host,
        port=int(payload.get("port", 21) or 21),
        transport=payload.get("transport", "ftp"),
        username=payload.get("username", "root"),
        password=payload.get("password") or None,
        service_type=int(payload.get("service_type", 4097) or 4097),
        bouquet_name=(payload.get("bouquet_name") or "web-player").strip(),
        enabled=bool(payload.get("enabled", True)),
    )
    db.add(receiver)
    await db.commit()
    return {"id": receiver.id}


@router.put("/api/console/receivers/{receiver_id}")
async def update_receiver(receiver_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    receiver = await db.get(Receiver, receiver_id)
    if receiver is None:
        raise HTTPException(404, "receiver not found")
    payload = await request.json()
    for field in ("name", "host", "transport", "username", "bouquet_name"):
        if field in payload:
            setattr(receiver, field, payload[field])
    if "port" in payload:
        receiver.port = int(payload["port"] or 21)
    if "service_type" in payload:
        receiver.service_type = int(payload["service_type"] or 4097)
    if "enabled" in payload:
        receiver.enabled = bool(payload["enabled"])
    if payload.get("password"):
        receiver.password = payload["password"]
    await db.commit()
    return {"ok": True}


@router.delete("/api/console/receivers/{receiver_id}")
async def delete_receiver(receiver_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    receiver = await db.get(Receiver, receiver_id)
    if receiver is None:
        raise HTTPException(404, "receiver not found")
    await db.delete(receiver)
    await db.commit()
    return {"ok": True}


async def bouquet_entries(db: AsyncSession, request: Request) -> list[enigma2.Entry]:
    """The items that go into the bouquet: enabled, with the enigma2 output on."""
    base = base_url(request)
    items = (await db.execute(
        select(Media).where(Media.enabled.is_(True)).order_by(
            Media.sort_order, Media.id))).scalars().all()
    groups = {g.id: g for g in (await db.execute(select(Group))).scalars().all()}

    entries = []
    for item in items:
        if not item.output_enigma2:
            continue
        delivery = resolver.fallback_delivery(item)
        if delivery == "direct":
            url = f"{base}/player/stream/{item.id}/direct"
        elif delivery == "hls":
            url = f"{base}/player/stream/{item.id}/stream.m3u8"
        else:
            continue        # an unresolvable YouTube link cannot go on the box
        group = groups.get(item.group_id)
        entries.append(enigma2.Entry(
            media_id=item.id, title=item.title, url=url,
            group=group.name if group else "Ungrouped",
            kind=item.kind, delivery=delivery))
    return entries


@router.get("/api/console/bouquet/preview")
async def preview_bouquet(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    receiver_id = request.query_params.get("receiver_id")
    receiver = await db.get(Receiver, int(receiver_id)) if receiver_id else None
    service_type = receiver.service_type if receiver else 4097
    name = receiver.bouquet_name if receiver else "web-player"
    entries = await bouquet_entries(db, request)
    content, line, count = enigma2.build_bouquet(
        name=name, service_type=service_type, entries=entries)
    return {"content": content, "bouquets_tv": line, "entries": count,
            "filename": enigma2.bouquet_filename(name),
            "players": {str(k): v for k, v in enigma2.PLAYERS.items()}}


@router.post("/api/console/bouquet/push")
async def push_bouquet(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    receiver = await db.get(Receiver, int(payload.get("receiver_id", 0)))
    if receiver is None:
        raise HTTPException(404, "receiver not found")
    if not receiver.enabled:
        raise HTTPException(400, "that receiver is disabled")

    entries = await bouquet_entries(db, request)
    content, line, count = enigma2.build_bouquet(
        name=receiver.bouquet_name, service_type=receiver.service_type, entries=entries)
    filename = enigma2.bouquet_filename(receiver.bouquet_name)

    try:
        if receiver.transport == "download":
            report = enigma2.push_download(
                host=receiver.host, port=receiver.port, username=receiver.username,
                password=receiver.password or "", filename=filename,
                content=content, bouquets_line=line)
        else:
            report = enigma2.push_ftp(
                host=receiver.host, port=receiver.port, username=receiver.username,
                password=receiver.password or "", filename=filename,
                content=content, bouquets_line=line)
    except enigma2.PushError as exc:
        raise HTTPException(502, str(exc)) from exc

    db.add(Bouquet(receiver_id=receiver.id, name=receiver.bouquet_name,
                   filename=filename, item_count=count, payload=content))
    await db.commit()
    return {"ok": True, "entries": count, "filename": filename, **report,
            "reload": enigma2.servicelist_reload_url(receiver.host)}


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------
@router.get("/api/console/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    rows = (await db.execute(select(Setting))).scalars().all()
    return {"items": {r.key: r.value for r in rows},
            "media_root": str(MEDIA_ROOT),
            "base_url": base_url(request)}


@router.put("/api/console/settings")
async def put_settings(request: Request, db: AsyncSession = Depends(get_db)):
    await admin_user(db, request)
    payload = await request.json()
    for key, value in payload.items():
        if not isinstance(value, (str, int, float, bool)):
            value = json.dumps(value)
        row = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if row is None:
            db.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    await db.commit()
    return {"ok": True}
