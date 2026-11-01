"""web-player — ORM models.

The schema mirrors the mock-up's fake-data.js one-to-one, so what the console
shows in the prototype is what these tables hold in the application.

See docs/MOCKUP.md §5 for the reasoning behind each table.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# USERS
# ===========================================================================
class User(Base):
    """A public web-player account.

    The password is stored as an Argon2 hash only (decision D5: the web
    player is the only output, so there is no reason to keep a plain-text
    copy for an M3U URL).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")     # admin | user
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_streams: Mapped[int] = mapped_column(Integer, default=2)
    expires_at: Mapped[str | None] = mapped_column(String(25))        # YYYY-MM-DD
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    groups: Mapped[list["UserGroup"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")
    overrides: Mapped[list["UserMedia"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


# ===========================================================================
# GROUPS  (named shelves of media)
# ===========================================================================
class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    color: Mapped[str] = mapped_column(String(16), default="#38bdf8")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    user_links: Mapped[list["UserGroup"]] = relationship(
        back_populates="group", cascade="all, delete-orphan")


class UserGroup(Base):
    """Which groups a user may see (decision D1: groups + per-item exceptions)."""

    __tablename__ = "user_groups"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"),
                                           primary_key=True)

    user: Mapped[User] = relationship(back_populates="groups")
    group: Mapped[Group] = relationship(back_populates="user_links")


class UserMedia(Base):
    """A per-item override: `allow` wins over the group rule, `deny` hides it."""

    __tablename__ = "user_media"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"),
                                           primary_key=True)
    state: Mapped[str] = mapped_column(String(8), default="allow")   # allow | deny

    user: Mapped[User] = relationship(back_populates="overrides")


# ===========================================================================
# MEDIA
# ===========================================================================
class Media(Base):
    """One playable item: a local file, a URL, or a YouTube link."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    year: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="movie", index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"),
                                                  index=True)

    source_type: Mapped[str] = mapped_column(String(16), default="local", index=True)
    path: Mapped[str | None] = mapped_column(Text)        # relative to the media root
    url: Mapped[str | None] = mapped_column(Text)          # what the admin typed
    poster: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    duration_s: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)

    # delivery: direct | hls | embed   (decision D3)
    delivery: Mapped[str] = mapped_column(String(16), default="auto")
    # ffprobe result as JSON text: {container, video, audio, width, height, ...}
    probe: Mapped[str | None] = mapped_column(Text)
    # yt-dlp resolution (decision D4)
    resolved_url: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_state: Mapped[str] = mapped_column(String(16), default="", index=True)
    resolved_error: Mapped[str | None] = mapped_column(Text)
    resolved_meta: Mapped[str | None] = mapped_column(Text)

    # outputs: {"web": bool, "enigma2": bool}
    outputs: Mapped[str] = mapped_column(Text, default='{"web":true,"enigma2":false}')

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, onupdate=utcnow)

    group: Mapped[Group | None] = relationship()

    @property
    def output_web(self) -> bool:
        return _outputs(self.outputs).get("web", True)

    @property
    def output_enigma2(self) -> bool:
        return _outputs(self.outputs).get("enigma2", False)


def _outputs(raw: str | None) -> dict:
    import json
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def outputs_json(web: bool, enigma2: bool) -> str:
    import json
    return json.dumps({"web": bool(web), "enigma2": bool(enigma2)})


# ===========================================================================
# ENIGMA2
# ===========================================================================
class Receiver(Base):
    """An Enigma2 satellite box."""

    __tablename__ = "receivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    host: Mapped[str] = mapped_column(String(300))
    port: Mapped[int] = mapped_column(Integer, default=21)
    transport: Mapped[str] = mapped_column(String(16), default="ftp")   # ftp | ssh | download
    username: Mapped[str] = mapped_column(String(120), default="root")
    password: Mapped[str | None] = mapped_column(String(255))
    service_type: Mapped[int] = mapped_column(Integer, default=4097)   # 1|4097|5001|5002
    bouquet_name: Mapped[str] = mapped_column(String(120), default="web-player")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bouquet(Base):
    """What was pushed to a receiver (the exact file, so it can be restored)."""

    __tablename__ = "bouquets"

    id: Mapped[int] = mapped_column(primary_key=True)
    receiver_id: Mapped[int] = mapped_column(ForeignKey("receivers.id", ondelete="CASCADE"),
                                              index=True)
    name: Mapped[str] = mapped_column(String(120))
    filename: Mapped[str] = mapped_column(String(300))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(Text, default="")
    pushed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ===========================================================================
# PLAYBACK PROGRESS  (resume where you left off)
# ===========================================================================
class PlaybackProgress(Base):
    __tablename__ = "playback_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"),
                                           primary_key=True)
    position_s: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, onupdate=utcnow)


# ===========================================================================
# SETTINGS
# ===========================================================================
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=utcnow, onupdate=utcnow)
