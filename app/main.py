"""web-player — application entry point.

Routes:

    /                  redirect to the console or the player
    /console/*         the private management console (admin session)
    /api/console/*     the console's JSON API
    /player/*          the public web player (user session)
    /api/player/*      the player's JSON API
    /player/stream/*   the media itself (direct with Range, or on-demand HLS)
    /api/health        the health check the container uses
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    BASE_URL, COOKIE_SECURE, DATA_DIR, MEDIA_ROOT, SESSION_HOURS, SESSION_SECRET,
    download_mode,
)
from .database import SessionLocal, init_db
from .routers import console as console_router
from .routers import player as player_router
from .services import hls, logs

logs.setup()
log = logging.getLogger("web-player")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.take_over_uvicorn()
    await init_db()
    log.info("web-player starting — media root %s, data %s", MEDIA_ROOT, DATA_DIR)
    log.info("log level %s, log file %s", os.environ.get("WEB_PLAYER_LOG_LEVEL", "INFO"),
             logs.file_info().get("path"))
    log.info("transcode: %s", hls.transcode_mode())
    log.info("remote downloads: %s", download_mode())
    task = asyncio.create_task(_reaper())
    try:
        yield
    finally:
        log.info("web-player stopping")
        task.cancel()


async def _reaper():
    """Stop the HLS sessions nobody is watching."""
    while True:
        try:
            await asyncio.sleep(30)
            dead = await hls.reap_idle()
            if dead:
                log.info("reaped %d idle HLS session(s)", dead)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("the HLS reaper failed")


app = FastAPI(title="web-player", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)


class AccessLog:
    """Log every request: the method, the path, the status, the duration.

    A plain ASGI middleware (not the BaseHTTP one) so that the streaming
    endpoints — the files and the HLS segments — are not touched.

    It sits **inside** the session middleware, so the signed-in user is
    known: a line says who did what.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            self._log(scope, status, started, failed=True)
            raise
        self._log(scope, status, started)

    def _log(self, scope, status, started, failed=False):
        path = scope.get("path", "")
        if path.startswith("/static/"):
            return                       # the icons and the css are noise
        duration = (time.perf_counter() - started) * 1000
        session = scope.get("session") or {}
        who = session.get("admin") or session.get("player_name")
        if not who and session.get("player_user"):
            who = f"user#{session['player_user']}"
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        ip = headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not ip:
            client = scope.get("client")
            ip = client[0] if client else "-"
        level = logging.INFO
        if status >= 500 or failed:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif path.startswith("/poster/"):
            level = logging.DEBUG        # one per item — noise at INFO
        logging.getLogger("web-player.access").log(
            level, "%s %s → %s in %.0f ms (user=%s, ip=%s)",
            scope.get("method", "?"), path, status, duration, who or "-", ip)


app.add_middleware(AccessLog)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="web_player",
    max_age=SESSION_HOURS * 3600,
    same_site="lax",
    https_only=COOKIE_SECURE,
)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(console_router.router)
app.include_router(player_router.router)


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/console", status_code=302)
    if request.session.get("player_user"):
        return RedirectResponse("/player", status_code=302)
    return RedirectResponse("/player/login", status_code=302)


@app.get("/poster/{name}", include_in_schema=False)
async def poster(name: str):
    """The poster frames that were grabbed from the media files."""
    from fastapi.responses import FileResponse

    from .config import POSTER_DIR

    target = (POSTER_DIR / Path(name).name).resolve()
    if not str(target).startswith(str(POSTER_DIR.resolve())) or not target.exists():
        raise HTTPException(status_code=404, detail="no poster")
    return FileResponse(str(target), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/health", include_in_schema=False)
async def health():
    from .database import db_healthy
    return JSONResponse({
        "ok": await db_healthy(),
        "version": app.version,
        "vaapi": hls.vaapi_report(),
        "base_url": BASE_URL or "",
    })


@app.exception_handler(404)
async def not_found(request: Request, exc):  # noqa: ANN001
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return HTMLResponse("<h1>404 — not found</h1>", status_code=404)
