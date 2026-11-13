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


def _draw_prims(d, name, k, ox, oy, rgb, w):
    """設計グリッド 24 の線画 name を、倍率 k・原点 (ox, oy)・線幅 w(px) で d に描く。"""
    def S(pts):
        return [(ox + x * k, oy + y * k) for x, y in pts]

    def bb(b):
        return [ox + b[0] * k, oy + b[1] * k, ox + b[2] * k, oy + b[3] * k]

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


def _render(name, size, color):
    """設計グリッド 24 を size px に描いた RGBA 画像。"""
    big = size * _SS
    k = big / _G
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = max(1, int(round(_STROKE * k)))
    _draw_prims(d, name, k, 0, 0, _hex_rgb(color) + (255,), w)
    return img.resize((size, size), Image.LANCZOS)


# カメラ線画 (camera) の外形: x 2.5..21.5, y 4.5..20 (設計グリッド)
_CAM_W, _CAM_CX, _CAM_CY = 19.0, 12.0, 12.25
_PLATE = "#F2F2F2"
_GLYPH_DARK = "#1C1C1E"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def camera_glyph(size, color, span=0.62, plate=None, stroke=None):
    """カメラ線画を size px で描く。span = 画像幅に対するグリフ幅、plate = 角丸の下地色 (None なら透明)。
    線幅はサイズごとに決める (小さいサイズでも潰れない最小値あり)。"""
    ss = 8 if size <= 64 else 4
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if plate:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=_hex_rgb(plate) + (255,))
    k = span * big / _CAM_W
    ox = big / 2.0 - _CAM_CX * k
    oy = big / 2.0 - _CAM_CY * k
    if stroke is None:
        stroke = max(1.3, size * 0.062)   # px (16->1.3, 32->2.0, 256->15.9)
    w = max(1, int(round(stroke * ss)))
    _draw_prims(d, "camera", k, ox, oy, _hex_rgb(color) + (255,), w)
    return img.resize((size, size), Image.LANCZOS)


def app_icon_image(size):
    """タスクバー/ウィンドウ/exe 用: 明るい角丸プレート + 黒の線画カメラ (サイズごとに描く)。"""
    return camera_glyph(size, _GLYPH_DARK, span=0.62, plate=_PLATE)


def write_icon_assets(out_dir):
    """assets/icon_app.png (256) と assets/icon.ico (16..256 の複数サイズ) を書き出す。"""
    os.makedirs(out_dir, exist_ok=True)
    imgs = {s: app_icon_image(s) for s in ICON_SIZES}
    imgs[256].save(os.path.join(out_dir, "icon_app.png"))
    imgs[256].save(os.path.join(out_dir, "icon.ico"), format="ICO", sizes=[(s, s) for s in ICON_SIZES],
                   append_images=[imgs[s] for s in ICON_SIZES if s != 256])


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


def _asset_dirs():
    from phonecam.core.paths import WINDOWS_DIR
    import sys
    return [d for d in (os.path.join(getattr(sys, "_MEIPASS", "") or "", "assets") if getattr(sys, "_MEIPASS", None) else None,
                        os.path.join(WINDOWS_DIR, "assets"),
                        os.path.join(os.path.dirname(WINDOWS_DIR), "assets")) if d]


def _find_asset(name):
    for d in _asset_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def app_icon(size=20):
    """ナビ左上のアイコン: 下地なし・テーマの文字色のカメラ線画 (テーマごとにキャッシュ)。"""
    col = theme.colors()["text"]
    key = ("__app__", size, theme.current(), col)
    ph = _cache.get(key)
    if ph is not None:
        return ph
    if Image is not None:
        try:
            ph = _to_photo(camera_glyph(px(size), col, span=0.92, stroke=max(1.4, px(size) * 0.085)))
        except Exception as e:
            theme._log("app_icon: %r" % (e,))
            ph = None
    if ph is None:
        ph = get("camera", size, col)
    if ph is not None:
        _cache[key] = ph
    return ph


_window_icons = []   # PhotoImage を GC させない


def set_window_icon(root):
    """ウィンドウ/タスクバーのアイコンを PhoneCam にする。
    (1) iconphoto: サイズごとに PIL で描いた PNG を tk.PhotoImage(data=) で渡す (PIL.ImageTk は使わない)
    (2) iconbitmap: 同梱の assets/icon.ico (凍結 exe では sys._MEIPASS/assets)、無ければ PIL で一時 .ico を作る
    どちらかが失敗しても残りを実行し、失敗は phonecam_ui.log に残す。"""
    ok = False
    if Image is not None:
        try:
            photos = [_to_photo(app_icon_image(s)) for s in ICON_SIZES]
            _window_icons.extend(photos)
            root.iconphoto(True, *photos)
            ok = True
        except Exception as e:
            theme._log("iconphoto(data): %r" % (e,))
    if not ok:
        p = _find_asset("icon_app.png")
        if p:
            try:
                ph = tk.PhotoImage(file=p)
                _window_icons.append(ph)
                root.iconphoto(True, ph)
                ok = True
            except Exception as e:
                theme._log("iconphoto(file): %r" % (e,))
    if os.name == "nt":
        ico = _find_asset("icon.ico")
        try:
            if not ico and Image is not None:
                import tempfile
                ico = os.path.join(tempfile.gettempdir(), "phonecam_icon.ico")
                write_icon_assets(os.path.dirname(ico) + os.sep + "phonecam_icon_tmp")
                import shutil
                shutil.copyfile(os.path.join(os.path.dirname(ico), "phonecam_icon_tmp", "icon.ico"), ico)
            if ico and os.path.isfile(ico):
                try:
                    root.iconbitmap(default=ico)
                    ok = True
                except Exception as e:
                    theme._log("iconbitmap(default): %r" % (e,))
                try:
                    root.wm_iconbitmap(ico)
                    ok = True
                except Exception as e:
                    theme._log("wm_iconbitmap: %r" % (e,))
        except Exception as e:
            theme._log("ico: %r" % (e,))
    return ok
