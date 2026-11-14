# -*- coding: utf-8 -*-
"""設定: DEFAULT_SETTINGS・sanitize・load/save・旧設定の移行・録画容量の見積もり・ペアリングコード
"""

import json
import os
import re
import shutil
import threading
from phonecam.core.audio_engine import AUDIO_PRIORITIES
from phonecam.core.deps import np, phonecam_audio, phonecam_hotkeys, sd
from phonecam.core.paths import _app_dir, _appdata_dir
from phonecam.core.softcam_reg import softcam_path
from phonecam.core.wireless import device_id, read_device


CAMERA_DROP_LABELS = [("late", "遅延"), ("outOfBuffers", "バッファ不足"), ("discontinuity", "不連続")]

SETTINGS_FILE = "phonecam_settings.json"


# 設定は exe / ソースのどちらでも %APPDATA%\PhoneCam に保存する
SETTINGS_PATH = os.path.join(_appdata_dir(), SETTINGS_FILE)


def legacy_settings_path():
    """旧バージョンの設定ファイルの場所 (exe またはスクリプトと同じフォルダ)。"""
    return os.path.join(_app_dir(), SETTINGS_FILE)


def migrate_legacy_settings(new_path=None, old_path=None):
    """新しい場所に設定が無く、旧い場所にあればコピーする。コピーしたら True。"""
    new_path = new_path or SETTINGS_PATH
    old_path = old_path or legacy_settings_path()
    try:
        if os.path.exists(new_path) or not os.path.isfile(old_path):
            return False
        if os.path.abspath(new_path) == os.path.abspath(old_path):
            return False
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.copy2(old_path, new_path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "known_devices": [],
    "camera_slot": 1,
    "camera_profiles": [],
    "audio_camera": 0,
    "target_device": "",
    "usb_serial": "",
    "mode": "auto",        # "auto" (USB があれば USB、なければ登録済みの iPhone へ Wi-Fi) / "usb" / "wifi"
    "mode_auto": False,    # 旧設定の mode を auto へ移行済みか
    "ip": "",
    "lens": "wide",
    "resolution": "1080p",       # iPhoneから送る解像度 (仮想カメラのサイズとは無関係)
    "output_size": "1080p",      # 仮想カメラの出力サイズ (長辺×短辺)。iPhoneの解像度が変わっても変えない
    "hw_decode": True,           # ハードウェアデコード (d3d11va) を試す。使えなければ自動でソフトウェア
    "fps": 60,
    "bitrate": 20,
    "adaptive_bitrate": True,  # only sent as enabled on Wi-Fi
    "mirror": False,
    "mirror_front": True,
    "rotate180": False,
    "orientation": "auto",
    "canvas": "follow",
    "fit": "pad",
    "backend": "phonecam" if os.path.isfile(softcam_path()) else "obs",
    "codec": "h264",
    "bg_mode": "off",
    "bg_strength": 0.6,
    "bg_quality": "balanced",
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 0.0,
    "exp_mode": "auto",
    "exp_bias": 0.0,
    "iso": 200,
    "shutter": 100,
    "wb_mode": "auto",
    "temperature": 5000,
    "tint": 0,
    "focus_mode": "auto",
    "focus_pos": 0.5,
    "zoom": 1.0,
    "torch": False,
    # 省電力
    "screen_black": False,
    "screen_auto": 0,
    # iPhone側の録画
    "rec_resolution": "same",
    "rec_bitrate": 50,           # 詳細設定でビットレートを直接指定したときの値 (Mbps)
    "rec_custom_bitrate": False, # True のときだけ rec_bitrate を送る (False は画質プリセットに任せる)
    "rec_preset": "standard",    # 画質プリセット: standard / high / max
    "rec_container": "mp4",      # mp4 / mov
    "rec_audio": True,
    "rec_photos": True,
    # Wi-Fi 接続のペアリングコード (iPhoneの画面に出る6桁。LAN内だけで使う。設定に保存してよい唯一の秘密)
    "pair_code": "",
    # iPhoneのマイク
    "mic_enabled": False,
    "mic_device": "",
    "mic_volume": 100,
    "mic_mute": False,           # PC側だけの消音 (iPhoneからは音が送られ続ける)
    "mic_muted": False,          # iPhoneのマイクミュート (オンの間は音声が送られない)。iPhone側の操作も取り込む
    "mic_delay_ms": 0,
    # ショートカットキー (v1.5): どのアプリを使っていても効く。動作名 → "ctrl+alt+c" (空文字=なし)
    "hotkeys_enabled": True,
    "hotkeys": dict(phonecam_hotkeys.DEFAULT_HOTKEYS) if phonecam_hotkeys is not None else {},
    "mic_exclusive": None,       # WASAPI排他モード。None=自動 (出力先が CABLE Input のときだけ ON)
    "mic_priority": "low",       # "low"=低遅延優先 / "stable"=安定優先
    # iPhoneのカメラを動かすタイミング
    "run_mode": "auto",
    "theme": "system",           # "system" / "light" / "dark"
    "language": "system",        # "system" / "ja" / "en" ...
    "preview_fps": 30,           # アプリ内プレビューの更新fps (15/30/60)。仮想カメラ出力には影響しない
    "preview_width_ratio": 0.6667,  # プレビューの幅 (内容列幅に対する割合)
    "idle_stop_sec": 60,   # 0 = 使われなくなっても止めない
    "window_maximized": True,    # Qt UI: 最大化で起動する
    "ui_expanded": {},           # Qt UI: ページごとの詳細表示状態
    "tk_window_geometry": "",    # Tk UI: 最大化していないときの位置と大きさ ("WxH+X+Y")
    "window_geometry": "",       # Qt UI: 最大化を解除して閉じたときの位置と大きさ (saveGeometry() の base64)
    # 初回セットアップ (ウィザード) を終えたか
    "setup_done": False,
    "setup_shown_version": "",   # セットアップウィザードを最後に出した版 (更新後の初回起動で再表示するため)
    # VB-CABLE 導入前の既定の音声デバイス (導入後に CABLE へ切り替わったら戻すため。戻したら消す)
    "audio_defaults_backup": None,
    "vbcable_restore_pending": 0,
    # 更新の確認
    "update_check": True,        # 起動時と6時間ごとに新しいバージョンを確認する
    "update_skip_version": "",   # 「このバージョンを通知しない」にした版 (例: "1.4.1")
}


RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4K": (3840, 2160)}


BACKENDS = ("phonecam", "obs", "unitycapture")


CODECS = ("h264", "hevc")


BG_MODES = ("off", "blur", "green")


BG_QUALITIES = ("fast", "balanced")


CANVAS_MODES = ("follow", "landscape", "portrait", "native")


FIT_MODES = ("pad", "crop")


# (キー, 最小, 最大) — 範囲外の値は丸める
NUMERIC_RANGES = {
    "bg_strength": (0.0, 1.0),
    "brightness": (-0.5, 0.5),
    "contrast": (0.5, 1.5),
    "saturation": (0.0, 2.0),
    "sharpness": (0.0, 1.0),
}


COLOR_DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 0.0}


LENSES = ("wide", "ultrawide", "front")


SCREEN_AUTO_VALUES = (0, 10, 30, 60)


REC_RESOLUTIONS = ("same", "4k")


REC_PRESETS = ("standard", "high", "max")


REC_CONTAINERS = ("mp4", "mov")


# 画質プリセット別のビットレート (Mbps)。iPhone側の対応表と同じ (HEVC)
REC_PRESET_MBPS = {"1080p": {"standard": 10, "high": 16, "max": 25},
                   "4k": {"standard": 25, "high": 40, "max": 60}}


REC_AUDIO_KBPS = 128             # AAC


PAIR_CODE_LEN = 6


RUN_MODES = ("auto", "always", "manual")


IDLE_STOP_VALUES = (0, 10, 30, 60, 300)   # 0 = 止めない


# 整数の設定 (キー: (最小, 最大))
PREVIEW_FPS_CHOICES = (15, 30, 60)

INT_RANGES = {
    "bitrate": (2, 50),         # 配信のビットレート (Mbps)。設定ファイルの壊れた値・過大な値で iPhone/デコーダーを詰まらせない
    "rec_bitrate": (10, 100),
    "mic_volume": (0, 200),
    "mic_delay_ms": (0, 500),
}


BOOL_KEYS = ("adaptive_bitrate", "screen_black", "rec_audio", "rec_photos", "rec_custom_bitrate", "mic_enabled", "mic_mute", "mic_muted", "setup_done", "hw_decode", "update_check", "hotkeys_enabled")


AUDIO_AVAILABLE = sd is not None and np is not None


def normalize_pair_code(value):
    """ペアリングコードを数字だけ・最大6桁にそろえる (全角数字も半角に直す)。"""
    if not isinstance(value, str):
        return ""
    digits = "".join(str(int(ch)) for ch in value if ch.isdecimal())  # 全角数字は半角に直す
    return digits[:PAIR_CODE_LEN]


def valid_pair_code(value):
    return isinstance(value, str) and len(value) == PAIR_CODE_LEN and value.isascii() and value.isdigit()


def build_auth_control(settings):
    """接続直後に最初に送る認証メッセージ。USB (ループバック) では iPhone が無視するので送っても無害。"""
    return {"auth": {"code": normalize_pair_code(settings.get("pair_code"))}}


AUTH_FAIL_TEXT = "ペアリングコードが違います。iPhoneの画面に表示されている6桁のコードを入力してください"


AUTH_REFUSED_TEXT = ("iPhoneに接続を断られました。iPhoneアプリで「Wi-Fiからの接続を許可」がオンになっているか確認してください"
                     "（コードを何度も間違えた場合は1分ほど待ってください）")


def auth_status_effect(st):
    """iPhone のステータスから認証の状態を読む: "required" (認証待ち) / "ok" (認証済み・認証不要) / None。
    authRequired を出さない古いiPhoneアプリは認証不要として扱う。"""
    if not isinstance(st, dict):
        return None
    if st.get("authRequired") and not st.get("authOk"):
        return "required"
    return "ok"


def auth_failure_kind(mode, authed, saw_required):
    """接続が閉じたときの原因: "code" (コード違い) / "refused" (Wi-Fi拒否など) / None (認証とは無関係)。"""
    if mode != "wifi" or authed:
        return None
    return "code" if saw_required else "refused"


def rec_effective_resolution(settings):
    """録画の実解像度クラス: "4k" か "1080p" (配信と同じ設定のときは配信の解像度に従う。720p は 1080p 扱い)。"""
    if settings.get("rec_resolution") == "4k":
        return "4k"
    if settings.get("rec_resolution") == "same" and settings.get("resolution") == "4K":
        return "4k"
    return "1080p"


def rec_video_mbps(settings):
    """録画の映像ビットレート (Mbps)。詳細設定で指定していればその値、なければ画質プリセットの値。"""
    if settings.get("rec_custom_bitrate"):
        return int(settings.get("rec_bitrate", DEFAULT_SETTINGS["rec_bitrate"]))
    table = REC_PRESET_MBPS[rec_effective_resolution(settings)]
    return table.get(settings.get("rec_preset"), table["standard"])


def rec_mb_per_min(video_mbps, audio=True):
    """1分あたりの録画サイズ (MB)。映像 Mbps × 7.5 MB/分 + 音声 AAC 128 kbps (約1 MB/分)。"""
    bits = video_mbps * 1_000_000 + (REC_AUDIO_KBPS * 1000 if audio else 0)
    return bits / 8.0 * 60.0 / 1_000_000


def rec_estimate_text(settings):
    mbps = rec_video_mbps(settings)
    audio = bool(settings.get("rec_audio", True))
    per_min = rec_mb_per_min(mbps, audio)
    per_hour_gb = per_min * 60.0 / 1000.0
    res = "4K" if rec_effective_resolution(settings) == "4k" else "フルHD"
    detail = "映像 %d Mbps" % mbps + (" + 音声 AAC %d kbps" % REC_AUDIO_KBPS if audio else "")
    return "%s・目安 約 %d MB/分（%s）、1時間で約 %.1f GB" % (res, round(per_min), detail, per_hour_gb)


def format_drop_reasons(reasons):
    """カメラのドロップ理由 (遅延 / バッファ不足 / 不連続) を「遅延 2 / バッファ不足 0 / 不連続 1」の形にする。"""
    if not isinstance(reasons, dict):
        return "-"
    parts = []
    for key, label in CAMERA_DROP_LABELS:
        v = reasons.get(key)
        parts.append("%s %d" % (label, v) if isinstance(v, (int, float)) and not isinstance(v, bool) else "%s -" % label)
    return " / ".join(parts)


def mirror_key(settings):
    """現在のレンズに対応する左右反転の設定キー (インカメだけ別に記憶する)。"""
    return "mirror_front" if settings.get("lens") == "front" else "mirror"


def sanitize_settings(s):
    """読み込んだ設定の新規項目を検証し、不正値は既定値へ戻す (古い設定ファイルとの互換用)。"""
    slot = s.get("camera_slot")
    s["camera_slot"] = slot if type(slot) is int and 1 <= slot <= 4 else 1
    audio = s.get("audio_camera")
    s["audio_camera"] = audio if type(audio) is int and 0 <= audio <= 3 else 0
    profiles = s.get("camera_profiles")
    s["camera_profiles"] = [p for p in profiles[:3] if isinstance(p, dict)] if isinstance(profiles, list) else []
    s["target_device"] = device_id(s.get("target_device"))
    serial = s.get("usb_serial")
    s["usb_serial"] = serial[:128] if isinstance(serial, str) else ""
    known = s.get("known_devices")
    s["known_devices"] = [device.stored() for entry in known[:64] if (device := read_device(entry))] if isinstance(known, list) else []
    expanded = s.get("ui_expanded")
    s["ui_expanded"] = {k: v for k, v in expanded.items() if isinstance(k, str) and isinstance(v, bool)} if isinstance(expanded, dict) else {}
    if s.get("mode") not in ("auto", "usb", "wifi"):
        s["mode"] = "auto"
    if s.get("mode_auto") is not True:   # 自動切り替え導入前の設定は一度だけ auto へ移す (手動選択は「詳細」に残る)
        s["mode"] = "auto"
        s["mode_auto"] = True
    if s.get("lens") not in LENSES:
        s["lens"] = DEFAULT_SETTINGS["lens"]
    for key in ("mirror", "mirror_front"):
        if not isinstance(s.get(key), bool):
            s[key] = DEFAULT_SETTINGS[key]
    for key, allowed in (("backend", BACKENDS), ("codec", CODECS),
                         ("bg_mode", BG_MODES), ("bg_quality", BG_QUALITIES),
                         ("canvas", CANVAS_MODES), ("fit", FIT_MODES)):
        if s.get(key) not in allowed:
            s[key] = DEFAULT_SETTINGS[key]
    for key, (lo, hi) in NUMERIC_RANGES.items():
        try:
            v = float(s[key])
        except Exception:
            v = DEFAULT_SETTINGS[key]
        s[key] = min(hi, max(lo, v))
    # v1.3 追加項目
    for key in BOOL_KEYS:
        if not isinstance(s.get(key), bool):
            s[key] = DEFAULT_SETTINGS[key]
    try:
        auto = int(s.get("screen_auto"))
    except Exception:
        auto = None
    s["screen_auto"] = auto if auto in SCREEN_AUTO_VALUES else DEFAULT_SETTINGS["screen_auto"]
    if s.get("rec_resolution") not in REC_RESOLUTIONS:
        s["rec_resolution"] = DEFAULT_SETTINGS["rec_resolution"]
    if s.get("rec_preset") not in REC_PRESETS:
        s["rec_preset"] = DEFAULT_SETTINGS["rec_preset"]
    if s.get("rec_container") not in REC_CONTAINERS:
        s["rec_container"] = DEFAULT_SETTINGS["rec_container"]
    s["pair_code"] = normalize_pair_code(s.get("pair_code"))
    for key, (lo, hi) in INT_RANGES.items():
        try:
            v = int(round(float(s.get(key))))
        except Exception:
            v = DEFAULT_SETTINGS[key]
        s[key] = min(hi, max(lo, v))
    if not isinstance(s.get("mic_device"), str):
        s["mic_device"] = DEFAULT_SETTINGS["mic_device"]
    if s.get("mic_exclusive") is not None and not isinstance(s.get("mic_exclusive"), bool):
        s["mic_exclusive"] = None
    if s.get("mic_priority") not in AUDIO_PRIORITIES:
        s["mic_priority"] = DEFAULT_SETTINGS["mic_priority"]
    if s.get("theme") not in ("system", "light", "dark"):
        s["theme"] = DEFAULT_SETTINGS["theme"]
    lang = s.get("language")
    if not isinstance(lang, str) or not lang.strip() or len(lang) > 16:
        s["language"] = DEFAULT_SETTINGS["language"]
    try:
        ratio = float(s.get("preview_width_ratio"))
        if ratio != ratio:
            raise ValueError
    except Exception:
        ratio = DEFAULT_SETTINGS["preview_width_ratio"]
    try:
        fps = int(round(float(s.get("preview_fps"))))
    except Exception:
        fps = DEFAULT_SETTINGS["preview_fps"]
    if fps not in PREVIEW_FPS_CHOICES:
        fps = min(PREVIEW_FPS_CHOICES, key=lambda c: abs(c - fps)) if 0 < fps < 10000 else DEFAULT_SETTINGS["preview_fps"]
    s["preview_fps"] = fps
    s["preview_width_ratio"] = min(1.0, max(1.0 / 3.0, ratio))
    s["window_maximized"] = bool(s.get("window_maximized", True))
    if not isinstance(s.get("window_geometry"), str) or len(s["window_geometry"]) > 4096:
        s["window_geometry"] = ""
    tg = s.get("tk_window_geometry")
    if not isinstance(tg, str) or not re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", tg):
        s["tk_window_geometry"] = ""
    # カメラの動かし方
    if s.get("run_mode") not in RUN_MODES:
        s["run_mode"] = DEFAULT_SETTINGS["run_mode"]
    try:
        idle = int(s.get("idle_stop_sec"))
    except Exception:
        idle = None
    s["idle_stop_sec"] = idle if idle in IDLE_STOP_VALUES else DEFAULT_SETTINGS["idle_stop_sec"]
    # v1.4 追加: 出力サイズ (古い設定では持っていない → 解像度に関係なく 1080p。仮想カメラのサイズは固定)
    if s.get("output_size") not in RESOLUTIONS:
        s["output_size"] = DEFAULT_SETTINGS["output_size"]
    if not isinstance(s.get("update_skip_version"), str) or len(s["update_skip_version"]) > 40:
        s["update_skip_version"] = DEFAULT_SETTINGS["update_skip_version"]
    if not isinstance(s.get("setup_shown_version"), str) or len(s["setup_shown_version"]) > 40:
        s["setup_shown_version"] = DEFAULT_SETTINGS["setup_shown_version"]
    # v1.5 追加: ショートカットキー
    if phonecam_hotkeys is not None:
        s["hotkeys"] = phonecam_hotkeys.sanitize_hotkeys(s.get("hotkeys"))
    else:
        s["hotkeys"] = {}
    # 音声デバイスの控え
    try:
        pend = int(s.get("vbcable_restore_pending"))
    except Exception:
        pend = 0
    s["vbcable_restore_pending"] = min(5, max(0, pend))
    if phonecam_audio is None or not phonecam_audio.valid_backup(s.get("audio_defaults_backup")):
        s["audio_defaults_backup"] = None
        s["vbcable_restore_pending"] = 0
    return s


def load_settings(path=SETTINGS_PATH):
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_SETTINGS:
                if k in data:
                    s[k] = data[k]
    except Exception:
        pass
    return sanitize_settings(s)


def settings_has_key(key, path=None):
    """設定ファイルが存在し、その中に key があるか。(ファイルなし・読めない場合は None)"""
    path = path or SETTINGS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and key in data
    except Exception:
        return None


_SAVE_LOCK = threading.Lock()  # 設定は UI スレッドと音声デバイスの復元スレッドの両方から保存される


def save_settings(settings, path=SETTINGS_PATH):
    try:
        with _SAVE_LOCK:
            path = os.fspath(path)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
    except Exception:
        try:
            from phonecam.core import crashlog
            crashlog.log_error("save_settings")
        except Exception:
            pass
