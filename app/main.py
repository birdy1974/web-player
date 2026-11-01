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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import (
    BASE_URL, COOKIE_SECURE, DATA_DIR, MEDIA_ROOT, SESSION_HOURS, SESSION_SECRET,
)
from .database import SessionLocal, init_db
from .routers import console as console_router
from .routers import player as player_router
from .services import hls

logging.basicConfig(
    level=os.environ.get("WEB_PLAYER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("web-player")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("web-player %s — media root %s, data %s", "starting", MEDIA_ROOT, DATA_DIR)
    log.info("transcode: %s", hls.transcode_mode())
    task = asyncio.create_task(_reaper())
    try:
        yield
    finally:
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
