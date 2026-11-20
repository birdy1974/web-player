"""Versioned public player assets, served without touching the session.

Compress the small, fixed set once at startup, not on every request. Other
paths (including authenticated pages and media Range responses) are untouched.
"""
from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


@dataclass(frozen=True)
class Asset:
    version: str
    content_type: str
    plain: bytes
    compressed: bytes


def _load_asset(path: str, content_type: str) -> Asset:
    plain = (STATIC_DIR / path).read_bytes()
    return Asset(hashlib.sha256(plain).hexdigest()[:20], content_type, plain,
                 gzip.compress(plain, compresslevel=6, mtime=0))


_assets = {
    f"/static/{path}": _load_asset(path, content_type)
    for path, content_type in (
        ("css/app.css", "text/css"),
        ("js/player.js", "text/javascript"),
        ("vendor/video-js.min.css", "text/css"),
        ("vendor/video.min.js", "text/javascript"),
    )
}


def asset_url(path: str) -> str:
    url = f"/static/{path}"
    return f"{url}?v={_assets[url].version}"


templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals["asset_url"] = asset_url


def _accepts_gzip(header: str) -> bool:
    weights = {}
    for entry in header.lower().split(","):
        coding, _, parameter = entry.strip().partition(";")
        try:
            weights[coding.strip()] = float(parameter.strip().removeprefix("q=")) if parameter else 1.0
        except ValueError:
            weights[coding.strip()] = 0.0
    return weights.get("gzip", weights.get("*", 0.0)) > 0


class PlayerAssets:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        asset = _assets.get(scope.get("path", "")) if scope["type"] == "http" else None
        if (asset is None or scope["method"] not in ("GET", "HEAD")
                or scope["query_string"] != f"v={asset.version}".encode("ascii")):
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        compressed = _accepts_gzip(request_headers.get("accept-encoding", ""))
        body = asset.compressed if compressed else asset.plain
        etag = f'"{asset.version}{"-gzip" if compressed else ""}"'
        headers = {"Cache-Control": "public, max-age=31536000, immutable",
                   "Vary": "Accept-Encoding", "ETag": etag}
        tags = {tag.strip().removeprefix("W/")
                for tag in request_headers.get("if-none-match", "").split(",")}
        if "*" in tags or etag in tags:
            response = Response(status_code=304, headers=headers)
        else:
            if compressed:
                headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(body))
            response = Response(body if scope["method"] == "GET" else b"",
                                media_type=asset.content_type, headers=headers)
        await response(scope, receive, send)
