# -*- coding: utf-8 -*-
"""出力キャンバス・フィット計算・iPhone へ送る control メッセージの組み立て
"""

from phonecam.core.deps import np
from phonecam.core.settings import AUDIO_AVAILABLE, FIT_MODES, RESOLUTIONS


def output_canvas_control(settings, frame_size=None):
    """iPhone の枠ガイド用: 仮想カメラの出力サイズと合わせ方。native のときは最後に受けたフレームのサイズ (無ければ None)。"""
    size = canvas_size(settings) or frame_size
    if not size:
        return None
    fit = settings.get("fit", "pad")
    return {"width": int(size[0]), "height": int(size[1]), "fit": fit if fit in FIT_MODES else "pad"}


BITRATE_MIN_MBPS = 2


BITRATE_MAX_MBPS = 50   # UI のスライダー上限。これを超える値 (壊れた設定ファイルなど) は送らない


def clamp_bitrate(value, default=20):
    """配信ビットレート (Mbps) を安全な範囲 (2〜50) に収める。数値でない値は既定値。"""
    try:
        v = int(round(float(value)))
    except Exception:
        v = default
    return min(BITRATE_MAX_MBPS, max(BITRATE_MIN_MBPS, v))


def build_control(settings, group, frame_size=None):
    """設定から制御 JSON を作る。group='all' で全項目。frame_size: 最後にデコードしたフレームの (幅, 高さ)。"""
    c = {}
    if group in ("all", "video", "canvas"):
        oc = output_canvas_control(settings, frame_size)
        if oc is not None:
            c["outputCanvas"] = oc
    if group in ("all", "video"):
        w, h = RESOLUTIONS.get(settings["resolution"], (1920, 1080))
        c["lens"] = settings["lens"]
        c["width"] = w
        c["height"] = h
        c["fps"] = int(settings["fps"])
        c["orientation"] = settings.get("orientation", "auto")
    if group in ("all", "codec"):
        c["codec"] = settings.get("codec", "h264")
        if group == "codec":
            c["keyframe"] = True
    if group in ("all", "background"):
        c["background"] = {
            "mode": settings["bg_mode"],
            "strength": float(settings["bg_strength"]),
            "quality": settings["bg_quality"],
        }
    if group in ("all", "color"):
        c["color"] = {
            "brightness": float(settings["brightness"]),
            "contrast": float(settings["contrast"]),
            "saturation": float(settings["saturation"]),
            "sharpness": float(settings["sharpness"]),
        }
    if group in ("all", "bitrate"):
        c["bitrate"] = clamp_bitrate(settings.get("bitrate"))
    if group in ("all", "exposure"):
        c["exposure"] = {
            "mode": settings["exp_mode"],
            "bias": float(settings["exp_bias"]),
            "iso": int(settings["iso"]),
            "shutter": int(settings["shutter"]),
        }
    if group in ("all", "wb"):
        c["wb"] = {
            "mode": settings["wb_mode"],
            "temperature": int(settings["temperature"]),
            "tint": int(settings["tint"]),
        }
    if group in ("all", "focus"):
        c["focus"] = {"mode": settings["focus_mode"], "position": float(settings["focus_pos"])}
    if group in ("all", "zoom"):
        c["zoom"] = float(settings["zoom"])
    if group in ("all", "torch"):
        c["torch"] = bool(settings["torch"])
    if group in ("all", "screen"):
        c["screen"] = {"black": bool(settings["screen_black"]),
                       "autoBlackSeconds": int(settings["screen_auto"])}
    if group in ("all", "recordSettings"):
        c["recordSettings"] = {
            "resolution": settings["rec_resolution"],
            "preset": settings.get("rec_preset", "standard"),
            "container": settings.get("rec_container", "mp4"),
            "includeAudio": bool(settings["rec_audio"]),
            "saveToPhotos": bool(settings["rec_photos"]),
        }
        if settings.get("rec_custom_bitrate"):
            # 詳細設定で直接指定したときだけ送る (iPhone側は bitrate があればプリセットより優先する)
            c["recordSettings"]["bitrate"] = int(settings["rec_bitrate"])
    if group in ("all", "mic"):
        # sounddevice が無いと再生できないので、iPhone側でも有効にしない
        c["mic"] = {"enabled": bool(settings["mic_enabled"]) and AUDIO_AVAILABLE}
    if group in ("all", "micMuted"):
        c["micMuted"] = bool(settings.get("mic_muted", False))
    return c


# ---------------------------------------------------------------------------
# 出力サイズの固定 (向きが変わっても仮想カメラを作り直さない)
# ---------------------------------------------------------------------------

def canvas_size(settings):
    """仮想カメラの出力サイズ (幅, 高さ)。native のときは None (= フレームのサイズそのまま)。"""
    mode = settings.get("canvas", "follow")
    if mode == "native":
        return None
    # 仮想カメラのサイズは「iPhoneから送る解像度」とは独立。解像度を変えてもカメラは作り直さない
    long_side, short_side = RESOLUTIONS.get(settings.get("output_size"), (1920, 1080))
    if mode == "follow":
        mode = "portrait" if settings.get("orientation", "auto") == "portrait" else "landscape"
    if mode == "portrait":
        return (short_side, long_side)
    return (long_side, short_side)


def _even(x):
    return max(2, int(round(x / 2.0)) * 2)


def fit_geometry(fw, fh, cw, ch, fit):
    """(fw x fh) のフレームを (cw x ch) に合わせるときの (拡縮後の幅, 高さ, x, y)。
    pad: 全体が収まる最大サイズで中央に置く位置 (x, y >= 0)。
    crop: 全体を覆う最小サイズで、中央を切り出す開始位置 (x, y >= 0)。"""
    if fit == "crop":
        scale = max(cw / float(fw), ch / float(fh))
        sw, sh = max(_even(fw * scale), cw), max(_even(fh * scale), ch)
        return sw, sh, (sw - cw) // 2, (sh - ch) // 2
    scale = min(cw / float(fw), ch / float(fh))
    sw, sh = min(_even(fw * scale), cw), min(_even(fh * scale), ch)
    return sw, sh, (cw - sw) // 2, (ch - sh) // 2


class CanvasFitter:
    """フレームを固定サイズのキャンバスに収める。PyAV の reformat で拡縮するので numpy では拡縮しない。"""

    def __init__(self):
        self._buf = None
        self._key = None

    def process(self, frame, fmt, canvas, fit, pad_color):
        """frame: av.VideoFrame, canvas: (幅, 高さ), pad_color: (v0, v1, v2) ※0/255のみ使うので RGB/BGR 共通。
        戻り値: (ch, cw, 3) の uint8 配列。"""
        cw, ch = canvas
        sw, sh, ox, oy = fit_geometry(frame.width, frame.height, cw, ch, fit)
        # 縮小・形式変換は reformat の1回で済ませる (4K → 1080 でも余計なコピーをしない)
        arr = frame.reformat(width=sw, height=sh, format=fmt).to_ndarray()
        if (sw, sh) == (cw, ch):
            return arr
        if fit == "crop":
            return arr[oy:oy + ch, ox:ox + cw]
        key = (cw, ch, tuple(pad_color), sw, sh)
        if self._buf is None or self._key != key:
            # 余白の色・キャンバス・貼り付け範囲が変わったときだけ塗り直す
            if self._buf is None or self._buf.shape != (ch, cw, 3):
                self._buf = np.empty((ch, cw, 3), dtype=np.uint8)
            self._buf[:, :] = pad_color
            self._key = key
        self._buf[oy:oy + sh, ox:ox + sw] = arr
        return self._buf


def pad_color_for(settings):
    """余白の色。背景の切り抜き(緑)中は、クロマキーが効くよう純緑 (RGBでもBGRでも同じ並び)。"""
    return (0, 255, 0) if settings.get("bg_mode") == "green" else (0, 0, 0)
