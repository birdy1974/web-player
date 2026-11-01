"""Draw the web-player symbol and write it as a PNG (no image library needed).

The SVG in app/static/brand/ is the master; this script draws the same
shape with plain maths and writes a PNG, so the icon that iOS and the old
browsers ask for looks exactly like the one in the header.

    python3 tools/make-brand.py

It writes:
    app/static/brand/apple-touch-icon.png   180×180
    app/static/brand/favicon-32.png          32×32
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BRAND = Path(__file__).resolve().parent.parent / "app" / "static" / "brand"

# The drawing board is 64×64 "units" — the same as the viewBox of the SVG.
UNITS = 64.0

# The corners of the play triangle (SVG: M25 19 L41.5 29 L25 39 Z).
TRIANGLE = ((25.0, 19.0), (41.5, 29.0), (25.0, 39.0))
TRIANGLE_ROUND = 2.25          # the stroke of the SVG rounds the corners
BAR = (17.0, 45.5, 30.0, 3.4)  # x, y, width, height of the progress bar
BAR_FILLED = 16.0              # how much of the bar is "played"
BAR_ROUND = 1.7

TOP_LEFT = (0x38, 0xbd, 0xf8)
BOTTOM_RIGHT = (0x63, 0x66, 0xf1)
DARK = (0x06, 0x12, 0x1f)


def rounded_rect_distance(px, py, x, y, w, h, r):
    """The distance to a rounded rectangle (negative inside)."""
    cx = x + w / 2
    cy = y + h / 2
    dx = abs(px - cx) - (w / 2 - r)
    dy = abs(py - cy) - (h / 2 - r)
    outside = (max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
    inside = min(max(dx, dy), 0.0)
    return outside + inside - r


def polygon_distance(px, py, points):
    """The signed distance to a convex polygon (negative inside)."""
    # the winding of the polygon decides which side of an edge is the inside
    area = 0.0
    count = len(points)
    for i in range(count):
        ax, ay = points[i]
        bx, by = points[(i + 1) % count]
        area += ax * by - bx * ay
    sign = 1.0 if area > 0 else -1.0

    best = None
    inside = True
    for i in range(count):
        ax, ay = points[i]
        bx, by = points[(i + 1) % count]
        ex, ey = bx - ax, by - ay
        # how far the point is from the edge, and which side it is on
        length = (ex * ex + ey * ey) ** 0.5
        cross = ex * (py - ay) - ey * (px - ax)
        if cross * sign < 0:
            inside = False
        # the distance to the edge segment
        t = 0.0
        if length:
            t = max(0.0, min(1.0, ((px - ax) * ex + (py - ay) * ey) / (length * length)))
        dx = px - (ax + t * ex)
        dy = py - (ay + t * ey)
        distance = (dx * dx + dy * dy) ** 0.5
        best = distance if best is None else min(best, distance)
    return -best if inside else best


def render(size: int, samples: int = 4) -> bytes:
    """Render the symbol and return the raw RGBA rows."""
    rows = []
    scale = UNITS / size
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r = g = b = a = 0.0
            for sy in range(samples):
                for sx in range(samples):
                    ux = (x + (sx + 0.5) / samples) * scale
                    uy = (y + (sy + 0.5) / samples) * scale
                    colour = pixel(ux, uy)
                    if colour is None:
                        continue
                    cr, cg, cb, ca = colour
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            total = samples * samples
            if a <= 0:
                row += bytes((0, 0, 0, 0))
                continue
            # the colour is averaged where it is, the alpha where it is not
            row += bytes((round(r / a), round(g / a), round(b / a), round(a / total * 255)))
        rows.append(bytes(row))
    return rows


def pixel(ux, uy):
    """The colour of one point of the board (None = transparent)."""
    if rounded_rect_distance(ux, uy, 2, 2, 60, 60, 15) > 0:
        return None

    # the gradient of the screen
    t = max(0.0, min(1.0, (ux + uy) / (2 * UNITS)))
    colour = [TOP_LEFT[i] + (BOTTOM_RIGHT[i] - TOP_LEFT[i]) * t for i in range(3)]

    # a soft shine in the upper half
    shine = max(0.0, 1.0 - uy / 34.0) * 0.22
    colour = [c + (255 - c) * shine for c in colour]

    # the play triangle (the stroke of the SVG rounds the corners)
    if polygon_distance(ux, uy, TRIANGLE) <= TRIANGLE_ROUND:
        return (DARK[0], DARK[1], DARK[2], 1.0)

    # the progress bar
    bx, by, bw, bh = BAR
    if rounded_rect_distance(ux, uy, bx, by, bw, bh, BAR_ROUND) <= 0:
        played = ux <= bx + BAR_FILLED
        return (DARK[0], DARK[1], DARK[2], 0.78 if played else 0.28)

    return (colour[0], colour[1], colour[2], 1.0)


def write_png(path: Path, size: int) -> None:
    rows = render(size)
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind, data):
        body = kind + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)
    print(f"{path.name}: {size}×{size}, {len(png)} bytes")


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    write_png(BRAND / "apple-touch-icon.png", 180)
    write_png(BRAND / "favicon-32.png", 32)


if __name__ == "__main__":
    main()
