"""web-player — who may see what.

The rule (decision D1: groups + per-item exceptions):

    visible(item, user) = item.enabled
                       and item.outputs.web
                       and ( item.group in user.groups  or  override == 'allow' )
                       and override != 'deny'

The console shows the **computed** result, never the raw flags — a console
that shows only one of the three conditions is what makes the reference
project's `groups_stale` bug possible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Group, Media, User, UserGroup, UserMedia, outputs_json
from .library import effective_delivery


@dataclass
class Visibility:
    user_id: int
    group_ids: set[int]
    overrides: dict[int, str]

    def allowed(self, item: Media) -> bool:
        if not item.enabled:
            return False
        if not item.output_web:
            return False
        override = self.overrides.get(item.id)
        if override == "deny":
            return False
        if override == "allow":
            return True
        return item.group_id in self.group_ids

    def reason(self, item: Media) -> str:
        if not item.enabled:
            return "globally disabled"
        if not item.output_web:
            return "web output off"
        override = self.overrides.get(item.id)
        if override == "deny":
            return "hidden for this user"
        if override == "allow":
            return "granted to this user"
        if item.group_id in self.group_ids:
            return "via group"
        return "not shared"


async def load_visibility(db: AsyncSession, user_id: int) -> Visibility:
    group_ids = set(
        (await db.execute(select(UserGroup.group_id).where(UserGroup.user_id == user_id)))
        .scalars().all()
    )
    rows = (await db.execute(select(UserMedia.media_id, UserMedia.state)
                             .where(UserMedia.user_id == user_id))).all()
    overrides = {mid: state for mid, state in rows}
    return Visibility(user_id=user_id, group_ids=group_ids, overrides=overrides)


async def visible_media(db: AsyncSession, user_id: int) -> list[Media]:
    """Every item this user may watch, in playlist order."""
    vis = await load_visibility(db, user_id)
    rows = (await db.execute(select(Media).order_by(Media.sort_order, Media.id))).scalars().all()
    return [m for m in rows if vis.allowed(m)]


async def set_groups(db: AsyncSession, user_id: int, group_ids: list[int]) -> None:
    await db.execute(UserGroup.__table__.delete().where(UserGroup.user_id == user_id))
    for gid in dict.fromkeys(group_ids):
        db.add(UserGroup(user_id=user_id, group_id=gid))
    await db.commit()


async def set_override(db: AsyncSession, user_id: int, media_id: int,
                       state: str | None) -> None:
    """state: 'allow' | 'deny' | None (inherit the group rule)."""
    existing = (await db.execute(select(UserMedia).where(
        UserMedia.user_id == user_id, UserMedia.media_id == media_id))).scalar_one_or_none()
    if state is None:
        if existing:
            await db.delete(existing)
    elif existing:
        existing.state = state
    else:
        db.add(UserMedia(user_id=user_id, media_id=media_id, state=state))
    await db.commit()


async def groups_with_counts(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(Group).order_by(Group.sort_order, Group.name))).scalars().all()
    out = []
    for g in rows:
        members = (await db.execute(select(UserGroup.user_id)
                                    .where(UserGroup.group_id == g.id))).scalars().all()
        items = (await db.execute(select(Media.id).where(Media.group_id == g.id))).scalars().all()
        out.append({"id": g.id, "name": g.name, "color": g.color,
                    "users": len(members), "items": len(items)})
    return out


def media_row(item: Media, vis: Visibility | None = None) -> dict:
    """The JSON shape the console and the player consume."""
    probe = {}
    if item.probe:
        try:
            probe = json.loads(item.probe)
        except (TypeError, ValueError):
            probe = {}
    row = {
        "id": item.id,
        "title": item.title,
        "year": item.year,
        "kind": item.kind,
        "group_id": item.group_id,
        "source_type": item.source_type,
        "path": item.path,
        "url": item.url,
        "poster": item.poster,
        "description": item.description,
        "duration_s": item.duration_s,
        "size_bytes": item.size_bytes,
        "enabled": item.enabled,
        "sort_order": item.sort_order,
        "delivery": item.delivery,
        "effective_delivery": effective_delivery(item),
        "outputs": {"web": item.output_web, "enigma2": item.output_enigma2},
        "probe": probe,
        "resolved_state": item.resolved_state,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolved_error": item.resolved_error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
    if vis is not None:
        row["visible"] = vis.allowed(item)
        row["visible_reason"] = vis.reason(item)
    return row


def user_row(user: User, visibility: Visibility | None = None) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "role": user.role,
        "enabled": user.enabled,
        "max_streams": user.max_streams,
        "expires_at": user.expires_at,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "group_ids": sorted(visibility.group_ids) if visibility else [],
        "overrides": visibility.overrides if visibility else {},
    }
