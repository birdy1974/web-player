"""web-player — logging, for fault finding.

The administrator wants to know *what happened* without leaving the
console, and the container log (stdout, what Portainer shows) must tell the
same story. So every record goes to three places:

* **stdout** — what Portainer shows while the container runs;
* **a rotating file** in the data directory — it survives a restart, it can
  be downloaded from the console, and it can be attached to a bug report;
* **a ring buffer** in memory — the console's *Logs* tab reads it, so the
  last few thousand lines are there even before the file was written.

The format is one line per record:

    2026-11-01 15:10:43  INFO    web-player.access   GET /player → 200 in 12 ms

The name in front of the message says **where** it came from, so a line can
be traced back to the code without a search.
"""
from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import LOG_FILE, LOG_LEVEL, LOG_LINES, LOG_MAX_BYTES

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_buffer: "RingBuffer | None" = None
_file_handler: logging.Handler | None = None


class RingBuffer(logging.Handler):
    """Keeps the last N records in memory — the console reads them.

    A lock, because the HLS threads and the request handlers write at the
    same time.
    """

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.records: deque[dict] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stamp = datetime.fromtimestamp(record.created)
            entry = {
                "ts": stamp.strftime("%Y-%m-%d %H:%M:%S") + f".{int(record.msecs):03d}",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "where": f"{record.filename}:{record.lineno}",
            }
            if record.exc_info:
                entry["exc"] = self.format(record)
            with self._lock:
                self._seq += 1
                entry["seq"] = self._seq
                self.records.append(entry)
        except Exception:  # noqa: BLE001 - a logging error must never crash
            self.handleError(record)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self.records)

    def clear(self) -> int:
        with self._lock:
            number = len(self.records)
            self.records.clear()
            return number


def level_number(level: str) -> int:
    return getattr(logging, (level or "INFO").upper(), logging.INFO)


def setup() -> None:
    """Send every record to stdout, to the log file and to the buffer."""
    global _buffer, _file_handler

    root = logging.getLogger()
    root.setLevel(level_number(LOG_LEVEL))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    root.addHandler(stream)

    _file_handler = None
    if LOG_FILE:
        try:
            path = Path(LOG_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                str(path), maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            root.addHandler(handler)
            _file_handler = handler
        except OSError as exc:
            sys.stdout.write(f"web-player: the log file could not be opened ({exc})\n")

    _buffer = RingBuffer(LOG_LINES)
    _buffer.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_buffer)


def take_over_uvicorn() -> None:
    """Let uvicorn's own log lines come through our handlers.

    uvicorn configures its loggers with `propagate: False`, so without this
    its messages ("Uvicorn running on …") would never reach the log file or
    the console. Its access log is silenced: the application logs every
    request itself, with more detail (the user, the duration).
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(
        logging.INFO if level_number(LOG_LEVEL) <= logging.DEBUG else logging.WARNING)


# --------------------------------------------------------------------------
# what the console reads
# --------------------------------------------------------------------------
def records(level: str = "", search: str = "", limit: int = 300) -> list[dict]:
    """The buffered records, oldest first, filtered."""
    if _buffer is None:
        return []
    items = _buffer.snapshot()
    if level:
        wanted = level.upper()
        items = [r for r in items if r["level"] == wanted]
    if search:
        needle = search.lower()
        items = [r for r in items
                 if needle in r["msg"].lower() or needle in r["logger"].lower()]
    limit = max(1, min(int(limit or 300), 5000))
    return items[-limit:]


def counts() -> dict:
    """How many lines of each level are in the buffer."""
    out = {name: 0 for name in LEVELS}
    for entry in (_buffer.snapshot() if _buffer else []):
        out[entry["level"]] = out.get(entry["level"], 0) + 1
    return out


def clear() -> int:
    """Empty the buffer. The file stays — it is the record of what happened."""
    if _buffer is None:
        return 0
    return _buffer.clear()


def log_path() -> Path | None:
    return Path(LOG_FILE) if LOG_FILE else None


def file_info() -> dict:
    path = log_path()
    if path is None:
        return {"path": None, "exists": False}
    info = {"path": str(path), "exists": path.exists()}
    if path.exists():
        stat = path.stat()
        info["size"] = stat.st_size
        info["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    return info


def truncate_file() -> bool:
    """Empty the log file (the buffer keeps running).

    The handler is taken off the root logger first: a handler that writes
    to a file that was emptied underneath it would keep the old size and
    would not start a new file.
    """
    global _file_handler
    path = log_path()
    if path is None or not path.exists():
        return False
    root = logging.getLogger()
    handler = _file_handler
    if handler is not None:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
        _file_handler = None
    try:
        path.write_text("", encoding="utf-8")
        ok = True
    except OSError:
        ok = False
    try:
        fresh = RotatingFileHandler(
            str(path), maxBytes=LOG_MAX_BYTES, backupCount=3, encoding="utf-8")
        fresh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(fresh)
        _file_handler = fresh
    except OSError:
        pass
    return ok
