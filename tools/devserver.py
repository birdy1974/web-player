#!/usr/bin/env python3
"""Tiny static file server for the web-player mock-up.

Why it exists: the browser can only *seek* inside a video when the server
answers a ``Range: bytes=`` request with a 206 and the matching byte range.
Python's stock ``http.server`` ignores Range and always answers 200 with the
whole file, so the mock-up's "jump to timestamp" could never be tested
honestly. The real application serves media through its own ASGI stack; this
script is only the mock-up's dev server.

Usage::

    python3 tools/devserver.py [--root mockup] [--port 8088] [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeHandler(SimpleHTTPRequestHandler):
    """``SimpleHTTPRequestHandler`` + RFC 7233 single-range support."""

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            fh = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        rng = self.headers.get("Range")
        if not rng:
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(os.fstat(fh.fileno()).st_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return fh

        m = RANGE_RE.match(rng.strip())
        if not m:
            fh.close()
            self.send_error(416, "Bad Range")
            return None

        size = os.fstat(fh.fileno()).st_size
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "":                       # suffix range: last N bytes
            length = int(end_s or 0)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            fh.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        fh.seek(start)
        return _Limited(fh, end - start + 1)


class _Limited:
    """File-like wrapper that stops after ``remaining`` bytes."""

    def __init__(self, fh, remaining: int):
        self.fh = fh
        self.remaining = remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        if size is None or size < 0:
            size = self.remaining
        size = min(size, self.remaining)
        chunk = self.fh.read(size)
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.fh.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="mockup", help="directory to serve")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"root {root} is not a directory", file=sys.stderr)
        return 1

    mimetypes.add_type("video/mp2t", ".ts")
    mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")

    handler = partial(RangeHandler, directory=str(root))
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"web-player mock-up serving {root} on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
