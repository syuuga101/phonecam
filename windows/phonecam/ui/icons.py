# -*- coding: utf-8 -*-
"""16px の線アイコン (PIL で描画 → tk.PhotoImage)。色はテーマの text2、テーマごと・サイズごとにキャッシュ。

get(name, size=16, color=None) -> tk.PhotoImage | None   (名前が無い/PIL が無いときは None)
app_icon(size=20) -> tk.PhotoImage | None
NAMES: 対応するアイコン名
"""

import base64
import io
import math
import os

from phonecam.core.deps import tk
from phonecam.ui import theme
from phonecam.ui.dpi import px

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = ImageDraw = None

_G = 24.0       # 設計グリッド
_SS = 4         # スーパーサンプリング
_STROKE = 2.25  # グリッド単位 (16px 時に約1.5px)


def _circle(cx, cy, r):
    return ("e", (cx - r, cy - r, cx + r, cy + r))


def _dot(cx, cy, r=1.3):
    return ("fe", (cx - r, cy - r, cx + r, cy + r))


def _arc(cx, cy, r, a0, a1):
    return ("a", (cx - r, cy - r, cx + r, cy + r), a0, a1)


def _heart():
    pts = []
    for i in range(0, 61):
        t = i / 60.0 * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((12 + x * 0.55, 11.5 - y * 0.55))
    return ("l", pts, True)


def _gear():
    prims = [_circle(12, 12, 6.2), _circle(12, 12, 2.4)]
    for k in range(8):
        a = math.radians(k * 45)
        prims.append(("l", [(12 + 6.2 * math.cos(a), 12 + 6.2 * math.sin(a)),
                            (12 + 9.2 * math.cos(a), 12 + 9.2 * math.sin(a))], False))
    return prims


def _sun():
    prims = [_circle(12, 12, 4)]
    for k in range(8):
        a = math.radians(k * 45)
        prims.append(("l", [(12 + 7 * math.cos(a), 12 + 7 * math.sin(a)),
                            (12 + 10 * math.cos(a), 12 + 10 * math.sin(a))], False))
    return prims


_SPEAKER = ("l", [(4, 10), (8, 10), (13, 6), (13, 18), (8, 14), (4, 14)], True)

_DEFS = {
    "link": [("r", (2.5, 8.5, 14, 15.5), 3.5), ("r", (10, 8.5, 21.5, 15.5), 3.5)],
    "usb": [("l", [(12, 3), (12, 18)], False), _circle(12, 20, 2), ("l", [(12, 13), (7, 10), (7, 8.5)], False),
            ("r", (5.5, 5.5, 8.5, 8.5), 0.5), ("l", [(12, 10), (17, 8.5), (17, 7.5)], False), _circle(17, 6.5, 1.5)],
    "wifi": [_arc(12, 19, 5, 232, 308), _arc(12, 19, 9.5, 232, 308), _arc(12, 19, 14, 232, 308), _dot(12, 19, 1.4)],
    "video": [("r", (2.5, 6.5, 15, 17.5), 2.5), ("l", [(15, 10.5), (21.5, 7.5), (21.5, 16.5), (15, 13.5)], True)],
    "camera": [("r", (2.5, 8, 21.5, 20), 2.5), ("l", [(8, 8), (9.5, 4.5), (14.5, 4.5), (16, 8)], False), _circle(12, 14, 3.6)],
    "lens": [_circle(12, 12, 9), _circle(12, 12, 4), _dot(15.5, 8.5, 0.8)],
    "aspect": [("r", (2.5, 6, 21.5, 18), 2.5), ("l", [(7, 10), (7, 14)], False), ("l", [(17, 10), (17, 14)], False)],
    "rotate": [_arc(12, 12, 7.5, 40, 320), ("l", [(17.7, 6.4), (17.7, 2.8)], False), ("l", [(17.7, 6.4), (21.3, 6.4)], False)],
    "flip": [("l", [(12, 3), (12, 21)], False), ("l", [(9, 7), (9, 19), (3, 19)], True), ("l", [(15, 7), (15, 19), (21, 19)], True)],
    "gauge": [_arc(12, 15, 9.5, 180, 360), ("l", [(12, 15), (16.5, 9.5)], False), _dot(12, 15, 1.4), ("l", [(2.5, 15), (21.5, 15)], False)],
    "sun": _sun(),
    "droplet": [("l", [(12, 3), (6.6, 12.5)], False), ("l", [(12, 3), (17.4, 12.5)], False), _arc(12, 15, 6, 335, 205)],
    "focus": [("l", [(3, 8), (3, 3), (8, 3)], False), ("l", [(16, 3), (21, 3), (21, 8)], False),
              ("l", [(21, 16), (21, 21), (16, 21)], False), ("l", [(8, 21), (3, 21), (3, 16)], False), _circle(12, 12, 2.5)],
    "zoom": [_circle(10.5, 10.5, 6.5), ("l", [(15.3, 15.3), (21, 21)], False),
             ("l", [(8, 10.5), (13, 10.5)], False), ("l", [(10.5, 8), (10.5, 13)], False)],
    "light": [_circle(12, 9.5, 6), ("l", [(9.3, 14.5), (9.8, 18), (14.2, 18), (14.7, 14.5)], False),
              ("l", [(10, 21), (14, 21)], False)],
    "sparkles": [("l", [(12, 3), (14, 10), (21, 12), (14, 14), (12, 21), (10, 14), (3, 12), (10, 10)], True)],
    "person": [_circle(12, 8, 4), _arc(12, 22, 8, 195, 345)],
    "palette": [("e", (3, 3.5, 21, 20.5)), _dot(8, 10, 1.3), _dot(12, 7.5, 1.3), _dot(16, 10, 1.3), _dot(15, 15.5, 1.3)],
    "mic": [("r", (9, 2.5, 15, 14), 3), _arc(12, 11, 6.5, 0, 180), ("l", [(12, 17.5), (12, 21.5)], False)],
    "speaker": [_SPEAKER],
    "volume": [("l", [(3, 10), (7, 10), (12, 6), (12, 18), (7, 14), (3, 14)], True),
               _arc(12.5, 12, 4.5, 305, 55), _arc(12.5, 12, 8.5, 305, 55)],
    "record": [_circle(12, 12, 9), ("fe", (8, 8, 16, 16))],
    "folder": [("l", [(3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19)], True)],
    "thermometer": [("r", (9.5, 2.5, 14.5, 15), 2.5), _circle(12, 17.5, 3.5), ("l", [(12, 8), (12, 17)], False)],
    "battery": [("r", (2, 7.5, 19, 16.5), 2.5), ("l", [(21.5, 11), (21.5, 13)], False), ("fr", (5, 10.5, 11, 13.5))],
    "clock": [_circle(12, 12, 9), ("l", [(12, 7), (12, 12), (15.5, 14)], False)],
    "keyboard": [("r", (2, 6, 22, 18), 2.5), _dot(6.5, 10, 0.9), _dot(10.5, 10, 0.9), _dot(14, 10, 0.9), _dot(17.5, 10, 0.9),
                 ("l", [(7, 14.5), (17, 14.5)], False)],
    "gear": _gear(),
    "globe": [_circle(12, 12, 9), ("e", (8, 3, 16, 21)), ("l", [(3, 12), (21, 12)], False)],
    "download": [("l", [(12, 3.5), (12, 15)], False), ("l", [(7, 10.5), (12, 15.5), (17, 10.5)], False),
                 ("l", [(4, 20), (20, 20)], False)],
    "wrench": [("l", [(4.5, 19.5), (12, 12)], False), _circle(16, 8, 4.5)],
    "info": [_circle(12, 12, 9), _dot(12, 7.8, 1.2), ("l", [(12, 11), (12, 17)], False)],
    "heart": [_heart()],
    "document": [("l", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)], True), ("l", [(14, 3), (14, 8), (19, 8)], False),
                 ("l", [(9, 13), (16, 13)], False), ("l", [(9, 17), (16, 17)], False)],
    "play": [("l", [(8, 5), (19, 12), (8, 19)], True)],
    "pause": [("r", (6.5, 5, 10.5, 19), 1), ("r", (13.5, 5, 17.5, 19), 1)],
}

NAMES = tuple(_DEFS.keys())
_cache = {}


def _hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _render(name, size, color):
    """設計グリッド 24 を size px に描いた RGBA 画像。"""
    big = size * _SS
    k = big / _G
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = _hex_rgb(color) + (255,)
    w = max(1, int(round(_STROKE * k * (size / 16.0) ** 0 * 1.0)))

    def S(pts):
        return [(x * k, y * k) for x, y in pts]

    def bb(b):
        return [b[0] * k, b[1] * k, b[2] * k, b[3] * k]

    def cap(x, y):
        r = w / 2.0
        d.ellipse([x - r, y - r, x + r, y + r], fill=rgb)

    for p in _DEFS[name]:
        kind = p[0]
        if kind == "l":
            pts = S(p[1])
            if p[2]:
                pts = pts + [pts[0], pts[1]]
            d.line(pts, fill=rgb, width=w, joint="curve")
            for x, y in pts:
                cap(x, y)
        elif kind == "r":
            d.rounded_rectangle(bb(p[1]), radius=p[2] * k, outline=rgb, width=w)
        elif kind == "fr":
            d.rounded_rectangle(bb(p[1]), radius=0.6 * k, fill=rgb)
        elif kind == "e":
            d.ellipse(bb(p[1]), outline=rgb, width=w)
        elif kind == "fe":
            d.ellipse(bb(p[1]), fill=rgb)
        elif kind == "a":
            b = bb(p[1])
            d.arc(b, p[2], p[3], fill=rgb, width=w)
            for ang in (p[2], p[3]):
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                rx, ry = (b[2] - b[0]) / 2, (b[3] - b[1]) / 2
                cap(cx + rx * math.cos(math.radians(ang)), cy + ry * math.sin(math.radians(ang)))
    return img.resize((size, size), Image.LANCZOS)


def _to_photo(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def get(name, size=16, color=None):
    """線アイコン。size は論理 px (DPI 倍率をかけて描く)。"""
    if Image is None or name not in _DEFS:
        return None
    col = color or theme.colors()["text2"]
    key = (name, theme.current(), size, col)
    ph = _cache.get(key)
    if ph is None:
        try:
            ph = _to_photo(_render(name, px(size), col))
        except Exception:
            return None
        _cache[key] = ph
    return ph


def _find_app_icon():
    from phonecam.core.paths import WINDOWS_DIR
    import sys
    bases = [getattr(sys, "_MEIPASS", None), WINDOWS_DIR, os.path.dirname(WINDOWS_DIR)]
    for b in bases:
        if not b:
            continue
        for rel in ("assets/icon.png", "docs/icon.png"):
            p = os.path.join(b, rel)
            if os.path.isfile(p):
                return p
    return None


def app_icon(size=20):
    """アプリアイコン (assets/icon.png → 無ければ camera の線画)。"""
    key = ("__app__", size)
    ph = _cache.get(key)
    if ph is not None:
        return ph
    if Image is not None:
        try:
            p = _find_app_icon()
            if p:
                img = Image.open(p).convert("RGBA").resize((px(size), px(size)), Image.LANCZOS)
                ph = _to_photo(img)
        except Exception:
            ph = None
    if ph is None:
        ph = get("camera", size, theme.colors()["accent"])
    if ph is not None:
        _cache[key] = ph
    return ph
