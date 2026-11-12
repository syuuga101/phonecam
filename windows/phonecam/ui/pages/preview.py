# -*- coding: utf-8 -*-
"""接続ページのライブプレビュー (受信した映像の縮小表示 + 右下のつまみで拡縮)

受信側 (core) には UI 向けのフレーム受け渡しが無いので、仮想カメラ出力の send を
1 枚分の参照を保存するだけのラッパーで包み、UI タイマーで間引いて描画する。
core は変更しない。
"""

import time

from phonecam.core.deps import np
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.dpi import px
from phonecam.ui.tkdeps import tk

RATIO_MIN = 1.0 / 3.0
RATIO_MAX = 1.0
RATIO_DEFAULT = 2.0 / 3.0
TICK_MS = 66          # 約15fps で描画 (プレビューは軽く)
STALE_SEC = 1.5       # これより古い映像は「映像なし」として扱う
GRIP = 14             # つまみの大きさ (px)


def clamp_ratio(v):
    try:
        v = float(v)
    except Exception:
        v = RATIO_DEFAULT
    return max(RATIO_MIN, min(RATIO_MAX, v))


def hook_frames(app):
    """vcam.send を包んで最新フレームの参照を app._preview_frame に置く。
    Link が vcam を作り直しても、毎tickで呼べば包み直す (二重には包まない)。"""
    try:
        vc = getattr(getattr(app, "link", None), "vcam", None)
        if vc is None:
            return
        orig = vc.send
        if getattr(orig, "_pc_wrapped", False):
            return
    except Exception:
        return
    if not hasattr(app, "_preview_seq"):
        app._preview_frame = None  # (img, fmt, monotonic time)
        app._preview_seq = 0

    def send(img, fps):
        try:
            app._preview_frame = (img, vc.frame_format(), time.monotonic())
            app._preview_seq += 1
        except Exception:
            pass
        return orig(img, fps)

    send._pc_wrapped = True
    try:
        vc.send = send
    except Exception:
        pass


def _to_ppm(img, fmt, tw, th):
    """img (H, W, 3) を (tw, th) に収まるよう縮小し、PPM(P6, RGB) の bytes を返す。"""
    h, w = img.shape[:2]
    if h < 2 or w < 2:
        return None
    s = min(tw / float(w), th / float(h))
    nw, nh = max(2, int(w * s)), max(2, int(h * s))
    ys = (np.arange(nh) * h) // nh
    xs = (np.arange(nw) * w) // nw
    small = img[ys[:, None], xs[None, :]]
    if small.ndim == 2:
        small = np.stack([small] * 3, axis=2)
    elif small.shape[2] == 1:
        small = np.repeat(small, 3, axis=2)
    elif small.shape[2] > 3:
        small = small[:, :, :3]  # アルファを捨てる
    if fmt == "bgr24":
        small = small[:, :, ::-1]
    return b"P6 %d %d 255\n" % (nw, nh) + np.ascontiguousarray(small).tobytes()


class Preview:
    def __init__(self, app, parent):
        self.app = app
        self.ratio = clamp_ratio(app.settings.get("preview_width_ratio", RATIO_DEFAULT))
        self.box = tk.Frame(parent, highlightthickness=0, borderwidth=0)
        self.box.pack(fill="x", pady=(12, 0))
        self.canvas = tk.Canvas(self.box, highlightthickness=0, borderwidth=0, width=px(320), height=px(180))
        self.canvas.pack(anchor="w")
        self._photo = None
        self._seq = -1
        self._drag = False
        self._width = px(320)
        self.box.bind("<Configure>", lambda e: self.relayout())
        self.canvas.tag_bind("grip", "<B1-Motion>", self._on_drag)
        self.canvas.tag_bind("grip", "<ButtonRelease-1>", self._on_release)
        self.canvas.tag_bind("grip", "<Enter>", lambda e: self.canvas.config(cursor="size_nw_se"))
        self.canvas.tag_bind("grip", "<Leave>", lambda e: self.canvas.config(cursor=""))
        theme.on_change(self._recolor)
        self._recolor()
        self.tick()

    # -- 見た目 -------------------------------------------------------------
    def _recolor(self, *_):
        try:
            c = theme.colors()
            self.box.config(bg=c["bg"])
            self.canvas.config(bg=c["card"])
            self.redraw_static()
        except Exception:
            pass

    def relayout(self):
        try:
            cw = max(px(160), self.box.winfo_width())
            w = int(cw * self.ratio)
            h = int(w * 9 / 16)
            self._width = w
            self.canvas.config(width=w, height=h)
            self.redraw_static()
            self._seq = -1  # 次のタイマーで再描画
        except Exception:
            pass

    def redraw_static(self):
        """プレースホルダー文言とつまみを描き直す (映像が出ている間は映像の上につまみだけ)。"""
        c = theme.colors()
        cv = self.canvas
        w, h = int(cv["width"]), int(cv["height"])
        cv.delete("grip")
        cv.delete("ph")
        if self._photo is None:
            cv.create_text(w // 2, h // 2, text=t("connection.preview.empty"), fill=c["text2"], tags="ph")
        g = px(GRIP)
        for k in (1, 2, 3):  # 斜線3本のつまみ
            d = k * g // 3
            cv.create_line(w - 2, h - d, w - d, h - 2, fill=c["text2"], width=1, tags="grip")
        cv.create_rectangle(w - g, h - g, w, h, outline="", fill="", tags="grip")  # つかみやすい当たり判定
        cv.tag_raise("grip")

    # -- ドラッグで拡縮 -----------------------------------------------------------
    def _on_drag(self, e):
        cw = max(1, self.box.winfo_width())
        x = e.x_root - self.box.winfo_rootx()
        self.ratio = clamp_ratio(x / float(cw))
        self._drag = True
        self.relayout()

    def _on_release(self, _e):
        if not self._drag:
            return
        self._drag = False
        try:
            self.app._changed("preview_width_ratio", round(self.ratio, 4), None)
        except Exception:
            pass

    # -- 映像 ----------------------------------------------------------------
    def tick(self):
        app = self.app
        if getattr(app, "_closing", False):
            return
        try:
            if not self.canvas.winfo_exists():
                return
            hook_frames(app)
            self._draw_frame()
        except Exception:
            pass
        try:
            self.canvas.after(TICK_MS, self.tick)
        except Exception:
            pass

    def _draw_frame(self):
        app = self.app
        if np is None or not self.canvas.winfo_ismapped():
            return
        item = getattr(app, "_preview_frame", None)
        live = item is not None and time.monotonic() - item[2] < STALE_SEC and app.state.snapshot()["connected"]
        if not live:
            if self._photo is not None:
                self._photo = None
                self.canvas.delete("img")
                self.redraw_static()
            self._seq = -1
            return
        seq = app._preview_seq
        if seq == self._seq:
            return
        self._seq = seq
        img, fmt, _t = item
        cw, ch = int(self.canvas["width"]), int(self.canvas["height"])
        data = _to_ppm(img, fmt, cw, ch)
        if data is None:
            return
        first = self._photo is None
        self._photo = tk.PhotoImage(data=data, format="PPM")
        self.canvas.delete("img")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo, tags="img")
        self.canvas.tag_lower("img")
        if first:
            self.canvas.delete("ph")


def build_preview(app, parent):
    hook_frames(app)
    app.preview = Preview(app, parent)
    return app.preview.box
