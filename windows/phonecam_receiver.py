# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ

iPhone (PhoneCam アプリ) から H.264 / HEVC 映像を受信してデコードし、
仮想カメラ (PhoneCam 専用カメラ / OBS Virtual Camera / Unity Capture) へ出力します。
"""

import ctypes
import hashlib
import json
import os
import plistlib
import shutil
import socket
import struct
import subprocess
import sys
try:
    from phonecam_version import VERSION as APP_VERSION  # ビルド時に生成される
except Exception:
    APP_VERSION = "dev"
import threading
import time
from collections import deque

try:
    import phonecam_setup  # セットアップ (必要ソフトの検出・インストール・ウィザード)
except Exception:  # pragma: no cover
    phonecam_setup = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
try:
    import av
except Exception:  # pragma: no cover
    av = None
try:
    import pyvirtualcam
except Exception:  # pragma: no cover
    pyvirtualcam = None
try:
    import sounddevice as sd  # iPhoneのマイク再生用 (無くても映像は使える)
except Exception:  # pragma: no cover
    sd = None

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception:  # pragma: no cover
    tk = None
    ttk = None
    messagebox = None

PHONE_PORT = 5555
USBMUX_ADDR = ("127.0.0.1", 27015)
RECONNECT_SEC = 2.0

MSG_VIDEO = 1        # H.264 アクセスユニット
MSG_VIDEO_HEVC = 2   # HEVC アクセスユニット (Annex B)
MSG_STATUS = 3
MSG_PONG = 4         # iPhone → PC: ping への応答 (JSON)
MSG_TIMING = 5       # iPhone → PC: 次の映像パケットの時刻情報 (16 byte)
MSG_AUDIO = 6        # iPhone → PC: マイク音声 (int16 PCM)
MSG_CONTROL = 10

def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _app_dir():
    """ソース実行時はスクリプトのあるフォルダ。exe 化されているときは exe と同じ場所。"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _appdata_dir():
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "PhoneCam")


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


# 専用仮想カメラ (softcam)。CLSID は .github/workflows/windows.yml でパッチして埋め込むものと同一
SOFTCAM_DLL = "softcam.dll"
SOFTCAM_DLL32 = "softcam32.dll"
PHONECAM_CLSID = "{BD7AEDAC-501F-4685-9B20-BC10F70BD144}"
# softcam へ渡す画像の行順。True = 上から下 (softcam 側が DIB 用に反転する)。逆さまに映るなら False にする
SOFTCAM_TOP_DOWN = True


def _embedded_dir():
    """exe に埋め込んだ DLL の場所 (PyInstaller の展開先 \\softcam)。ソース実行時はスクリプトのフォルダ。"""
    if _is_frozen():
        return os.path.join(getattr(sys, "_MEIPASS", _app_dir()), "softcam")
    return _app_dir()


def softcam_path():
    """埋め込み (ソース実行時はスクリプトの隣) の softcam.dll。"""
    return os.path.join(_embedded_dir(), SOFTCAM_DLL)


def softcam32_path():
    return os.path.join(_embedded_dir(), SOFTCAM_DLL32)


def _file_hash12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def softcam_install_root():
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return os.path.join(base, "PhoneCam", "softcam")


def softcam_install_dir(hash12):
    return os.path.join(softcam_install_root(), hash12)


def softcam_installed_path():
    """埋め込み DLL の現在のバージョン用のインストール先 DLL パス。埋め込みが無ければ None。"""
    try:
        return os.path.join(softcam_install_dir(_file_hash12(softcam_path())), SOFTCAM_DLL)
    except Exception:
        return None


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def softcam_registered_path():
    """レジストリに登録されている PhoneCam カメラの DLL パス。未登録なら None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID\\%s\\InprocServer32" % PHONECAM_CLSID) as k:
            return winreg.QueryValueEx(k, "")[0]
    except Exception:
        return None


def registration_status():
    """exe 版での登録状態。"ok" / "missing"(未登録) / "outdated"(更新が必要) / "unavailable"(埋め込み DLL なし)。"""
    expected = softcam_installed_path()
    if expected is None:
        return "unavailable"
    reg = softcam_registered_path()
    if not reg:
        return "missing"
    if _same_path(reg, expected) and os.path.isfile(expected):
        return "ok"
    return "outdated"


def softcam_load_path():
    """送る側が読み込む softcam.dll。登録済み(OK)ならその DLL、そうでなければ埋め込みの DLL。"""
    if _is_frozen():
        try:
            if registration_status() == "ok":
                return softcam_installed_path()
        except Exception:
            pass
    return softcam_path()


def register_hint():
    """登録が必要なときの案内文。"""
    if _is_frozen():
        return "「接続・映像」タブの「登録する（更新）」ボタンを押してください"
    return "register.bat を実行してください（管理者権限が必要です）"


_CREATE_NO_WINDOW = 0x08000000


def _regsvr32_paths():
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return (os.path.join(root, "System32", "regsvr32.exe"),
            os.path.join(root, "SysWOW64", "regsvr32.exe"))


def _run_regsvr32(exe, dll, unregister=False):
    args = [exe, "/u", "/s", dll] if unregister else [exe, "/s", dll]
    r = subprocess.run(args, creationflags=_CREATE_NO_WINDOW, timeout=60)
    return r.returncode == 0


def _cleanup_old_install_dirs(keep_hash=None):
    """他のハッシュのフォルダを消す (OBS が使用中だと消せないので失敗は無視)。"""
    root = softcam_install_root()
    try:
        names = os.listdir(root)
    except Exception:
        return
    for n in names:
        if keep_hash is not None and n == keep_hash:
            continue
        shutil.rmtree(os.path.join(root, n), ignore_errors=True)


def helper_register():
    """管理者権限で実行される --register。成功なら True。"""
    src = softcam_path()
    h = _file_hash12(src)
    dst_dir = softcam_install_dir(h)
    os.makedirs(dst_dir, exist_ok=True)
    pairs = [(src, os.path.join(dst_dir, SOFTCAM_DLL))]
    if os.path.isfile(softcam32_path()):
        pairs.append((softcam32_path(), os.path.join(dst_dir, SOFTCAM_DLL32)))
    for s, d in pairs:
        if os.path.isfile(d) and _file_hash12(d) == _file_hash12(s):
            continue  # 同じ内容なら上書きしない (使用中でも失敗しない)
        shutil.copyfile(s, d)
    x64, x86 = _regsvr32_paths()
    if not _run_regsvr32(x64, pairs[0][1]):
        return False
    if len(pairs) > 1:
        _run_regsvr32(x86, pairs[1][1])  # 32bit は失敗しても致命的ではない
    _cleanup_old_install_dirs(keep_hash=h)
    return True


def helper_unregister():
    """管理者権限で実行される --unregister。成功なら True。"""
    ok = True
    reg = softcam_registered_path()
    x64, x86 = _regsvr32_paths()
    if reg and os.path.isfile(reg):
        ok = _run_regsvr32(x64, reg, unregister=True)
        dll32 = os.path.join(os.path.dirname(reg), SOFTCAM_DLL32)
        if os.path.isfile(dll32):
            _run_regsvr32(x86, dll32, unregister=True)
    elif reg:
        # 登録先の DLL が消えている: 現行のインストール先にあればそれで解除を試みる
        inst = softcam_installed_path()
        if inst and os.path.isfile(inst):
            ok = _run_regsvr32(x64, inst, unregister=True)
        else:
            ok = False
    _cleanup_old_install_dirs()
    return ok


def _error_box(text):
    try:
        r = tk.Tk() if tk is not None else None
        if r is not None:
            r.withdraw()
            messagebox.showerror("PhoneCam", text)
            r.destroy()
    except Exception:
        pass


def helper_main(argv):
    """--register / --unregister が渡されたときの処理 (GUI なし)。それ以外なら None。"""
    args = list(argv)
    if "--setup" in args:
        # 管理者権限で実行されるセットアップ (job.json の内容を順に実行して result.json に書く)
        try:
            job = args[args.index("--setup") + 1]
            sha = args[args.index("--sha256") + 1] if "--sha256" in args else None
            if phonecam_setup is None:
                return 1
            return phonecam_setup.run_job(job, expected_sha=sha)
        except Exception as e:
            _error_box("セットアップの実行に失敗しました。\n\n詳細: %s" % e)
            return 1
    if "--register" in args:
        action, fn, msg = "register", helper_register, "専用カメラ「PhoneCam」の登録に失敗しました。"
    elif "--unregister" in args:
        action, fn, msg = "unregister", helper_unregister, "専用カメラ「PhoneCam」の登録解除に失敗しました。"
    else:
        return None
    try:
        ok = fn()
    except Exception as e:
        _error_box("%s\n\n詳細: %s" % (msg, e))
        return 1
    if not ok:
        _error_box(msg)
        return 1
    return 0


class _SHELLEXECUTEINFOW(ctypes.Structure):
    # Win32 の SHELLEXECUTEINFOW (64bit で 112 byte)。型は wintypes に頼らず固定幅で定義する
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_ERROR_CANCELLED = 1223
_WAIT_OBJECT_0 = 0
ELEVATED_TIMEOUT_MS = 180000


def _win_dll(name):
    return ctypes.WinDLL(name, use_last_error=True)


def run_elevated(params, timeout_ms=ELEVATED_TIMEOUT_MS):
    """自分自身を管理者権限で params 付きで起動し、終了まで待つ。
    戻り値: ("ok", 0) / ("failed", exit_code) / ("cancelled", 0) / ("timeout", 0) / ("error", win_error)。ブロックするのでワーカースレッドで呼ぶ。"""
    shell32 = _win_dll("shell32")
    kernel32 = _win_dll("kernel32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = params
    info.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        return ("cancelled", 0) if err == _ERROR_CANCELLED else ("error", err)
    if not info.hProcess:
        return ("error", 0)
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout_ms)) != _WAIT_OBJECT_0:
            return ("timeout", 0)
        code = ctypes.c_uint32(1)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return ("ok", 0) if code.value == 0 else ("failed", int(code.value))
    finally:
        kernel32.CloseHandle(info.hProcess)


class PhoneCamError(Exception):
    """利用者に見せる日本語メッセージ付きのエラー"""


# ---------------------------------------------------------------------------
# フレーミング
# ---------------------------------------------------------------------------

def encode_msg(msg_type, payload):
    return struct.pack(">BI", msg_type, len(payload)) + payload


def read_exact(sock, n, stop_event=None):
    """ちょうど n バイト読む。タイムアウト時は stop_event を確認して継続する。"""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        try:
            r = sock.recv_into(view[got:], n - got)
        except socket.timeout:
            if stop_event is not None and stop_event.is_set():
                raise ConnectionAbortedError("stopped")
            continue
        if r == 0:
            raise ConnectionError("接続が閉じられました")
        got += r
    return bytes(buf)


def read_msg(sock, stop_event=None):
    header = read_exact(sock, 5, stop_event)
    msg_type, length = struct.unpack(">BI", header)
    if length > 64 * 1024 * 1024:
        raise ConnectionError("不正なメッセージ長です")
    payload = read_exact(sock, length, stop_event) if length else b""
    return msg_type, payload


# ---------------------------------------------------------------------------
# usbmux
# ---------------------------------------------------------------------------

def usbmux_pack(payload_dict, tag):
    body = plistlib.dumps(payload_dict, fmt=plistlib.FMT_XML)
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body


def usbmux_recv(sock):
    header = read_exact(sock, 16)
    length, _version, _msg, _tag = struct.unpack("<IIII", header)
    body = read_exact(sock, length - 16) if length > 16 else b""
    return plistlib.loads(body)


def usbmux_connect(port=PHONE_PORT, mux_addr=USBMUX_ADDR, timeout=5.0):
    """usbmux 経由で USB 接続した iPhone のポートへ接続し、トンネル化したソケットを返す。"""
    try:
        sock = socket.create_connection(mux_addr, timeout=timeout)
    except OSError:
        raise PhoneCamError(
            "Apple Mobile Device Service に接続できません。"
            "Apple Devicesアプリ（またはiTunes）をインストールしてください"
        )
    try:
        sock.settimeout(timeout)
        base = {"ClientVersionString": "phonecam", "ProgName": "phonecam"}

        req = dict(base)
        req["MessageType"] = "ListDevices"
        sock.sendall(usbmux_pack(req, 1))
        resp = usbmux_recv(sock)
        devices = [d for d in resp.get("DeviceList", [])
                   if d.get("Properties", {}).get("ConnectionType") == "USB"]
        if not devices:
            raise PhoneCamError(
                "iPhoneがUSBで見つかりません。ケーブルとロック解除・このコンピュータを信頼を確認"
            )
        device_id = devices[0]["DeviceID"]

        req = dict(base)
        req["MessageType"] = "Connect"
        req["DeviceID"] = device_id
        req["PortNumber"] = socket.htons(port)
        sock.sendall(usbmux_pack(req, 2))
        resp = usbmux_recv(sock)
        number = resp.get("Number", -1)
        if number != 0:
            if number == 3:
                raise PhoneCamError(
                    "iPhoneのPhoneCamアプリに接続できません。アプリを起動して前面に表示してください"
                )
            raise PhoneCamError("USB接続に失敗しました（エラー番号 %s）" % number)
        return sock
    except PhoneCamError:
        sock.close()
        raise
    except (OSError, ConnectionError, plistlib.InvalidFileException, struct.error, ValueError):
        sock.close()
        raise PhoneCamError("USBでの通信中にエラーが発生しました。ケーブルを抜き差しして再試行してください")


def wifi_connect(ip, port=PHONE_PORT, timeout=3.0):
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError:
        raise PhoneCamError(
            "%s:%d に接続できません。IPアドレスと、iPhoneでアプリが起動していることを確認してください" % (ip, port)
        )
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "mode": "usb",
    "ip": "",
    "lens": "wide",
    "resolution": "1080p",
    "fps": 60,
    "bitrate": 20,
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
    "rec_bitrate": 50,
    "rec_audio": True,
    "rec_photos": True,
    # iPhoneのマイク
    "mic_enabled": False,
    "mic_device": "",
    "mic_volume": 100,
    "mic_mute": False,
    "mic_delay_ms": 0,
    # iPhoneのカメラを動かすタイミング
    "run_mode": "auto",
    "idle_stop_sec": 10,
    # 初回セットアップ (ウィザード) を終えたか
    "setup_done": False,
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
RUN_MODES = ("auto", "always", "manual")
IDLE_STOP_VALUES = (10, 30, 60)
# 整数の設定 (キー: (最小, 最大))
INT_RANGES = {
    "rec_bitrate": (10, 100),
    "mic_volume": (0, 200),
    "mic_delay_ms": (0, 500),
}
BOOL_KEYS = ("screen_black", "rec_audio", "rec_photos", "mic_enabled", "mic_mute", "setup_done")
AUDIO_AVAILABLE = sd is not None and np is not None


def mirror_key(settings):
    """現在のレンズに対応する左右反転の設定キー (インカメだけ別に記憶する)。"""
    return "mirror_front" if settings.get("lens") == "front" else "mirror"


def sanitize_settings(s):
    """読み込んだ設定の新規項目を検証し、不正値は既定値へ戻す (古い設定ファイルとの互換用)。"""
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
    for key, (lo, hi) in INT_RANGES.items():
        try:
            v = int(round(float(s.get(key))))
        except Exception:
            v = DEFAULT_SETTINGS[key]
        s[key] = min(hi, max(lo, v))
    if not isinstance(s.get("mic_device"), str):
        s["mic_device"] = DEFAULT_SETTINGS["mic_device"]
    # カメラの動かし方
    if s.get("run_mode") not in RUN_MODES:
        s["run_mode"] = DEFAULT_SETTINGS["run_mode"]
    try:
        idle = int(s.get("idle_stop_sec"))
    except Exception:
        idle = None
    s["idle_stop_sec"] = idle if idle in IDLE_STOP_VALUES else DEFAULT_SETTINGS["idle_stop_sec"]
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


def save_settings(settings, path=SETTINGS_PATH):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def build_control(settings, group):
    """設定から制御 JSON を作る。group='all' で全項目。"""
    c = {}
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
        c["bitrate"] = int(settings["bitrate"])
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
            "bitrate": int(settings["rec_bitrate"]),
            "includeAudio": bool(settings["rec_audio"]),
            "saveToPhotos": bool(settings["rec_photos"]),
        }
    if group in ("all", "mic"):
        # sounddevice が無いと再生できないので、iPhone側でも有効にしない
        c["mic"] = {"enabled": bool(settings["mic_enabled"]) and AUDIO_AVAILABLE}
    return c


# ---------------------------------------------------------------------------
# 出力サイズの固定 (向きが変わっても仮想カメラを作り直さない)
# ---------------------------------------------------------------------------

def canvas_size(settings):
    """仮想カメラの出力サイズ (幅, 高さ)。native のときは None (= フレームのサイズそのまま)。"""
    mode = settings.get("canvas", "follow")
    if mode == "native":
        return None
    long_side, short_side = RESOLUTIONS.get(settings.get("resolution"), (1920, 1080))
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
        arr = frame.reformat(width=sw, height=sh, format=fmt).to_ndarray()
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


# ---------------------------------------------------------------------------
# v1.3 パケット解析・遅延計測・温度履歴 (GUI/ネットワークに依存しない純粋なロジック)
# ---------------------------------------------------------------------------

def parse_pong(payload):
    """type 4 (JSON) → (PCが送った値, iPhoneの時刻[秒])。不正なら None。"""
    try:
        d = json.loads(payload.decode("utf-8"))
        sent = float(d["pong"])
        phone_t = float(d["t"])
    except Exception:
        return None
    if sent != sent or phone_t != phone_t:  # NaN
        return None
    return sent, phone_t


def parse_timing(payload):
    """type 5 (16 byte: float64 BE captureTime, float64 BE sendTime) → (capture, send)。不正なら None。"""
    if len(payload) < 16:
        return None
    capture, send = struct.unpack(">dd", payload[:16])
    if capture != capture or send != send:
        return None
    return capture, send


AUDIO_HEADER_SIZE = 13  # float64 + uint32 + uint8


def parse_audio(payload):
    """type 6 → (captureTime, sampleRate, samples)。samples は float32 の mono (-1〜1)。不正なら None。"""
    if np is None or len(payload) < AUDIO_HEADER_SIZE:
        return None
    capture, rate, channels = struct.unpack(">dIB", payload[:AUDIO_HEADER_SIZE])
    if rate < 8000 or rate > 192000:
        return None
    body = payload[AUDIO_HEADER_SIZE:]
    body = body[: len(body) // 2 * 2]
    if not body:
        return None
    pcm = np.frombuffer(body, dtype="<i2").astype(np.float32) * (1.0 / 32768.0)
    if channels > 1:
        n = len(pcm) // channels * channels
        pcm = pcm[:n].reshape(-1, channels).mean(axis=1)
    return capture, int(rate), pcm


class LatencyTracker:
    """ping/pong で iPhone と PC の時計のずれ(offset)を推定し、フレームごとの遅延を集計する。
    時刻はすべて秒。offset は「iPhone時刻 ≈ PC時刻(perf_counter) + offset」。"""

    def __init__(self, max_offsets=15, max_frames=60):
        self._lock = threading.Lock()
        self._max_offsets = max_offsets
        self._max_frames = max_frames
        self.reset()

    def reset(self):
        with getattr(self, "_lock", threading.Lock()):
            self._pongs = deque(maxlen=self._max_offsets)   # (rtt, offset)
            self._frames = deque(maxlen=self._max_frames)   # (iphone, comm|None, pc)

    def add_pong(self, sent_s, recv_s, phone_t):
        rtt = recv_s - sent_s
        if rtt < 0 or rtt > 10.0:
            return False
        offset = phone_t - (sent_s + rtt / 2.0)
        with self._lock:
            self._pongs.append((rtt, offset))
        return True

    def offset(self):
        """RTT が最小だったサンプルの offset (非対称な遅れの影響が最も小さいため)。無ければ None。"""
        with self._lock:
            return self._offset_locked()

    def _offset_locked(self):
        if not self._pongs:
            return None
        return min(self._pongs, key=lambda p: p[0])[1]

    def add_frame(self, capture, send, recv_pc, done_pc):
        """capture/send: iPhone時刻[秒]、recv_pc/done_pc: PCの perf_counter[秒]。"""
        iphone = max(0.0, (send - capture) * 1000.0)
        pc = max(0.0, (done_pc - recv_pc) * 1000.0)
        with self._lock:
            off = self._offset_locked()
            comm = None
            if off is not None:
                comm = max(0.0, ((recv_pc + off) - send) * 1000.0)  # PC時刻 + offset = iPhone時刻
            self._frames.append((iphone, comm, pc))

    def averages(self):
        """直近の平均 (ms)。無い項目は None。キー: iphone, comm, pc, total, rtt"""
        with self._lock:
            frames = list(self._frames)
            rtts = [p[0] for p in self._pongs]
        out = {"iphone": None, "comm": None, "pc": None, "total": None,
               "rtt": (sum(rtts) / len(rtts) * 1000.0) if rtts else None}
        if frames:
            out["iphone"] = sum(f[0] for f in frames) / len(frames)
            out["pc"] = sum(f[2] for f in frames) / len(frames)
            full = [f for f in frames if f[1] is not None]
            if full:
                out["comm"] = sum(f[1] for f in full) / len(full)
                out["total"] = sum(f[0] + f[1] + f[2] for f in full) / len(full)
        return out


THERMAL_LEVELS = ("nominal", "fair", "serious", "critical")
THERMAL_COLORS = {"nominal": "#1d1d1f", "fair": "#b7791f", "serious": "#d9730d", "critical": "#c0392b"}
HISTORY_SECONDS = 30 * 60


class ThermalLog:
    """温度と受信fpsを1秒ごとに記録 (直近30分)。「高い」以上だった時間も積算する。"""

    def __init__(self, seconds=HISTORY_SECONDS):
        self.samples = deque(maxlen=seconds)  # (レベル 0-3 または None, fps)
        self.hot_seconds = 0.0
        self._was_connected = False
        self._last_t = None

    def sample(self, now, connected, thermal, fps):
        if connected and not self._was_connected:
            self.hot_seconds = 0.0
        dt = 0.0 if self._last_t is None else min(2.0, max(0.0, now - self._last_t))
        self._last_t = now
        self._was_connected = connected
        level = THERMAL_LEVELS.index(thermal) if (connected and thermal in THERMAL_LEVELS) else None
        if level is not None and level >= 2:
            self.hot_seconds += dt
        self.samples.append((level, float(fps) if connected else 0.0))


def format_mmss(seconds):
    try:
        t = max(0, int(seconds))
    except Exception:
        return "-"
    if t >= 3600:
        return "%d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60)
    return "%02d:%02d" % (t // 60, t % 60)


def format_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "-"
    if n < 0:
        return "-"
    if n >= 1024 ** 3:
        return "%.2f GB" % (n / 1024 ** 3)
    return "%.1f MB" % (n / 1024 ** 2)


PRESSURE_LABELS = {"nominal": "正常", "fair": "やや高い", "serious": "高い", "critical": "危険", "shutdown": "停止"}
PRESSURE_FACTOR_LABELS = {
    "systemTemperature": "本体の温度",
    "peakPower": "電力の消費",
    "depthModuleTemperature": "深度センサーの温度",
    "cameraTemperature": "カメラの温度",
}


def format_pressure_factors(factors):
    if not isinstance(factors, (list, tuple)):
        return "-"
    names = [PRESSURE_FACTOR_LABELS[f] for f in factors if f in PRESSURE_FACTOR_LABELS]
    return "、".join(names) if names else "-"


# ---------------------------------------------------------------------------
# iPhoneのマイク再生
# ---------------------------------------------------------------------------

class AudioRing:
    """float32 モノラルのリングバッファ (呼び出し側がロックする)。"""

    def __init__(self, capacity):
        self.buf = np.zeros(capacity, dtype=np.float32)
        self.cap = capacity
        self.head = 0   # 次に読む位置
        self.count = 0

    def write(self, x):
        n = len(x)
        if n >= self.cap:
            x = x[-self.cap:]
            n = self.cap
            self.head = 0
            self.count = 0
        over = self.count + n - self.cap
        if over > 0:  # 溢れる分は古い方を捨てる
            self.head = (self.head + over) % self.cap
            self.count -= over
        tail = (self.head + self.count) % self.cap
        first = min(n, self.cap - tail)
        self.buf[tail:tail + first] = x[:first]
        if first < n:
            self.buf[:n - first] = x[first:]
        self.count += n

    def read(self, n):
        n = min(n, self.count)
        first = min(n, self.cap - self.head)
        out = np.empty(n, dtype=np.float32)
        out[:first] = self.buf[self.head:self.head + first]
        if first < n:
            out[first:] = self.buf[:n - first]
        self.head = (self.head + n) % self.cap
        self.count -= n
        return out

    def drop(self, n):
        n = min(n, self.count)
        self.head = (self.head + n) % self.cap
        self.count -= n
        return n

    def clear(self):
        self.head = 0
        self.count = 0


class LinearResampler:
    """線形補間のリサンプラ。パケットをまたいで位相を引き継ぐのでつなぎ目でプチ音が出にくい。"""

    def __init__(self, src_rate, dst_rate):
        self.step = float(src_rate) / float(dst_rate)
        self.same = int(src_rate) == int(dst_rate)
        self.carry = None
        self.phase = 0.0

    def process(self, x):
        if self.same or len(x) == 0:
            return x
        if self.carry is None:
            buf = x.astype(np.float32, copy=False)  # 最初のパケットは先頭サンプルが位置0
        else:
            buf = np.concatenate((np.array([self.carry], dtype=np.float32), x))
        last = len(buf) - 1
        if last < 1:  # 1サンプルだけ: 次のパケットと合わせて補間する
            self.carry = buf[-1]
            return np.zeros(0, dtype=np.float32)
        if self.phase > last:
            n = 0
        else:
            n = int((last - self.phase) / self.step) + 1
        pos = self.phase + np.arange(n, dtype=np.float64) * self.step
        idx = np.minimum(pos.astype(np.int64), last - 1)
        frac = (pos - idx).astype(np.float32)
        out = buf[idx] * (1.0 - frac) + buf[idx + 1] * frac
        self.phase = self.phase + n * self.step - last
        self.carry = buf[-1]
        return out.astype(np.float32, copy=False)


DEFAULT_DEVICE_LABEL = "システム既定"
SYSTEM_DEFAULT_KEY = "__default__"
CABLE_KEYWORD = "CABLE Input"
AUDIO_BASE_MS = 40.0
AUDIO_SLACK_MS = 60.0


def list_output_devices(sd_mod=None):
    """出力デバイス一覧。WASAPI があればそれだけ (名前の重複は除く)。"""
    sd_mod = sd_mod or sd
    if sd_mod is None:
        return []
    apis = sd_mod.query_hostapis()
    out = []
    for i, d in enumerate(sd_mod.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            api = apis[d["hostapi"]]["name"] if d.get("hostapi") is not None else ""
            out.append({"index": i, "name": d["name"], "api": api,
                        "rate": int(d.get("default_samplerate") or 48000),
                        "channels": int(d["max_output_channels"])})
    wasapi = [d for d in out if "WASAPI" in d["api"]]
    use = wasapi or out
    seen, result = set(), []
    for d in use:
        if d["name"] not in seen:
            seen.add(d["name"])
            result.append(d)
    return result


def choose_output_device(devices, saved):
    """保存された名前に合うデバイスを返す。None はシステム既定。
    saved が空 (未設定) なら 'CABLE Input' を含むものを優先、無ければシステム既定。"""
    if saved == SYSTEM_DEFAULT_KEY:
        return None
    if saved:
        for d in devices:
            if d["name"] == saved:
                return d
        return None  # 保存したデバイスが見つからない → 既定で鳴らす
    for d in devices:
        if CABLE_KEYWORD.lower() in d["name"].lower():
            return d
    return None


class AudioEngine:
    """type 6 の音声をリングバッファに溜め、sounddevice の出力コールバックで再生する。
    ネットワークスレッドからは feed() だけを呼ぶ (短いロックのみでブロックしない)。"""

    def __init__(self, get_params):
        """get_params() → (音量 0-200 [%], ミュート, 遅延 [ms])"""
        self._get_params = get_params
        self._lock = threading.Lock()
        self.ring = None
        self.rate = 0
        self.channels = 1
        self._stream = None
        self._resampler = None
        self._src_rate = 0
        self._primed = False
        self._last_delay = 0.0
        self._peak = 0.0
        self._stopping = False
        self.dead = False
        self.error = ""
        self.device_name = ""
        self.dropped_samples = 0
        self.underruns = 0

    # -- バッファ -------------------------------------------------------------
    def setup(self, rate):
        """出力サンプルレートを決めてバッファを用意する (ストリームは開かない)。"""
        with self._lock:
            self.rate = int(rate)
            self.ring = AudioRing(int(rate * 2))
            self._primed = False
            self._resampler = None
            self._src_rate = 0

    def flush(self):
        with self._lock:
            if self.ring is not None:
                self.ring.clear()
            self._primed = False
            self._resampler = None
            self._src_rate = 0

    def _target(self, delay_ms):
        return int(self.rate * (AUDIO_BASE_MS + delay_ms) / 1000.0)

    def feed(self, payload):
        """ネットワークスレッドから呼ぶ。解析 → リサンプル → 追加。"""
        if self.ring is None:
            return
        parsed = parse_audio(payload)
        if parsed is None:
            return
        _capture, src_rate, pcm = parsed
        peak = float(np.abs(pcm).max()) if len(pcm) else 0.0
        if peak > self._peak:
            self._peak = peak
        if self._resampler is None or self._src_rate != src_rate:
            self._resampler = LinearResampler(src_rate, self.rate)
            self._src_rate = src_rate
        x = self._resampler.process(pcm)
        self.push(x)

    def push(self, x):
        delay = float(self._get_params()[2])
        target = self._target(delay)
        with self._lock:
            ring = self.ring
            if ring is None:
                return
            ring.write(x)
            if ring.count > target + int(self.rate * AUDIO_SLACK_MS / 1000.0):
                self.dropped_samples += ring.drop(ring.count - target)

    def pull(self, frames):
        """出力コールバック用: frames 個の mono float32 を返す。足りなければ無音。"""
        vol, mute, delay = self._get_params()
        target = self._target(float(delay))
        with self._lock:
            ring = self.ring
            if ring is None:
                return np.zeros(frames, dtype=np.float32)
            if self._primed and delay > self._last_delay + 10:
                self._primed = False  # 遅延を増やしたので溜め直す
            self._last_delay = float(delay)
            if not self._primed:
                if ring.count >= target:
                    self._primed = True
                else:
                    return np.zeros(frames, dtype=np.float32)
            if ring.count < frames:
                got = ring.read(frames)
                self._primed = False
                self.underruns += 1
                out = np.zeros(frames, dtype=np.float32)
                out[:len(got)] = got
            else:
                out = ring.read(frames)
        gain = 0.0 if mute else float(vol) / 100.0
        out *= gain
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def level_db(self):
        """前回読んでからのピーク (dBFS)。無音は -100。"""
        p, self._peak = self._peak, 0.0
        if p <= 0.0:
            return -100.0
        import math
        return max(-100.0, 20.0 * math.log10(p))

    def buffered_ms(self):
        with self._lock:
            if self.ring is None or not self.rate:
                return 0.0
            return self.ring.count * 1000.0 / self.rate

    # -- デバイス ---------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status):
        try:
            mono = self.pull(frames)
            outdata[:] = mono[:, None]
        except Exception:
            outdata.fill(0)

    def _finished(self):
        if not self._stopping:
            self.dead = True

    def start(self, saved_device):
        """ストリームを開く。成功なら True。失敗時は self.error に日本語の理由を入れる。"""
        self.stop()
        if sd is None or np is None:
            self.error = "sounddevice がインストールされていません"
            return False
        try:
            devices = list_output_devices()
            dev = choose_output_device(devices, saved_device)
            if dev is None:
                idx = sd.default.device[1]
                if idx is None or idx < 0:
                    raise RuntimeError("出力デバイスが見つかりません")
                info = sd.query_devices(idx)
                dev = {"index": idx, "name": info["name"], "rate": int(info["default_samplerate"]),
                       "channels": int(info["max_output_channels"])}
            self.setup(dev["rate"])
            last = None
            stream = None
            tried = []
            for ch in (min(2, dev["channels"]), dev["channels"], 1):
                if ch < 1 or ch in tried:
                    continue
                tried.append(ch)
                try:
                    stream = sd.OutputStream(
                        device=dev["index"], samplerate=dev["rate"], channels=ch, dtype="float32",
                        latency="low", callback=self._callback, finished_callback=self._finished)
                    stream.start()
                    break
                except Exception as e:  # 次のチャンネル数で再挑戦
                    last = e
                    stream = None
            if stream is None:
                raise last or RuntimeError("開けませんでした")
        except Exception as e:
            self.error = "音声の出力先を開けませんでした。別のデバイスを選ぶか、他のアプリが占有していないか確認してください（%s）" % e
            with self._lock:
                self.ring = None
            return False
        self._stream = stream
        self.channels = ch
        self.device_name = dev["name"]
        self.dead = False
        self.error = ""
        return True

    def stop(self):
        self._stopping = True
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._stopping = False
        self.dead = False
        with self._lock:
            self.ring = None

    @property
    def running(self):
        return self._stream is not None and not self.dead


# ---------------------------------------------------------------------------
# 共有状態 (スレッド間)
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self.status_text = "停止中"
        self.error_text = ""
        self.recv_fps = 0.0
        self.decode_ms = 0.0
        self.resolution = "-"
        self.phone_status = {}
        self.connected = False

    def update(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {
                "status_text": self.status_text,
                "error_text": self.error_text,
                "recv_fps": self.recv_fps,
                "decode_ms": self.decode_ms,
                "resolution": self.resolution,
                "phone_status": dict(self.phone_status),
                "connected": self.connected,
            }


# ---------------------------------------------------------------------------
# カメラを動かすタイミングの判定 (使っているアプリがあるときだけ iPhone のカメラを動かす)
# ---------------------------------------------------------------------------

# softcam の共有メモリ (windows.yml で "DirectShow Softcam/SharedMemory" から改名したもの)
SOFTCAM_SHMEM_NAME = "PhoneCam/SharedMemory"
SHMEM_HEADER_BYTES = 16        # ヘッダ先頭 16 byte だけ読む (receiver_heartbeat は 15 byte 目)
RECEIVER_HEARTBEAT_OFFSET = 15
CONSUMER_WINDOW_SEC = 3.0      # この時間以内にハートビートが変化していれば「使われている」
STANDBY_INTERVAL_SEC = 1.0     # 映像が来ないとき、待機用の黒フレームを送る間隔
WATCH_INTERVAL_MS = 500

RUN_MODE_LABELS = [("auto", "使われているときだけ（自動）"), ("always", "接続中ずっと"), ("manual", "手動")]
IDLE_STOP_LABELS = [(10, "10秒"), (30, "30秒"), (60, "60秒")]
AUTO_UNSUPPORTED_NOTE = "自動判定は出力先が「PhoneCam（専用カメラ）」のときだけ使えます"


def parse_receiver_heartbeat(raw):
    """softcam 共有メモリの先頭バイト列から receiver_heartbeat (uint8) を取り出す。不正なら None。
    ヘッダ: u32 image_offset, u16 width, u16 height, f32 framerate, u8 is_active,
            u8 connected_min_version, u8 sender_heartbeat, u8 receiver_heartbeat, u64 frame_counter"""
    if raw is None or len(raw) < SHMEM_HEADER_BYTES:
        return None
    return raw[RECEIVER_HEARTBEAT_OFFSET]


class Win32Mapping:
    """名前付き共有メモリを読み取り専用で開く (作成はしない)。"""
    FILE_MAP_READ = 0x0004

    def __init__(self, name):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        k.UnmapViewOfFile.restype = ctypes.c_int
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        self._k = k
        self._handle = k.OpenFileMappingW(self.FILE_MAP_READ, 0, name)
        if not self._handle:
            raise OSError("共有メモリがありません")
        self._view = k.MapViewOfFile(self._handle, self.FILE_MAP_READ, 0, 0, SHMEM_HEADER_BYTES)
        if not self._view:
            k.CloseHandle(self._handle)
            self._handle = None
            raise OSError("共有メモリを読み取れません")

    def read(self):
        return ctypes.string_at(self._view, SHMEM_HEADER_BYTES)

    def close(self):
        view, self._view = self._view, None
        handle, self._handle = self._handle, None
        if view:
            self._k.UnmapViewOfFile(view)
        if handle:
            self._k.CloseHandle(handle)


class SoftcamMemoryReader:
    """softcam の共有メモリから receiver_heartbeat を読む。カメラが無いときは None。"""

    def __init__(self, opener=Win32Mapping, name=SOFTCAM_SHMEM_NAME):
        self._opener = opener
        self._name = name
        self._m = None

    def read(self):
        if self._m is None:
            try:
                self._m = self._opener(self._name)
            except Exception:
                self._m = None
                return None
        try:
            return parse_receiver_heartbeat(self._m.read())
        except Exception:
            self.close()
            return None

    def close(self):
        m, self._m = self._m, None
        if m is not None:
            try:
                m.close()
            except Exception:
                pass


class ConsumerWatcher:
    """receiver_heartbeat が最近変化したか = 仮想カメラを開いているアプリがあるか。
    ハートビートは 8bit で一周するので、前回値との不一致だけを見る。"""

    def __init__(self, reader, window=CONSUMER_WINDOW_SEC):
        self.reader = reader
        self.window = window
        self._last = None
        self._changed_t = None

    def reset(self):
        """カメラを作り直したときに呼ぶ (古い共有メモリを掴み続けないため)。"""
        self.reader.close()
        self._last = None
        self._changed_t = None

    def poll(self, now):
        hb = self.reader.read()
        if hb is None:
            self._last = None
        else:
            if self._last is not None and hb != self._last:
                self._changed_t = now
            self._last = hb

    def in_use(self, now):
        return self._changed_t is not None and (now - self._changed_t) <= self.window


class StreamController:
    """iPhone のカメラを動かすか (stream.active) を決める。判定は desired() ひとつに集約する。"""

    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self.manual_started = False   # 手動モード: 開始を押した
        self.user_paused = False      # 自動/常時: 一時停止を押した (開始を押すまで保持)
        self.in_use = False
        self.last_in_use = None
        self.sent = None              # 最後にiPhoneへ送った値 (未送信は None)

    # -- 設定 ------------------------------------------------------------
    def mode(self):
        """実際に使うモード。PhoneCam 以外の出力先では自動判定できないので「常時」と同じ扱い。"""
        m = self.settings.get("run_mode", "auto")
        if m not in RUN_MODES:
            m = "auto"
        if m == "auto" and self.settings.get("backend") != "phonecam":
            return "always"
        return m

    def auto_unsupported(self):
        return self.settings.get("run_mode", "auto") == "auto" and self.settings.get("backend") != "phonecam"

    def idle_sec(self):
        v = self.settings.get("idle_stop_sec", 10)
        return v if v in IDLE_STOP_VALUES else 10

    # -- 入力 ------------------------------------------------------------
    def note_in_use(self, in_use, now):
        with self._lock:
            self.in_use = bool(in_use)
            if in_use:
                self.last_in_use = now

    def mode_changed(self):
        with self._lock:
            self.user_paused = False
            self.manual_started = False

    def reset_user(self):
        """切断したとき: 押したボタンの状態を忘れる。"""
        with self._lock:
            self.user_paused = False
            self.manual_started = False
            self.last_in_use = None
            self.sent = None

    # -- 判定 ------------------------------------------------------------
    def _desired_locked(self, connected, now):
        if not connected:
            return False
        mode = self.mode()
        if mode == "manual":
            return self.manual_started
        if self.user_paused:
            return False
        if mode == "always":
            return True
        if self.in_use:
            return True
        li = self.last_in_use
        return li is not None and (now - li) < self.idle_sec()

    def desired(self, connected, now):
        with self._lock:
            return self._desired_locked(connected, now)

    def press_button(self, connected, now):
        """開始/一時停止ボタン。"""
        with self._lock:
            active = self._desired_locked(connected, now)
            if self.mode() == "manual":
                self.manual_started = not active
            elif active:
                self.user_paused = True
            else:
                self.user_paused = False
                if self.mode() == "auto":
                    self.last_in_use = now  # 押した直後は設定時間だけ動かして映像を確認できる

    def button_label(self, connected, now):
        return "一時停止" if self.desired(connected, now) else "開始"

    # -- 送信管理 ----------------------------------------------------------
    def on_connect(self, now):
        """接続直後の "all" 制御に載せる値。送信済みとして記録する。"""
        with self._lock:
            v = self._desired_locked(True, now)
            self.sent = v
            return v

    def pending(self, connected, now):
        """変化があれば送るべき値、なければ None。切断中は送信済みの記録を消す。"""
        with self._lock:
            if not connected:
                self.sent = None
                return None
            v = self._desired_locked(True, now)
            return v if v != self.sent else None

    def mark_sent(self, value):
        with self._lock:
            self.sent = value

    # -- 表示 ------------------------------------------------------------
    def state_text(self, connected, st, now):
        if not connected:
            return "-"
        with self._lock:
            want = self._desired_locked(True, now)
            manual = self.mode() == "manual"
            paused = self.user_paused
        if want:
            if st.get("cameraRunning") is False or st.get("streamActive") is False:
                return "起動中…"
            return "配信中"
        if manual:
            return "待機中（開始を押すと動きます）"
        if paused:
            return "一時停止中"
        return "待機中（使っているアプリなし）"


def camera_state_text(connected, st):
    """iPhone の状態から「カメラ: 動作中/停止」。"""
    if not connected or "cameraRunning" not in st:
        return "-"
    return "動作中" if st.get("cameraRunning") else "停止"


# ---------------------------------------------------------------------------
# 仮想カメラ出力
# ---------------------------------------------------------------------------

class SoftcamSender:
    """softcam.dll (専用仮想カメラ) への ctypes ラッパー。BGR24 のフレームを送る。"""

    def __init__(self, dll_path):
        lib = ctypes.CDLL(dll_path)  # softcam は __cdecl
        lib.scCreateCamera.restype = ctypes.c_void_p
        lib.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float]
        lib.scDeleteCamera.restype = None
        lib.scDeleteCamera.argtypes = [ctypes.c_void_p]
        lib.scSendFrame.restype = None
        lib.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.scIsConnected.restype = ctypes.c_bool
        lib.scIsConnected.argtypes = [ctypes.c_void_p]
        self._lib = lib
        self._handle = None

    def create(self, width, height):
        # framerate=0: 受け取ったフレームを待たずに即送る (低遅延)
        h = self._lib.scCreateCamera(int(width), int(height), 0.0)
        if not h:
            raise RuntimeError("scCreateCamera が失敗しました（別のアプリがPhoneCamカメラを使用中の可能性があります）")
        self._handle = h

    def send(self, img):
        """img: C 連続な uint8 (H, W, 3) BGR 配列"""
        self._lib.scSendFrame(self._handle, img.ctypes.data_as(ctypes.c_void_p))

    def close(self):
        h, self._handle = self._handle, None
        if h:
            self._lib.scDeleteCamera(h)  # 同一プロセスでは1台のみ。作り直す前に必ず削除


SIZE_CHANGED_NOTICE = "出力サイズが変わりました。OBSでソースを一度非表示→表示にすると映像が戻ります"


class VirtualCamOutput:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self.cam = None
        self.size = None
        self.fail_until = 0.0
        self.reset_requested = False
        self._softcam = None
        self._softcam_path = None
        self._cam_kind = None  # 現在開いているバックエンド
        self._notice_until = 0.0  # 出力サイズ変更の案内を消す時刻
        self._lock = threading.RLock()  # 受信スレッドと GUI スレッド(待機フレーム)で同じカメラを触るため
        self.generation = 0       # カメラを作る/閉じるたびに増える (共有メモリの掴み直し用)
        self.last_frame_t = 0.0   # 最後に映像を渡した時刻 (monotonic)
        self._last_standby_t = 0.0
        self._black = None

    # -- 補助 -----------------------------------------------------------
    def backend(self):
        b = self.settings.get("backend")
        return b if b in BACKENDS else "obs"

    def frame_format(self):
        """デコード結果をどの画素形式で渡すか。"""
        return "bgr24" if self.backend() == "phonecam" else "rgb24"

    def apply_pending_reset(self):
        if self.reset_requested:
            self.reset_requested = False
            self.close()
            self.fail_until = 0.0

    def close(self):
        with self._lock:
            if self.cam is not None:
                try:
                    self.cam.close()
                except Exception:
                    pass
                self.generation += 1
            self.cam = None
            self.size = None
            self._cam_kind = None

    def park(self):
        """切断したとき。PhoneCam専用カメラは、使うアプリが繋がったままにできるよう残して待機フレームを送る。
        それ以外の出力先は閉じる。"""
        with self._lock:
            if self.cam is not None and self._cam_kind == "phonecam" and self.backend() == "phonecam":
                return
            self.close()

    def standby(self, canvas, now):
        """PhoneCam専用カメラを作った状態に保ち、映像が来ないときは約1秒ごとに黒フレームを送る。
        canvas: canvas_size() の結果 (None なら現在のサイズ。無ければ横長の既定)。"""
        with self._lock:
            self.apply_pending_reset()
            if self.backend() != "phonecam":
                return
            if now - self.last_frame_t < STANDBY_INTERVAL_SEC or now - self._last_standby_t < STANDBY_INTERVAL_SEC:
                return
            self._last_standby_t = now
            w, h = canvas or self.size or RESOLUTIONS.get(self.settings.get("resolution"), (1920, 1080))
            w, h = w // 4 * 4, h // 4 * 4
            if self._black is None or self._black.shape[:2] != (h, w):
                self._black = np.zeros((h, w, 3), dtype=np.uint8)
            self._send_locked(self._black, 30)

    def request_reset(self):
        self.reset_requested = True

    # -- 出力 -----------------------------------------------------------
    def _open_softcam(self, w, h):
        path = softcam_load_path()
        if not os.path.isfile(path):
            if _is_frozen():
                raise PhoneCamError(
                    "exe に内蔵された softcam.dll が見つかりません。PhoneCamReceiver.exe をダウンロードし直してください。"
                    "OBS Virtual Camera など他の出力先も選べます")
            raise PhoneCamError(
                "softcam.dll が見つかりません。ソース実行では windows フォルダに softcam.dll を置き、"
                "register.bat を実行してください。OBS Virtual Camera など他の出力先も選べます")
        if self._softcam is not None and self._softcam_path != path:
            # 登録(更新)で読み込み元が変わった: 古い方のカメラを閉じて新しい DLL を使う
            self._softcam.close()
            self._softcam = None
        if self._softcam is None:
            try:
                self._softcam = SoftcamSender(path)
                self._softcam_path = path
            except OSError as e:
                raise PhoneCamError("softcam.dll を読み込めません: %s" % e)
        sender = self._softcam
        sender.close()
        sender.create(w, h)
        if _is_frozen():
            st = registration_status()
            if st == "missing":
                self.state.update(error_text="PhoneCamカメラが未登録です。%s" % register_hint())
            elif st == "outdated":
                self.state.update(error_text="PhoneCamカメラの更新が必要です。%s" % register_hint())
            else:
                self.state.update(error_text="")
            return sender
        reg = softcam_registered_path()
        if reg is None:
            self.state.update(error_text="PhoneCamカメラが未登録です。%s" % register_hint())
        elif not _same_path(reg, path):
            self.state.update(error_text="PhoneCamカメラの登録先が別のフォルダです。フォルダを移動した場合は register.bat をもう一度実行してください")
        else:
            self.state.update(error_text="")
        return sender

    def send(self, img, fps):
        with self._lock:
            self.last_frame_t = time.monotonic()
            self._send_locked(img, fps)

    def _send_locked(self, img, fps):
        self.apply_pending_reset()
        backend = self.backend()
        if backend == "phonecam":
            # softcam は幅・高さが4の倍数である必要がある
            h4, w4 = img.shape[0] // 4 * 4, img.shape[1] // 4 * 4
            if (h4, w4) != (img.shape[0], img.shape[1]):
                img = np.ascontiguousarray(img[:h4, :w4])
            if not SOFTCAM_TOP_DOWN:
                img = np.ascontiguousarray(img[::-1])
        h, w = img.shape[0], img.shape[1]
        size_changed = False
        if self.cam is not None and (self.size != (w, h) or self._cam_kind != backend):
            size_changed = self._cam_kind == backend and self.size != (w, h)
            self.close()
        if self.cam is None:
            now = time.time()
            if now < self.fail_until:
                return
            if backend == "phonecam":
                try:
                    self.cam = self._open_softcam(w, h)
                    self.size = (w, h)
                    self._cam_kind = backend
                    self.generation += 1
                except PhoneCamError as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    self.state.update(error_text=str(e))
                    return
                except Exception as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    self.state.update(error_text="PhoneCamカメラを開始できません。登録済みか確認してください（%s）。詳細: %s" % (register_hint(), e))
                    return
            else:
                if pyvirtualcam is None:
                    self.state.update(error_text="pyvirtualcam がインストールされていません。install.bat を実行してください")
                    return
                pv_backend = "unitycapture" if backend == "unitycapture" else "obs"
                try:
                    self.cam = pyvirtualcam.Camera(
                        w, h, max(1, int(fps)), fmt=pyvirtualcam.PixelFormat.RGB, backend=pv_backend
                    )
                    self.size = (w, h)
                    self._cam_kind = backend
                    self.generation += 1
                    self.state.update(error_text="")
                except Exception as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    if pv_backend == "obs":
                        msg = ("仮想カメラ(OBS Virtual Camera)を開始できません。OBS Studio 30以上をインストールし、"
                               "OBSの「仮想カメラ開始」を使用中の場合は停止してください。詳細: %s" % e)
                    else:
                        msg = ("仮想カメラ(Unity Capture)を開始できません。Unity Captureドライバーを"
                               "インストールしてください。詳細: %s" % e)
                    self.state.update(error_text=msg)
                    return
            if size_changed:
                self._notice_until = time.time() + 10.0
                self.state.update(error_text=SIZE_CHANGED_NOTICE)
        if self._notice_until and time.time() >= self._notice_until:
            self._notice_until = 0.0
            if self.state.snapshot()["error_text"] == SIZE_CHANGED_NOTICE:
                self.state.update(error_text="")
        try:
            self.cam.send(img)
        except Exception as e:
            self.state.update(error_text="仮想カメラへの出力に失敗しました: %s" % e)
            self.close()


# ---------------------------------------------------------------------------
# 接続 + デコード スレッド
# ---------------------------------------------------------------------------

def codec_for_msg(msg_type):
    """映像パケット種別 → コーデック名。映像以外は None。"""
    if msg_type == MSG_VIDEO:
        return "h264"
    if msg_type == MSG_VIDEO_HEVC:
        return "hevc"
    return None


class DecoderManager:
    """デコーダー (CodecContext) を1つだけ保持し、パケットのコーデックが変わったら作り直す。"""

    def __init__(self, factory):
        self._factory = factory
        self.codec = None
        self.decoder = None

    def get(self, codec):
        """(decoder, switched)。switched は既存デコーダーから別コーデックへ切り替えたとき True。"""
        switched = False
        if self.decoder is None or codec != self.codec:
            switched = self.decoder is not None
            self.decoder = self._factory(codec)
            self.codec = codec
        return self.decoder, switched


def make_decoder(codec="h264"):
    ctx = av.CodecContext.create(codec, "r")
    try:
        ctx.thread_type = "SLICE"
    except Exception:
        pass
    # 低遅延フラグ (PyAV のバージョン差を吸収するため何重にも試す)
    applied = False
    try:
        flags_cls = getattr(av.codec.context, "Flags", None)
        if flags_cls is not None:
            for name in ("low_delay", "LOW_DELAY"):
                flag = getattr(flags_cls, name, None)
                if flag is not None:
                    try:
                        ctx.flags = ctx.flags | flag
                    except Exception:
                        ctx.flags |= flag
                    applied = True
                    break
    except Exception:
        pass
    if not applied:
        try:
            ctx.options = {"flags": "low_delay"}
        except Exception:
            pass
    return ctx


class Link:
    def __init__(self, settings, state, get_mode_ip, audio=None, stream=None):
        self.stream = stream
        self.settings = settings
        self.state = state
        self.get_mode_ip = get_mode_ip
        self.audio = audio
        self.latency = LatencyTracker()
        self.vcam = VirtualCamOutput(settings, state)
        self._thread = None
        self._stop = threading.Event()
        self._sock = None
        self._send_lock = threading.Lock()

    # -- 公開 API --------------------------------------------------------
    def start(self):
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="phonecam-link", daemon=True)
        self._thread.start()

    def stop(self):
        t = self._thread
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        self._thread = None
        self.vcam.park()
        self.state.update(status_text="停止中", connected=False, recv_fps=0.0, decode_ms=0.0, resolution="-")
        self.latency.reset()

    def send_control(self, control):
        sock = self._sock
        if sock is None:
            return False
        data = encode_msg(MSG_CONTROL, json.dumps(control).encode("utf-8"))
        try:
            with self._send_lock:
                sock.sendall(data)
            return True
        except Exception:
            return False

    # -- 内部 -------------------------------------------------------------
    def _connect(self):
        mode, ip = self.get_mode_ip()
        if mode == "usb":
            return usbmux_connect()
        return wifi_connect(ip)

    def _run(self):
        stop = self._stop
        while not stop.is_set():
            self.state.update(status_text="接続中…", connected=False)
            try:
                sock = self._connect()
            except PhoneCamError as e:
                self.state.update(status_text="待機中（再接続を試行します）", error_text=str(e), connected=False)
                stop.wait(RECONNECT_SEC)
                continue
            except Exception as e:
                self.state.update(status_text="待機中（再接続を試行します）", error_text="接続エラー: %s" % e, connected=False)
                stop.wait(RECONNECT_SEC)
                continue

            sock.settimeout(1.0)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            self._sock = sock
            try:
                self._session(sock, stop)
            except Exception as e:
                if not stop.is_set():
                    self.state.update(error_text="切断: %s" % e)
            finally:
                self._sock = None
                try:
                    sock.close()
                except Exception:
                    pass
            self.state.update(connected=False, recv_fps=0.0)
            if stop.is_set():
                break
            self.state.update(status_text="切断されました。再接続します…")
            stop.wait(RECONNECT_SEC)
        self.vcam.park()

    def _session(self, sock, stop):
        if av is None or np is None:
            raise PhoneCamError("av / numpy がインストールされていません。install.bat を実行してください")
        decoders = DecoderManager(make_decoder)
        self.state.update(status_text="接続済み", connected=True, error_text="")

        # 保存済み設定を全部送る + キーフレーム要求
        full = build_control(self.settings, "all")
        full["keyframe"] = True
        if self.stream is not None:
            # 新しい接続では iPhone は既定で動作中として扱うので、PC側の希望をすぐ伝える
            full["stream"] = {"active": self.stream.on_connect(time.monotonic())}
        with self._send_lock:
            sock.sendall(encode_msg(MSG_CONTROL, json.dumps(full).encode("utf-8")))

        last_kf = 0.0
        frame_count = 0
        fps_t0 = time.time()
        decode_avg = 0.0
        fitter = CanvasFitter()
        self.latency.reset()
        if self.audio is not None:
            self.audio.flush()
        pending_timing = None  # 次の映像パケットに対応する (captureTime, sendTime)
        ping_stop = threading.Event()
        threading.Thread(target=self._ping_loop, args=(ping_stop,), name="phonecam-ping", daemon=True).start()
        try:
            self._receive_loop(sock, stop, decoders, fitter, last_kf, frame_count, fps_t0, decode_avg, pending_timing)
        finally:
            ping_stop.set()

    def _ping_loop(self, ping_stop):
        """1秒ごとに ping を送る (遅延計測用)。"""
        while not ping_stop.is_set():
            self.send_control({"ping": round(time.perf_counter() * 1000.0, 3)})
            if ping_stop.wait(1.0):
                break

    def _receive_loop(self, sock, stop, decoders, fitter, last_kf, frame_count, fps_t0, decode_avg, pending_timing):
        while not stop.is_set():
            msg_type, payload = read_msg(sock, stop)
            t_recv = time.perf_counter()
            codec_name = codec_for_msg(msg_type)
            if codec_name is not None:
                timing, pending_timing = pending_timing, None
                t0 = t_recv
                try:
                    decoder, switched = decoders.get(codec_name)
                    if switched:
                        last_kf = time.time()
                        self.send_control({"keyframe": True})
                    frames = decoder.decode(av.Packet(payload))
                except Exception:
                    now = time.time()
                    if now - last_kf >= 1.0:
                        last_kf = now
                        self.send_control({"keyframe": True})
                    continue
                dt = (time.perf_counter() - t0) * 1000.0
                decode_avg = dt if decode_avg == 0.0 else decode_avg * 0.9 + dt * 0.1
                if not frames:
                    continue
                frame = frames[-1]
                self.vcam.apply_pending_reset()
                fmt = self.vcam.frame_format()
                canvas = canvas_size(self.settings)
                if canvas is None or canvas == (frame.width, frame.height):
                    img = frame.to_ndarray(format=fmt)
                else:
                    img = fitter.process(frame, fmt, canvas, self.settings.get("fit", "pad"),
                                         pad_color_for(self.settings))
                if self.settings.get(mirror_key(self.settings)):
                    img = img[:, ::-1]
                if self.settings.get("rotate180"):
                    img = img[::-1, ::-1]
                img = np.ascontiguousarray(img)
                fps_val = self.state.phone_status.get("fps", 60) or 60
                self.vcam.send(img, fps_val)
                if timing is not None:
                    self.latency.add_frame(timing[0], timing[1], t_recv, time.perf_counter())
                frame_count += 1
                now = time.time()
                if now - fps_t0 >= 1.0:
                    self.state.update(
                        recv_fps=frame_count / (now - fps_t0),
                        decode_ms=decode_avg,
                        resolution="%dx%d" % (img.shape[1], img.shape[0]),
                    )
                    frame_count = 0
                    fps_t0 = now
            elif msg_type == MSG_STATUS:
                try:
                    st = json.loads(payload.decode("utf-8"))
                    if isinstance(st, dict):
                        self.state.update(phone_status=st)
                except Exception:
                    pass
                # カメラ停止中は映像が来ない (タイムアウトにはしない)。表示だけ 0 に戻す
                now = time.time()
                if now - fps_t0 >= 2.0:
                    self.state.update(recv_fps=0.0)
                    frame_count = 0
                    fps_t0 = now
            elif msg_type == MSG_TIMING:
                pending_timing = parse_timing(payload)
            elif msg_type == MSG_PONG:
                p = parse_pong(payload)
                if p is not None:
                    self.latency.add_pong(p[0] / 1000.0, t_recv, p[1])
            elif msg_type == MSG_AUDIO:
                if self.audio is not None:
                    try:
                        self.audio.feed(payload)
                    except Exception:
                        pass
            # それ以外の種別は無視する (将来の拡張用)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

LENS_LABELS = [("wide", "広角"), ("ultrawide", "超広角"), ("front", "インカメ")]
RES_LABELS = ["720p", "1080p", "4K"]
FPS_LABELS = [30, 60]
MODE3_LABELS = [("auto", "自動"), ("locked", "固定"), ("manual", "マニュアル")]
SHUTTER_VALUES = [30, 50, 60, 100, 120, 250, 500, 1000]
ORIENT_LABELS = [("auto", "自動"), ("landscape", "横"), ("portrait", "縦")]
BACKEND_LABELS = [("phonecam", "PhoneCam（専用カメラ）"), ("obs", "OBS Virtual Camera"), ("unitycapture", "Unity Capture")]
CODEC_LABELS = [("h264", "H.264"), ("hevc", "HEVC(H.265)")]
CANVAS_LABELS = [("follow", "向きの設定に合わせる"), ("landscape", "横長で固定"), ("portrait", "縦長で固定"),
                 ("native", "映像のサイズそのまま（向きが変わるとOBSで再読み込みが必要）")]
FIT_LABELS = [("pad", "余白を付けて全体を表示"), ("crop", "はみ出す部分を切り取って埋める")]
BG_MODE_LABELS = [("off", "オフ"), ("blur", "ぼかし"), ("green", "切り抜き(緑)")]
BG_QUALITY_LABELS = [("fast", "速度優先"), ("balanced", "バランス")]
THERMAL_LABELS = {"nominal": "正常", "fair": "やや高い", "serious": "高い", "critical": "危険"}
BATTERY_STATE_LABELS = {"charging": "充電中", "full": "満充電", "unplugged": "放電中", "unknown": "不明"}
SCREEN_AUTO_LABELS = [(0, "しない"), (10, "10秒"), (30, "30秒"), (60, "60秒")]
REC_RES_LABELS = [("same", "配信と同じ"), ("4k", "4K")]
STATUS_TAB_INDEX = 5
METER_MIN_DB = -60.0
THERMAL_WARNING = "iPhoneが熱くなっています。fpsを30に下げるか、エフェクトをオフにしてください"
HINT_FG = "#666666"
THROTTLE_MS = 50  # 1コントロールあたり最大 20 msg/s


def format_battery(level, state):
    """バッテリー表示文字列。level は 0-1 または -1 (不明)。"""
    st = BATTERY_STATE_LABELS.get(state, "不明")
    try:
        lv = float(level)
    except Exception:
        lv = -1.0
    if lv < 0:
        return "不明" if st == "不明" else st
    return "%d%%（%s）" % (round(lv * 100), st)


def is_thermal_warning(thermal):
    return thermal in ("serious", "critical")


class App:
    def __init__(self, root):
        self.root = root
        migrate_legacy_settings()
        self._setup_key_present = settings_has_key("setup_done")  # None=設定ファイルなし(新規) / False=旧バージョンの設定
        self.settings = load_settings()
        self.state = SharedState()
        self.audio = AudioEngine(lambda: (self.settings["mic_volume"], self.settings["mic_mute"],
                                          self.settings["mic_delay_ms"]))
        self.stream = StreamController(self.settings)
        self.link = Link(self.settings, self.state, self._get_mode_ip, audio=self.audio, stream=self.stream)
        self.watcher = ConsumerWatcher(SoftcamMemoryReader())
        self._vcam_gen = -1
        self._closing = False
        self.wanted = False
        self.thermal_log = ThermalLog()
        self._last_sample = 0.0
        self._graph_dirty = True
        self._was_connected = False
        self._screen_user_t = 0.0
        self._rec_lock_until = 0.0
        self._audio_retry_t = 0.0
        self._meter_db = METER_MIN_DB
        self._timer_win = None
        self._pending = {}
        self._save_job = None
        self.focus_widgets = []
        self.manual_widgets = {}
        self._range_cache = {}
        self._elev_busy = False
        self._elev_result = None
        self.setup = None
        self._wizard = None
        self._setup_last_check = 0.0
        if phonecam_setup is not None:
            try:
                self.setup = phonecam_setup.SetupController(
                    root, can_start=lambda: not self._elev_busy, on_busy=self._setup_busy,
                    on_changed=self._setup_changed)
            except Exception:
                self.setup = None

        root.title("PhoneCam 受信 v%s" % APP_VERSION)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.minsize(560, 560)
        root.geometry("640x720")
        self._build_ui()
        self._update_enabled()
        self._apply_audio()
        self._poll()
        self._meter_tick()
        self._stream_tick()
        root.after(600, self._first_run_check)

    # -- 補助 -------------------------------------------------------------
    def _get_mode_ip(self):
        return self.settings["mode"], self.settings["ip"].strip()

    def _label_to_value(self, pairs, label):
        for v, l in pairs:
            if l == label:
                return v
        return pairs[0][0]

    def _value_to_label(self, pairs, value):
        for v, l in pairs:
            if v == value:
                return l
        return pairs[0][1]

    def _schedule_save(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._do_save)

    def _do_save(self):
        self._save_job = None
        save_settings(self.settings)

    def _schedule_send(self, group):
        """同じグループの送信は THROTTLE_MS ごとに最新値で1回だけ送る。"""
        if group in self._pending:
            return

        def fire():
            self._pending.pop(group, None)
            self.link.send_control(build_control(self.settings, group))

        self._pending[group] = self.root.after(THROTTLE_MS, fire)

    def _lens_changed(self):
        """レンズ変更。左右反転チェックはレンズごとに記憶した値へ切り替える。"""
        self._changed("lens", self._label_to_value(LENS_LABELS, self.lens_var.get()), "video")
        self.mirror_var.set(bool(self.settings[mirror_key(self.settings)]))

    def _changed(self, key, value, group):
        self.settings[key] = value
        self._schedule_save()
        if group:
            self._schedule_send(group)

    # -- UI 構築 ----------------------------------------------------------
    def _section(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
        frame.pack(fill="x", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # 熱警告バナー (どのタブを開いていても見えるよう下部に表示)
        self.warn_var = tk.StringVar(value="")
        self.warn_label = ttk.Label(outer, textvariable=self.warn_var, foreground="#c0392b",
                                    font=("", 9, "bold"), wraplength=560, justify="left", padding=(14, 6))
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True, padx=4, pady=(6, 4))
        self._canvases = []
        self.root.bind_all("<MouseWheel>", self._on_wheel)

        p1 = self._page("接続・映像")
        self._build_connection(p1)
        self._build_run_control(p1)
        self._build_video(p1)
        self._build_power(p1)
        p2 = self._page("画質調整")
        self._build_exposure(p2)
        self._build_wb(p2)
        self._build_focus(p2)
        self._build_zoom(p2)
        p3 = self._page("エフェクト")
        self._build_background(p3)
        self._build_color(p3)
        p_rec = self._page("録画")
        self._build_record(p_rec)
        p_aud = self._page("音声")
        self._build_audio(p_aud)
        p4 = self._page("状態")
        self._build_phone_status(p4)
        self._build_latency(p4)
        self._build_thermal_graph(p4)
        p_setup = self._page("セットアップ")
        self._setup_tab = p_setup
        try:
            self._build_setup_tab(p_setup)
        except Exception as e:  # セットアップ画面の不具合でアプリ本体を止めない
            self.setup = None
            ttk.Label(p_setup, text="セットアップ画面を表示できませんでした (%s)" % e).pack(padx=14, pady=14, anchor="w")
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _page(self, title):
        """スクロール可能なタブページを作り、中身を載せるフレームを返す。"""
        page = ttk.Frame(self.nb)
        self.nb.add(page, text=title)
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self._canvases.append(canvas)
        return body

    def _on_wheel(self, e):
        try:
            canvas = self._canvases[self.nb.index("current")]
            canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    def _hint(self, parent, row, text, **grid):
        lbl = ttk.Label(parent, text=text, foreground=HINT_FG, wraplength=480, justify="left")
        lbl.grid(row=row, column=0, columnspan=3, sticky="w", **grid)
        return lbl

    def _slider(self, parent, row, label, key, lo, hi, group, fmt, round_to, as_int=False, var_init=None):
        """ラベル + スライダー + 値表示。戻り値: (scale, value_label)。"""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.DoubleVar(value=float(self.settings[key]))
        val_label = ttk.Label(parent, width=9, anchor="e")
        val_label.grid(row=row, column=2, padx=(6, 0))

        def show(v):
            val_label.config(text=fmt(v))

        show(var.get())
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal")
        scale.grid(row=row, column=1, sticky="ew", padx=6)

        def on_write(*_):
            try:
                v = float(var.get())
            except Exception:
                return
            v = round(v / round_to) * round_to
            if as_int:
                v = int(round(v))
            else:
                v = round(v, 4)
            show(v)
            self._changed(key, v, group)

        var.trace_add("write", on_write)
        return scale, val_label, var

    def _build_connection(self, parent):
        f = self._section(parent, "接続")
        self.mode_var = tk.StringVar(value=self.settings["mode"])
        row = ttk.Frame(f)
        row.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(row, text="USB", value="usb", variable=self.mode_var,
                        command=lambda: self._changed("mode", self.mode_var.get(), None)).pack(side="left")
        ttk.Radiobutton(row, text="Wi-Fi", value="wifi", variable=self.mode_var,
                        command=lambda: self._changed("mode", self.mode_var.get(), None)).pack(side="left", padx=(12, 0))

        ttk.Label(f, text="iPhoneのIP").grid(row=1, column=0, sticky="w", pady=4)
        self.ip_var = tk.StringVar(value=self.settings["ip"])
        self.ip_var.trace_add("write", lambda *_: self._changed("ip", self.ip_var.get(), None))
        ttk.Entry(f, textvariable=self.ip_var, width=20).grid(row=1, column=1, sticky="w", padx=6)

        self.connect_btn = ttk.Button(f, text="接続", command=self.toggle_connect)
        self.connect_btn.grid(row=1, column=2, sticky="e")

        self.status_var = tk.StringVar(value="停止中")
        self.fps_var = tk.StringVar(value="-")
        self.dec_var = tk.StringVar(value="-")
        self.res_var = tk.StringVar(value="-")
        for i, (name, var) in enumerate([("状態", self.status_var), ("受信fps", self.fps_var),
                                         ("デコード時間", self.dec_var), ("解像度", self.res_var)]):
            ttk.Label(f, text=name).grid(row=2 + i, column=0, sticky="w")
            ttk.Label(f, textvariable=var, wraplength=380, justify="left").grid(row=2 + i, column=1, columnspan=2, sticky="w", padx=6)
        self.error_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.error_var, foreground="#c0392b", wraplength=480, justify="left").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _build_run_control(self, parent):
        f = self._section(parent, "iPhoneのカメラの動作")
        ttk.Label(f, text="カメラを動かすタイミング").grid(row=0, column=0, sticky="w", pady=2)
        self.run_mode_var = tk.StringVar(value=self._value_to_label(RUN_MODE_LABELS, self.settings["run_mode"]))
        cb = ttk.Combobox(f, textvariable=self.run_mode_var, values=[l for _, l in RUN_MODE_LABELS],
                          state="readonly", width=26)
        cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._run_mode_changed())

        ttk.Label(f, text="使われなくなってから停止まで").grid(row=1, column=0, sticky="w", pady=2)
        self.idle_var = tk.StringVar(value=self._value_to_label(IDLE_STOP_LABELS, self.settings["idle_stop_sec"]))
        cb = ttk.Combobox(f, textvariable=self.idle_var, values=[l for _, l in IDLE_STOP_LABELS],
                          state="readonly", width=10)
        cb.grid(row=1, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed(
            "idle_stop_sec", self._label_to_value(IDLE_STOP_LABELS, self.idle_var.get()), None))
        self.idle_cb = cb

        ttk.Label(f, text="状態").grid(row=2, column=0, sticky="w", pady=(6, 2))
        self.stream_state_var = tk.StringVar(value="-")
        ttk.Label(f, textvariable=self.stream_state_var, font=("", 9, "bold")).grid(
            row=2, column=1, sticky="w", padx=6, pady=(6, 2))
        self.stream_btn = ttk.Button(f, text="開始", width=10, command=self._stream_button)
        self.stream_btn.grid(row=2, column=2, sticky="e", pady=(6, 2))
        ttk.Label(f, text="カメラ").grid(row=3, column=0, sticky="w")
        self.camera_state_var = tk.StringVar(value="-")
        ttk.Label(f, textvariable=self.camera_state_var).grid(row=3, column=1, sticky="w", padx=6)
        self.run_note_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.run_note_var, foreground="#b7791f", wraplength=480, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._hint(f, 5, "自動: OBSやDiscordなどがPhoneCamカメラを開いている間だけ、iPhoneのカメラを動かします。"
                         "使っていないときはカメラが止まり、発熱とバッテリー消費を抑えられます", pady=(4, 0))

    def _run_mode_changed(self):
        self._changed("run_mode", self._label_to_value(RUN_MODE_LABELS, self.run_mode_var.get()), None)
        self.stream.mode_changed()
        self._stream_tick_once()

    def _stream_button(self):
        self.stream.press_button(self.state.snapshot()["connected"], time.monotonic())
        self._stream_tick_once()

    def _stream_tick(self):
        """0.5秒ごと: 待機フレーム送出 → 使用中の検出 → iPhoneのカメラの起動/停止指示 → 表示。"""
        try:
            self._stream_tick_once()
        except Exception:
            pass
        if not self._closing:
            self.root.after(WATCH_INTERVAL_MS, self._stream_tick)

    def _stream_tick_once(self):
        now = time.monotonic()
        vc = self.link.vcam
        phonecam = self.settings.get("backend") == "phonecam"
        if phonecam:
            vc.standby(canvas_size(self.settings), now)
            if self._vcam_gen != vc.generation:
                self.watcher.reset()
                self._vcam_gen = vc.generation
            self.watcher.poll(now)
            self.stream.note_in_use(self.watcher.in_use(now), now)
        else:
            if self._vcam_gen != -2:
                self.watcher.reset()
                self._vcam_gen = -2
            self.stream.note_in_use(False, now)
        snap = self.state.snapshot()
        connected = snap["connected"]
        want = self.stream.pending(connected, now)
        if want is not None and self.link.send_control({"stream": {"active": want}}):
            self.stream.mark_sent(want)
        st = snap["phone_status"] if connected else {}
        self.stream_state_var.set(self.stream.state_text(connected, st, now))
        self.camera_state_var.set(camera_state_text(connected, st))
        self.stream_btn.config(text=self.stream.button_label(connected, now))
        self._set_enabled(self.stream_btn, connected)
        self._set_enabled(self.idle_cb, self.stream.mode() == "auto")
        self.run_note_var.set(AUTO_UNSUPPORTED_NOTE if self.stream.auto_unsupported() else "")

    def _build_video(self, parent):
        f = self._section(parent, "映像")
        ttk.Label(f, text="レンズ").grid(row=0, column=0, sticky="w", pady=2)
        self.lens_var = tk.StringVar(value=self._value_to_label(LENS_LABELS, self.settings["lens"]))
        cb = ttk.Combobox(f, textvariable=self.lens_var, values=[l for _, l in LENS_LABELS], state="readonly", width=14)
        cb.grid(row=0, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._lens_changed())
        self.lens_combo = cb

        ttk.Label(f, text="解像度").grid(row=1, column=0, sticky="w", pady=2)
        self.res_sel_var = tk.StringVar(value=self.settings["resolution"])
        cb = ttk.Combobox(f, textvariable=self.res_sel_var, values=RES_LABELS, state="readonly", width=14)
        cb.grid(row=1, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("resolution", self.res_sel_var.get(), "video"))

        ttk.Label(f, text="fps").grid(row=2, column=0, sticky="w", pady=2)
        self.fps_sel_var = tk.StringVar(value=str(self.settings["fps"]))
        cb = ttk.Combobox(f, textvariable=self.fps_sel_var, values=[str(x) for x in FPS_LABELS], state="readonly", width=14)
        cb.grid(row=2, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("fps", int(self.fps_sel_var.get()), "video"))

        self._slider(f, 3, "ビットレート (Mbps)", "bitrate", 5, 50, "bitrate", lambda v: "%d Mbps" % round(v), 1, as_int=True)

        self.mirror_var = tk.BooleanVar(value=bool(self.settings[mirror_key(self.settings)]))
        ttk.Checkbutton(f, text="左右反転", variable=self.mirror_var,
                        command=lambda: self._changed(mirror_key(self.settings), bool(self.mirror_var.get()), None)).grid(row=4, column=0, columnspan=2, sticky="w")
        self.rot_var = tk.BooleanVar(value=bool(self.settings["rotate180"]))
        ttk.Checkbutton(f, text="180°回転", variable=self.rot_var,
                        command=lambda: self._changed("rotate180", bool(self.rot_var.get()), None)).grid(row=5, column=0, columnspan=2, sticky="w")

        ttk.Label(f, text="出力先").grid(row=6, column=0, sticky="w", pady=2)
        self.backend_var = tk.StringVar(value=self._value_to_label(BACKEND_LABELS, self.settings["backend"]))
        cb = ttk.Combobox(f, textvariable=self.backend_var, values=[l for _, l in BACKEND_LABELS], state="readonly", width=24)
        cb.grid(row=6, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_backend())

        ttk.Label(f, text="向き").grid(row=7, column=0, sticky="w", pady=2)
        self.orient_var = tk.StringVar(value=self._value_to_label(ORIENT_LABELS, self.settings.get("orientation", "auto")))
        cb = ttk.Combobox(f, textvariable=self.orient_var, values=[l for _, l in ORIENT_LABELS], state="readonly", width=14)
        cb.grid(row=7, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("orientation", self._label_to_value(ORIENT_LABELS, self.orient_var.get()), "video"))

        ttk.Label(f, text="出力の形").grid(row=8, column=0, sticky="w", pady=2)
        self.canvas_var = tk.StringVar(value=self._value_to_label(CANVAS_LABELS, self.settings["canvas"]))
        cb = ttk.Combobox(f, textvariable=self.canvas_var, values=[l for _, l in CANVAS_LABELS], state="readonly", width=24)
        cb.grid(row=8, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("canvas", self._label_to_value(CANVAS_LABELS, self.canvas_var.get()), None))
        ttk.Label(f, text="合わせ方").grid(row=9, column=0, sticky="w", pady=2)
        self.fit_var = tk.StringVar(value=self._value_to_label(FIT_LABELS, self.settings["fit"]))
        cb = ttk.Combobox(f, textvariable=self.fit_var, values=[l for _, l in FIT_LABELS], state="readonly", width=24)
        cb.grid(row=9, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("fit", self._label_to_value(FIT_LABELS, self.fit_var.get()), None))
        self._hint(f, 10, "向きが変わってもOBSの映像が止まらないよう、出力サイズを固定します")

        ttk.Label(f, text="コーデック").grid(row=11, column=0, sticky="w", pady=2)
        self.codec_var = tk.StringVar(value=self._value_to_label(CODEC_LABELS, self.settings["codec"]))
        cb = ttk.Combobox(f, textvariable=self.codec_var, values=[l for _, l in CODEC_LABELS], state="readonly", width=14)
        cb.grid(row=11, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("codec", self._label_to_value(CODEC_LABELS, self.codec_var.get()), "codec"))
        self._hint(f, 12, "HEVC は同じ画質をより低いビットレートで送れます（Wi-Fi向き）")
        self._hint(f, 13, "出力先「PhoneCam（専用カメラ）」は OBS や Discord のカメラ一覧に「PhoneCam」と表示されます。"
                          "OBSの仮想カメラと同時に使えます" +
                          ("（初回は下の「登録する」が必要です）" if _is_frozen() else "（初回は register.bat が必要）"))
        self._build_cam_registration(f, 14)

    def _build_power(self, parent):
        f = self._section(parent, "省電力")
        self.screen_black_var = tk.BooleanVar(value=bool(self.settings["screen_black"]))
        ttk.Checkbutton(f, text="iPhoneの画面を真っ黒にする", variable=self.screen_black_var,
                        command=lambda: self._screen_changed(
                            "screen_black", bool(self.screen_black_var.get()))).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(f, text="操作がないと自動で黒くする").grid(row=1, column=0, sticky="w", pady=2)
        self.screen_auto_var = tk.StringVar(value=self._value_to_label(SCREEN_AUTO_LABELS, self.settings["screen_auto"]))
        cb = ttk.Combobox(f, textvariable=self.screen_auto_var, values=[l for _, l in SCREEN_AUTO_LABELS],
                          state="readonly", width=10)
        cb.grid(row=1, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._screen_changed(
            "screen_auto", self._label_to_value(SCREEN_AUTO_LABELS, self.screen_auto_var.get())))
        self.screen_state_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.screen_state_var, foreground=HINT_FG).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._hint(f, 3, "画面を黒くしても配信は続きます。発熱とバッテリーの消費を抑えられます")

    def _screen_changed(self, key, value):
        self._screen_user_t = time.monotonic()
        self._changed(key, value, "screen")

    def _on_backend(self):
        self._changed("backend", self._label_to_value(BACKEND_LABELS, self.backend_var.get()), None)
        self.link.vcam.request_reset()
        self.stream.note_in_use(False, time.monotonic())
        self._stream_tick_once()

    # -- PhoneCam 専用カメラの登録 (exe 版) ---------------------------------
    CAM_STATUS_TEXT = {"ok": "登録済み", "missing": "未登録", "outdated": "更新が必要", "unavailable": "利用できません"}

    def _build_cam_registration(self, f, row):
        ttk.Label(f, text="PhoneCamカメラ").grid(row=row, column=0, sticky="w", pady=(8, 2))
        box = ttk.Frame(f)
        box.grid(row=row, column=1, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.cam_status_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.cam_status_var).pack(side="left", padx=(0, 10))
        self.cam_reg_btn = ttk.Button(box, text="登録する（更新）", command=lambda: self._start_elevated("register"))
        self.cam_reg_btn.pack(side="left", padx=2)
        self.cam_unreg_btn = ttk.Button(box, text="登録を解除", command=lambda: self._start_elevated("unregister"))
        self.cam_unreg_btn.pack(side="left", padx=2)
        self.cam_note_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.cam_note_var, foreground="#c0392b", wraplength=480,
                  justify="left").grid(row=row + 1, column=0, columnspan=3, sticky="w")
        if not _is_frozen():
            self.cam_status_var.set("exe 版のみ（ソース実行では register.bat を使います）")
            self._set_enabled(self.cam_reg_btn, False)
            self._set_enabled(self.cam_unreg_btn, False)
        else:
            self._refresh_cam_status()

    def _refresh_cam_status(self):
        if not _is_frozen():
            return "ok"
        try:
            st = registration_status()
        except Exception:
            st = "unavailable"
        self.cam_status_var.set(self.CAM_STATUS_TEXT.get(st, st))
        self._set_enabled(self.cam_reg_btn, st != "unavailable" and not self._elev_busy)
        self._set_enabled(self.cam_unreg_btn, st in ("ok", "outdated") and not self._elev_busy)
        return st

    def _first_run_check(self):
        """起動後の確認 (exe 版のみ)。初回はセットアップウィザード、2回目以降は専用カメラの更新だけを案内する。"""
        if self._closing or not _is_frozen():
            return
        if self.setup is not None and not self.settings.get("setup_done"):
            if self._setup_key_present is False and self._refresh_cam_status() == "ok":
                # 旧バージョンから使っていて、専用カメラも登録済み: ウィザードは出さない
                self.settings["setup_done"] = True
                self._schedule_save()
                return
            self.open_setup_wizard()
            return
        if self.settings.get("backend") != "phonecam":
            return
        st = self._refresh_cam_status()
        if st not in ("missing", "outdated"):
            return
        if st == "outdated":
            msg = "新しいバージョンの専用カメラに更新します。このあと管理者の確認が表示されたら「はい」を押してください。"
        else:
            msg = "専用カメラ「PhoneCam」をWindowsに登録します。このあと管理者の確認が表示されたら「はい」を押してください。"
        if messagebox.askyesno("PhoneCam", msg):
            self._start_elevated("register")
        else:
            self.cam_note_var.set("専用カメラ「PhoneCam」は未登録（または古い）のままです。使うには「登録する（更新）」を押してください。"
                                  "それまでは出力先に「OBS Virtual Camera」などを選べます。")

    # -- セットアップ (必要ソフトの導入) -----------------------------------
    def _setup_busy(self, flag):
        """セットアップ実行中は、専用カメラの登録ボタンなど管理者権限の操作を止める。"""
        self._elev_busy = bool(flag)
        try:
            self._refresh_cam_status()
        except Exception:
            pass

    def _setup_changed(self):
        """検出結果が更新された。専用カメラの状態表示を合わせ、登録が変わっていれば出力を作り直す。"""
        try:
            self._refresh_cam_status()
            self.link.vcam.request_reset()
        except Exception:
            pass

    def _tab_changed(self, _e=None):
        self._graph_dirty = True
        try:
            if self.setup is not None and self.nb.index("current") == self.nb.index(self._setup_tab):
                now = time.monotonic()
                if not self.setup.busy and now - self._setup_last_check > 3.0:
                    self._setup_last_check = now
                    self.setup.recheck()
        except Exception:
            pass

    def _build_setup_tab(self, parent):
        if self.setup is None:
            ttk.Label(parent, text="セットアップ機能を読み込めませんでした。").pack(padx=14, pady=14, anchor="w")
            return
        ps = phonecam_setup
        wrap = tk.Frame(parent, bg=ps.BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=8)
        inner = tk.Frame(wrap, bg=ps.BG)
        inner.pack(fill="both", expand=True, padx=16, pady=14)
        tk.Label(inner, text="セットアップ", bg=ps.BG, fg=ps.FG, font=(ps.FONT, 18, "bold")).pack(anchor="w")
        self.setup_summary_var = tk.StringVar(value="確認しています…")
        tk.Label(inner, textvariable=self.setup_summary_var, bg=ps.BG, fg=ps.SUB, font=(ps.FONT, 10)).pack(anchor="w", pady=(4, 10))
        self.setup_panel = ps.SetupPanel(inner, self.setup, "tab", on_install_one=self._setup_install_one)
        self.setup_panel.pack(fill="x")
        bar = tk.Frame(inner, bg=ps.BG)
        bar.pack(fill="x", pady=(14, 0))
        self.setup_recheck_btn = ps.make_button(bar, "再チェック", self._setup_recheck)
        self.setup_recheck_btn.pack(side="left")
        self.setup_all_btn = ps.make_button(bar, "足りないものをすべてインストール", self._setup_install_missing, primary=True)
        self.setup_all_btn.pack(side="left", padx=(10, 0))
        self.setup_note_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=self.setup_note_var, bg=ps.BG, fg=ps.WARN, font=(ps.FONT, 10), justify="left",
                 wraplength=480).pack(anchor="w", pady=(10, 0))
        self.setup_manual_box = tk.Frame(inner, bg=ps.BG)
        self.setup_manual_box.pack(anchor="w")
        tk.Frame(inner, bg=ps.LINE, height=1).pack(fill="x", pady=(14, 10))
        tk.Label(inner, text="iPhoneへのインストール", bg=ps.BG, fg=ps.FG, font=(ps.FONT, 11, "bold")).pack(anchor="w")
        row = tk.Frame(inner, bg=ps.BG)
        row.pack(anchor="w", pady=(8, 0))
        self.setup_ipa_btn = ps.make_button(row, "iPhone用アプリをダウンロード", self._setup_open_ipa)
        if ps.ipa_url():
            self.setup_ipa_btn.pack(side="left")
        self.setup_alt_btn = ps.make_button(row, "AltServerを起動", self._setup_launch_altserver)
        self.setup_alt_btn.pack(side="left", padx=(10, 0))
        self.setup_alt_btn.pack_forget()
        rerun = ps.make_link(inner, "初期セットアップをやり直す", self._setup_rerun)
        rerun.pack(anchor="w", pady=(16, 0))
        self.setup.listen(self._setup_event)
        self.setup_panel.render()
        self.root.after(400, self._setup_initial_check)

    def _setup_initial_check(self):
        if self._closing or self.setup is None:
            return
        self._setup_last_check = time.monotonic()
        self.setup.recheck()

    def _setup_event(self, what):
        """SetupController からの通知 (UI スレッド)。"""
        if self._closing or self.setup is None:
            return
        ps = phonecam_setup
        if what in ("statuses", "checking", "start", "done"):
            self.setup_panel.render()
            self._setup_update_summary()
        if what == "start":
            self.setup_note_var.set("インストール中です。管理者の確認が表示されたら「はい」を押してください…")
            self._setup_clear_manual()
        elif what == "stage" and self.setup.busy and self._wizard is None:
            self.setup_note_var.set(self.setup.stage)
        elif what == "done":
            self._setup_show_outcome()

    def _setup_update_summary(self):
        ps = phonecam_setup
        c = self.setup
        if not c.checked:
            text = "確認しています…"
        else:
            missing = [x for x in ps.COMPONENTS if c.statuses.get(x["id"], {}).get("state") != ps.STATE_OK
                       and ps.is_selectable(x["id"], c.statuses)]
            text = "すべて導入済みです" if not missing else "未導入・要確認: %d 件" % len(missing)
        self.setup_summary_var.set(text)
        busy = c.busy
        for b in (self.setup_recheck_btn, self.setup_all_btn):
            try:
                b.config(state="disabled" if busy else "normal")
            except Exception:
                pass
        if ps.find_altserver():
            self.setup_alt_btn.pack(side="left", padx=(10, 0))
        else:
            self.setup_alt_btn.pack_forget()

    def _setup_clear_manual(self):
        for w in list(self.setup_manual_box.winfo_children()):
            w.destroy()

    def _setup_show_outcome(self):
        ps = phonecam_setup
        out = self.setup.outcome or {}
        if self._wizard is not None:
            return
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        failed = [ps.COMPONENT_BY_ID[c]["name"] for c, r in out.get("results", {}).items() if not r.get("ok")]
        if failed and not out.get("error"):
            details = " / ".join("%s: %s" % (ps.COMPONENT_BY_ID[c]["name"], r.get("detail", "")) for c, r in
                                 out["results"].items() if not r.get("ok"))
            notes.append("完了しなかった項目があります。" + details)
        if out.get("reboot"):
            notes.append("再起動が必要です。PCを再起動してください。")
        if not notes:
            notes.append("インストールが完了しました。")
        self.setup_note_var.set("\n".join(notes))
        self._setup_clear_manual()
        for m in out.get("manual", []):
            ps.make_link(self.setup_manual_box, "%s の公式ページを開く（%s）" % (m["name"], m["text"]),
                         lambda u=m["url"]: webbrowser_open(u), bg=ps.BG).pack(anchor="w")

    def _setup_recheck(self):
        if self.setup is not None:
            self.setup.recheck()

    def _setup_install_one(self, cid):
        if self.setup is not None:
            self.setup.start([cid])

    def _setup_install_missing(self):
        if self.setup is None:
            return
        ps = phonecam_setup
        ids = [x["id"] for x in ps.COMPONENTS
               if ps.is_selectable(x["id"], self.setup.statuses)
               and (x["required"] or x["id"] == "altserver")]
        if not ids:
            self.setup_note_var.set("インストールが必要なものはありません。")
            return
        self.setup.start(ids)

    def _setup_open_ipa(self):
        url = phonecam_setup.ipa_url()
        if url:
            webbrowser_open(url)

    def _setup_launch_altserver(self):
        path = phonecam_setup.find_altserver()
        if path:
            phonecam_setup._startfile(path)

    def _setup_rerun(self):
        self.open_setup_wizard(rerun=True)

    def open_setup_wizard(self, rerun=False):
        if self.setup is None or self._wizard is not None or tk is None:
            return
        if self.setup.busy:
            return
        if rerun:
            self.settings["setup_done"] = False
            self._do_save_now()
        self.setup.touched.clear()
        self.setup.mic = False
        self.setup.checked = False
        self.setup.outcome = None
        self.setup_note_var.set("")
        try:
            self._wizard = phonecam_setup.SetupWizard(self.root, self.setup, on_finish=self._wizard_closed,
                                                      on_skip=self._wizard_closed, open_url=webbrowser_open)
        except Exception:
            self._wizard = None

    def _wizard_closed(self):
        self._wizard = None
        self.settings["setup_done"] = True
        self._do_save_now()
        try:
            self.setup.recheck()
        except Exception:
            pass

    def _do_save_now(self):
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        save_settings(self.settings)

    def _start_elevated(self, action):
        if self._elev_busy or not _is_frozen():
            return
        self._elev_busy = True
        self._elev_result = None
        self.cam_note_var.set("管理者の確認が表示されたら「はい」を押してください…")
        self._refresh_cam_status()
        param = "--register" if action == "register" else "--unregister"

        def work():
            try:
                self._elev_result = run_elevated(param)
            except Exception as e:
                self._elev_result = ("error", str(e))

        threading.Thread(target=work, name="elevated-" + action, daemon=True).start()
        self.root.after(200, lambda: self._elev_poll(action))

    def _elev_poll(self, action):
        if self._closing:
            return
        res = self._elev_result
        if res is None:
            self.root.after(200, lambda: self._elev_poll(action))
            return
        self._elev_busy = False
        self._elev_result = None
        kind = res[0]
        st = self._refresh_cam_status()
        self.link.vcam.request_reset()
        word = "登録" if action == "register" else "登録解除"
        if kind == "cancelled":
            self.cam_note_var.set("管理者の確認がキャンセルされたため、%sはしませんでした。もう一度ボタンを押してください。" % word)
        elif kind == "ok":
            if action == "register" and st == "ok":
                self.cam_note_var.set("")
                messagebox.showinfo("PhoneCam", "専用カメラ「PhoneCam」を登録しました。OBS や Discord のカメラ一覧に表示されます。"
                                                "（すでにOBSで開いている場合は、ソースを開き直してください）")
            elif action == "unregister" and st in ("missing", "unavailable"):
                self.cam_note_var.set("")
                messagebox.showinfo("PhoneCam", "専用カメラ「PhoneCam」の登録を解除しました。")
            else:
                self.cam_note_var.set("%sの処理は終わりましたが、状態が変わっていません。もう一度お試しください。" % word)
        elif kind == "timeout":
            self.cam_note_var.set("%sの処理が時間内に終わりませんでした。もう一度お試しください。" % word)
        else:
            self.cam_note_var.set("%sに失敗しました。管理者の確認で「はい」を押したか、他のアプリが「PhoneCam」カメラを使用中でないか確認してください。" % word)

    def _mode_combo(self, parent, row, key, group, on_change):
        ttk.Label(parent, text="モード").grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self._value_to_label(MODE3_LABELS, self.settings[key]))
        cb = ttk.Combobox(parent, textvariable=var, values=[l for _, l in MODE3_LABELS], state="readonly", width=14)
        cb.grid(row=row, column=1, sticky="w", padx=6)

        def sel(_e=None):
            self._changed(key, self._label_to_value(MODE3_LABELS, var.get()), group)
            on_change()

        cb.bind("<<ComboboxSelected>>", sel)
        return cb

    def _build_exposure(self, parent):
        f = self._section(parent, "露出")
        self.exp_mode_cb = self._mode_combo(f, 0, "exp_mode", "exposure", self._update_enabled)
        self.bias_scale, _, _ = self._slider(f, 1, "露出補正", "exp_bias", -2.0, 2.0, "exposure", lambda v: "%+.1f EV" % v, 0.1)
        self.iso_scale, self.iso_label, _ = self._slider(f, 2, "ISO", "iso", 25, 3200, "exposure", lambda v: "%d" % round(v), 1, as_int=True)

        ttk.Label(f, text="シャッター速度").grid(row=3, column=0, sticky="w", pady=2)
        self.shutter_var = tk.StringVar(value="1/%d" % int(self.settings["shutter"]))
        self.shutter_cb = ttk.Combobox(f, textvariable=self.shutter_var, values=["1/%d" % s for s in SHUTTER_VALUES], state="readonly", width=14)
        self.shutter_cb.grid(row=3, column=1, sticky="w", padx=6)

        def on_shutter(_e=None):
            try:
                v = int(self.shutter_var.get().split("/")[1])
            except Exception:
                return
            self._changed("shutter", v, "exposure")

        self.shutter_cb.bind("<<ComboboxSelected>>", on_shutter)
        ttk.Label(f, text="東日本(50Hz)は1/50か1/100、西日本(60Hz)は1/60か1/120でちらつき防止",
                  foreground="#666666", wraplength=480, justify="left").grid(row=4, column=0, columnspan=3, sticky="w")
        self.cur_exp_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.cur_exp_var, foreground="#666666", font=("", 8)).grid(row=5, column=0, columnspan=3, sticky="w")

    def _build_wb(self, parent):
        f = self._section(parent, "ホワイトバランス")
        self.wb_mode_cb = self._mode_combo(f, 0, "wb_mode", "wb", self._update_enabled)
        self.temp_scale, _, _ = self._slider(f, 1, "色温度", "temperature", 2500, 8000, "wb", lambda v: "%d K" % round(v), 50, as_int=True)
        self.tint_scale, _, _ = self._slider(f, 2, "色かぶり", "tint", -50, 50, "wb", lambda v: "%d" % round(v), 1, as_int=True)
        self.cur_wb_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.cur_wb_var, foreground="#666666", font=("", 8)).grid(row=3, column=0, columnspan=3, sticky="w")

    def _build_focus(self, parent):
        f = self._section(parent, "フォーカス")
        self.focus_mode_cb = self._mode_combo(f, 0, "focus_mode", "focus", self._update_enabled)
        self.focus_scale, _, _ = self._slider(f, 1, "位置 (0=近い 1=遠い)", "focus_pos", 0.0, 1.0, "focus", lambda v: "%.2f" % v, 0.01)
        self.cur_focus_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.cur_focus_var, foreground="#666666", font=("", 8)).grid(row=2, column=0, columnspan=3, sticky="w")
        self.focus_widgets = [self.focus_mode_cb, self.focus_scale]

    def _build_zoom(self, parent):
        f = self._section(parent, "ズーム・ライト")
        self.zoom_scale, _, _ = self._slider(f, 0, "ズーム", "zoom", 1.0, 10.0, "zoom", lambda v: "%.1fx" % v, 0.1)
        self.torch_var = tk.BooleanVar(value=bool(self.settings["torch"]))
        self.torch_cb = ttk.Checkbutton(f, text="ライト", variable=self.torch_var,
                                        command=lambda: self._changed("torch", bool(self.torch_var.get()), "torch"))
        self.torch_cb.grid(row=1, column=0, columnspan=2, sticky="w")

    def _build_background(self, parent):
        f = self._section(parent, "背景")
        ttk.Label(f, text="モード").grid(row=0, column=0, sticky="w", pady=2)
        self.bg_mode_var = tk.StringVar(value=self._value_to_label(BG_MODE_LABELS, self.settings["bg_mode"]))
        cb = ttk.Combobox(f, textvariable=self.bg_mode_var, values=[l for _, l in BG_MODE_LABELS], state="readonly", width=14)
        cb.grid(row=0, column=1, sticky="w", padx=6)

        def on_mode(_e=None):
            self._changed("bg_mode", self._label_to_value(BG_MODE_LABELS, self.bg_mode_var.get()), "background")
            self._update_enabled()

        cb.bind("<<ComboboxSelected>>", on_mode)
        self.bg_strength_scale, _, _ = self._slider(
            f, 1, "強さ", "bg_strength", 0.0, 1.0, "background", lambda v: "%d%%" % round(v * 100), 0.05)

        ttk.Label(f, text="品質").grid(row=2, column=0, sticky="w", pady=2)
        self.bg_quality_var = tk.StringVar(value=self._value_to_label(BG_QUALITY_LABELS, self.settings["bg_quality"]))
        self.bg_quality_cb = ttk.Combobox(f, textvariable=self.bg_quality_var, values=[l for _, l in BG_QUALITY_LABELS],
                                          state="readonly", width=14)
        self.bg_quality_cb.grid(row=2, column=1, sticky="w", padx=6)
        self.bg_quality_cb.bind("<<ComboboxSelected>>", lambda e: self._changed(
            "bg_quality", self._label_to_value(BG_QUALITY_LABELS, self.bg_quality_var.get()), "background"))
        self.bg_hint = self._hint(f, 3, "OBSのソースに「クロマキー」フィルタ(緑)を追加すると背景が透明になります", pady=(4, 0))
        self.bg_hint_visible = True

    def _build_color(self, parent):
        f = self._section(parent, "色調整")
        self._color_vars = {}
        specs = [("明るさ", "brightness", -0.5, 0.5, lambda v: "%+.2f" % v, 0.01),
                 ("コントラスト", "contrast", 0.5, 1.5, lambda v: "%.2f" % v, 0.01),
                 ("彩度", "saturation", 0.0, 2.0, lambda v: "%.2f" % v, 0.01),
                 ("シャープネス", "sharpness", 0.0, 1.0, lambda v: "%.2f" % v, 0.01)]
        for row, (label, key, lo, hi, fmt, step) in enumerate(specs):
            _, _, var = self._slider(f, row, label, key, lo, hi, "color", fmt, step)
            self._color_vars[key] = var
        ttk.Button(f, text="リセット", command=self._reset_color).grid(row=len(specs), column=2, sticky="e", pady=(6, 0))

    def _reset_color(self):
        for key, var in self._color_vars.items():
            var.set(COLOR_DEFAULTS[key])

    def _build_phone_status(self, parent):
        f = self._section(parent, "iPhoneの状態")
        self._default_fg = ttk.Style().lookup("TLabel", "foreground") or "#1d1d1f"
        self.st_vars = {}
        self.st_labels = {}
        for row, (key, name) in enumerate([("thermal", "温度"), ("pressure", "カメラの負荷"), ("factors", "原因"),
                                           ("hot", "「高い」以上だった時間"), ("sentfps", "送信fps"),
                                           ("dropped", "落ちたフレーム"), ("encode", "エンコード時間"),
                                           ("battery", "バッテリー"), ("lowpower", "低電力モード"),
                                           ("processing", "エフェクト処理時間")]):
            ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value="-")
            lbl = ttk.Label(f, textvariable=var)
            lbl.grid(row=row, column=1, sticky="w", padx=6)
            self.st_vars[key] = var
            self.st_labels[key] = lbl
        self._hint(f, 10, "接続中のiPhoneから約1秒ごとに更新されます", pady=(6, 0))

    def _build_record(self, parent):
        f = self._section(parent, "録画の設定（iPhone側で高画質に録画）")
        ttk.Label(f, text="解像度").grid(row=0, column=0, sticky="w", pady=2)
        self.rec_res_var = tk.StringVar(value=self._value_to_label(REC_RES_LABELS, self.settings["rec_resolution"]))
        cb = ttk.Combobox(f, textvariable=self.rec_res_var, values=[l for _, l in REC_RES_LABELS],
                          state="readonly", width=14)
        cb.grid(row=0, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed(
            "rec_resolution", self._label_to_value(REC_RES_LABELS, self.rec_res_var.get()), "recordSettings"))
        self._slider(f, 1, "画質 (Mbps)", "rec_bitrate", 10, 100, "recordSettings",
                     lambda v: "%d Mbps" % round(v), 5, as_int=True)
        self.rec_audio_var = tk.BooleanVar(value=bool(self.settings["rec_audio"]))
        ttk.Checkbutton(f, text="音声を入れる", variable=self.rec_audio_var,
                        command=lambda: self._changed("rec_audio", bool(self.rec_audio_var.get()), "recordSettings")
                        ).grid(row=2, column=0, columnspan=3, sticky="w")
        self.rec_photos_var = tk.BooleanVar(value=bool(self.settings["rec_photos"]))
        ttk.Checkbutton(f, text="写真アプリにも保存", variable=self.rec_photos_var,
                        command=lambda: self._changed("rec_photos", bool(self.rec_photos_var.get()), "recordSettings")
                        ).grid(row=3, column=0, columnspan=3, sticky="w")
        self._hint(f, 4, "4K録画中は配信用の映像を縮小して送ります。発熱が増えるので30fpsがおすすめです", pady=(6, 0))
        self._hint(f, 5, "録画ファイルはiPhoneの「ファイル」アプリ（このiPhone内 > PhoneCam）にも残ります")
        self._hint(f, 6, "初めて使うときは、iPhoneでマイクと写真への保存の許可が必要です")

        g = self._section(parent, "録画")
        ttk.Style().configure("Rec.TButton", font=("", 12, "bold"), padding=(24, 10))
        self.rec_btn = ttk.Button(g, text="録画開始", style="Rec.TButton", command=self._rec_clicked)
        self.rec_btn.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        self.rec_vars = {}
        self.rec_labels = {}
        for row, (key, name) in enumerate([("state", "状態"), ("file", "ファイル名"),
                                           ("size", "サイズ"), ("free", "iPhoneの空き容量")], start=1):
            ttk.Label(g, text=name).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value="-")
            lbl = ttk.Label(g, textvariable=var, wraplength=380, justify="left")
            lbl.grid(row=row, column=1, columnspan=2, sticky="w", padx=6)
            self.rec_vars[key] = var
            self.rec_labels[key] = lbl
        self.rec_error_var = tk.StringVar(value="")
        ttk.Label(g, textvariable=self.rec_error_var, foreground="#c0392b", wraplength=520, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.rec_btn.state(["disabled"])

    def _rec_clicked(self):
        rec = self.state.snapshot()["phone_status"].get("recording")
        active = bool(rec.get("active")) if isinstance(rec, dict) else False
        if self.link.send_control({"record": "stop" if active else "start"}):
            self._rec_lock_until = time.monotonic() + 1.5
            self.rec_btn.state(["disabled"])

    def _build_audio(self, parent):
        f = self._section(parent, "iPhoneのマイク")
        self.mic_var = tk.BooleanVar(value=bool(self.settings["mic_enabled"]))
        self.mic_cb = ttk.Checkbutton(f, text="iPhoneのマイクを使う", variable=self.mic_var, command=self._mic_toggled)
        self.mic_cb.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(f, text="出力先").grid(row=1, column=0, sticky="w", pady=2)
        try:
            self._audio_devices = list_output_devices() if AUDIO_AVAILABLE else []
        except Exception:
            self._audio_devices = []
        names = [d["name"] for d in self._audio_devices]
        cur = choose_output_device(self._audio_devices, self.settings["mic_device"])
        self.audio_dev_var = tk.StringVar(value=cur["name"] if cur else DEFAULT_DEVICE_LABEL)
        self.audio_dev_cb = ttk.Combobox(f, textvariable=self.audio_dev_var, values=[DEFAULT_DEVICE_LABEL] + names,
                                         state="readonly", width=42)
        self.audio_dev_cb.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6)
        self.audio_dev_cb.bind("<<ComboboxSelected>>", lambda e: self._audio_device_changed())

        self.vol_scale, _, _ = self._slider(f, 2, "音量", "mic_volume", 0, 200, None,
                                            lambda v: "%d%%" % round(v), 5, as_int=True)
        self.mute_var = tk.BooleanVar(value=bool(self.settings["mic_mute"]))
        self.mute_cb = ttk.Checkbutton(f, text="ミュート", variable=self.mute_var,
                                       command=lambda: self._changed("mic_mute", bool(self.mute_var.get()), None))
        self.mute_cb.grid(row=3, column=0, columnspan=3, sticky="w")

        ttk.Label(f, text="入力レベル").grid(row=4, column=0, sticky="w", pady=(6, 2))
        self.meter = tk.Canvas(f, height=12, highlightthickness=0, borderwidth=0, background="#e5e5ea")
        self.meter.grid(row=4, column=1, sticky="ew", padx=6, pady=(6, 2))
        self._meter_bar = self.meter.create_rectangle(0, 0, 0, 12, width=0, fill="#34c759")
        self.meter_text = ttk.Label(f, text="", width=9, anchor="e", foreground=HINT_FG)
        self.meter_text.grid(row=4, column=2, padx=(6, 0))

        self.delay_scale, _, _ = self._slider(f, 5, "音声の遅延", "mic_delay_ms", 0, 500, None,
                                              lambda v: "%d ms" % round(v), 10, as_int=True)
        self._hint(f, 6, "映像より音が早いときに増やして、映像に合わせます", pady=(0, 4))
        self.audio_info_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.audio_info_var, foreground=HINT_FG, wraplength=520, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w")
        self.audio_err_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.audio_err_var, foreground="#c0392b", wraplength=520, justify="left").grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self._hint(f, 9, "VB-CABLE（無料）をインストールして出力先を「CABLE Input」にすると、"
                         "OBSやDiscordでマイクとして「CABLE Output」を選べます", pady=(6, 0))
        self._hint(f, 10, "初めて使うときは、iPhoneでマイクの許可が必要です")
        if not AUDIO_AVAILABLE:
            self.audio_err_var.set("音声ライブラリ(sounddevice)が見つかりません。install.bat を実行してください")
            for w in (self.mic_cb, self.audio_dev_cb, self.vol_scale, self.mute_cb, self.delay_scale):
                self._set_enabled(w, False)

    def _mic_toggled(self):
        self._changed("mic_enabled", bool(self.mic_var.get()), "mic")
        self._apply_audio()

    def _audio_device_changed(self):
        label = self.audio_dev_var.get()
        self._changed("mic_device", SYSTEM_DEFAULT_KEY if label == DEFAULT_DEVICE_LABEL else label, None)
        self._apply_audio()

    def _apply_audio(self):
        """設定に合わせて音声ストリームを開く/閉じる。"""
        if not AUDIO_AVAILABLE:
            return
        self._audio_retry_t = time.monotonic()
        if self.settings["mic_enabled"]:
            self.audio.start(self.settings["mic_device"])
        else:
            self.audio.stop()

    def _meter_tick(self):
        """入力レベルメーターを約20Hzで更新する。"""
        try:
            db = self.audio.level_db() if (AUDIO_AVAILABLE and self.audio.running) else -100.0
            if db >= self._meter_db:
                self._meter_db = db
            else:
                self._meter_db = max(db, self._meter_db - 3.0)
            shown = max(METER_MIN_DB, min(0.0, self._meter_db))
            w = self.meter.winfo_width()
            x = int(w * (shown - METER_MIN_DB) / -METER_MIN_DB)
            color = "#34c759" if shown < -12 else ("#ff9f0a" if shown < -3 else "#ff3b30")
            self.meter.coords(self._meter_bar, 0, 0, x, 12)
            self.meter.itemconfigure(self._meter_bar, fill=color)
            self.meter_text.config(text="%d dBFS" % round(shown) if self._meter_db > METER_MIN_DB else "-")
        except Exception:
            pass
        self.root.after(50, self._meter_tick)

    def _build_latency(self, parent):
        f = self._section(parent, "遅延（直近の平均）")
        self.lat_vars = {}
        for row, (key, name) in enumerate([("iphone", "iPhone処理"), ("comm", "通信"), ("pc", "PC処理"),
                                           ("total", "合計"), ("rtt", "往復(RTT)")]):
            ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value="-")
            ttk.Label(f, textvariable=var, font=("", 9, "bold") if key == "total" else "TkDefaultFont").grid(
                row=row, column=1, sticky="w", padx=6)
            self.lat_vars[key] = var
        self._hint(f, 5, "カメラの露光やOBSの表示までの時間は含みません", pady=(6, 0))
        ttk.Button(f, text="計測用タイマーを開く", command=self.open_timer).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def open_timer(self):
        """実測用の大きなミリ秒タイマー。カメラに映して OBS のプレビューと一緒に撮ると遅延が分かる。"""
        if self._timer_win is not None:
            try:
                self._timer_win.lift()
                return
            except Exception:
                self._timer_win = None
        win = tk.Toplevel(self.root)
        self._timer_win = win
        win.title("計測用タイマー")
        win.attributes("-topmost", True)
        win.configure(background="#ffffff")
        win.bind("<Escape>", lambda e: win.destroy())
        t0 = time.perf_counter()
        label = tk.Label(win, text="00:00.000", font=("Consolas", 72, "bold"), background="#ffffff",
                         foreground="#1d1d1f", padx=30, pady=16)
        label.pack()
        tk.Label(win, text="カメラをこのタイマーに向け、OBSのプレビューに映るタイマーと実物のタイマーを"
                           "一緒にスクリーンショットすると、合計の遅延が分かります",
                 font=("", 10), background="#ffffff", foreground="#666666", wraplength=560,
                 justify="center", padx=20, pady=(0)).pack(pady=(0, 16))
        job = {"id": None}

        def tick():
            ms = int((time.perf_counter() - t0) * 1000)
            label.config(text="%02d:%02d.%03d" % (ms // 60000 % 100, ms // 1000 % 60, ms % 1000))
            job["id"] = win.after(5, tick)

        def closed(_e=None):
            if _e is not None and _e.widget is not win:
                return
            if job["id"] is not None:
                try:
                    win.after_cancel(job["id"])
                except Exception:
                    pass
            self._timer_win = None

        win.bind("<Destroy>", closed)
        tick()

    def _build_thermal_graph(self, parent):
        f = self._section(parent, "温度の履歴（直近30分）")
        self.graph = tk.Canvas(f, height=140, highlightthickness=0, borderwidth=0, background="#fbfbfd")
        self.graph.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.graph.bind("<Configure>", lambda e: setattr(self, "_graph_dirty", True))
        self._hint(f, 1, "色つきの太い線: iPhoneの温度　灰色の細い線: 受信fps", pady=(4, 0))

    def _draw_graph(self):
        c = self.graph
        c.delete("all")
        W = c.winfo_width()
        H = int(c.cget("height"))
        if W < 120:
            return
        L, R, T, B = 52, 10, 10, 22
        pw, ph = W - L - R, H - T - B
        N = HISTORY_SECONDS
        samples = list(self.thermal_log.samples)
        n = len(samples)

        def X(i):
            return L + pw * (1.0 - (n - 1 - i) / float(N - 1))

        def Y(level):
            return T + ph * (1.0 - level / 3.0)

        for lv, name in enumerate(("正常", "やや高い", "高い", "危険")):
            y = Y(lv)
            c.create_line(L, y, W - R, y, fill="#ececf0")
            c.create_text(L - 6, y, text=name, anchor="e", fill="#8e8e93", font=("", 8))
        c.create_text(L, H - B + 6, text="30分前", anchor="nw", fill="#8e8e93", font=("", 8))
        c.create_text(W - R, H - B + 6, text="今", anchor="ne", fill="#8e8e93", font=("", 8))
        if n < 2:
            return
        # 受信fps (薄いグレーの細線、副目盛り)
        max_fps = max(60.0, max(f for _, f in samples))
        cur = []
        for i, (lv, fps) in enumerate(samples + [(None, 0.0)]):
            if lv is None:
                if len(cur) >= 4:
                    c.create_line(*cur, fill="#c7c7cc", width=1)
                cur = []
            else:
                cur += [X(i), T + ph * (1.0 - min(fps, max_fps) / max_fps)]
        # 温度 (4段階の階段線)
        i = 0
        prev_end_level = None
        prev_end_i = None
        while i < n:
            lv = samples[i][0]
            if lv is None:
                i += 1
                prev_end_level = None
                continue
            j = i
            while j + 1 < n and samples[j + 1][0] == lv:
                j += 1
            color = THERMAL_COLORS[THERMAL_LEVELS[lv]]
            y = Y(lv)
            if prev_end_level is not None and prev_end_i == i - 1:
                c.create_line(X(i), Y(prev_end_level), X(i), y, fill=color, width=2)
            x1 = X(j) if X(j) > X(i) else X(i) + 1
            c.create_line(X(i), y, x1, y, fill=color, width=2)
            prev_end_level, prev_end_i = lv, j
            i = j + 1

    # -- 有効/無効 -----------------------------------------------------------
    @staticmethod
    def _set_enabled(widget, enabled):
        try:
            if enabled:
                widget.state(["!disabled"])
            else:
                widget.state(["disabled"])
        except Exception:
            pass

    def _update_enabled(self):
        s = self.settings
        self._set_enabled(self.bias_scale, s["exp_mode"] == "auto")
        manual_exp = s["exp_mode"] == "manual"
        self._set_enabled(self.iso_scale, manual_exp)
        self._set_enabled(self.shutter_cb, manual_exp)
        manual_wb = s["wb_mode"] == "manual"
        self._set_enabled(self.temp_scale, manual_wb)
        self._set_enabled(self.tint_scale, manual_wb)
        status = self.state.snapshot()["phone_status"]
        focus_ok = status.get("focusSupported", True)
        self._set_enabled(self.focus_mode_cb, focus_ok)
        self._set_enabled(self.focus_scale, focus_ok and s["focus_mode"] == "manual")
        self._set_enabled(self.torch_cb, status.get("torchAvailable", True))
        bg_on = s["bg_mode"] != "off"
        self._set_enabled(self.bg_strength_scale, bg_on)
        self._set_enabled(self.bg_quality_cb, bg_on)
        show_hint = s["bg_mode"] == "green"
        if show_hint != self.bg_hint_visible:
            self.bg_hint_visible = show_hint
            if show_hint:
                self.bg_hint.grid()
            else:
                self.bg_hint.grid_remove()

    # -- 接続 ---------------------------------------------------------------
    def toggle_connect(self):
        if not self.wanted:
            if self.settings["mode"] == "wifi" and not self.settings["ip"].strip():
                messagebox.showerror("PhoneCam", "iPhoneのIPアドレスを入力してください（iPhoneのアプリ画面に表示されています）")
                return
            need_pv = self.settings.get("backend") != "phonecam"
            if av is None or np is None or (need_pv and pyvirtualcam is None):
                messagebox.showerror("PhoneCam", "必要なライブラリ(av, numpy, pyvirtualcam)が見つかりません。install.bat を実行してください")
                return
            self.wanted = True
            self.connect_btn.config(text="切断")
            self.link.start()
        else:
            self.wanted = False
            self.connect_btn.config(text="接続")
            self.link.stop()
            self.stream.reset_user()

    # -- 状態表示のポーリング (メインスレッド) -----------------------------------
    def _poll(self):
        snap = self.state.snapshot()
        st = snap["phone_status"]
        self.status_var.set(snap["status_text"])
        self.fps_var.set("%.1f" % snap["recv_fps"] if snap["connected"] else "-")
        self.dec_var.set("%.1f ms" % snap["decode_ms"] if snap["connected"] else "-")
        self.res_var.set(snap["resolution"])
        self.error_var.set(snap["error_text"])

        if st:
            self._apply_ranges(st)
            parts = []
            if "iso" in st:
                parts.append("現在のISO %d" % round(st["iso"]))
            if st.get("exposureSeconds"):
                parts.append("シャッター 1/%d" % round(1.0 / st["exposureSeconds"]))
            self.cur_exp_var.set(" / ".join(parts))
            if "temperature" in st:
                self.cur_wb_var.set("現在の色温度 %dK / 色かぶり %d" % (round(st["temperature"]), round(st.get("tint", 0))))
            if "lensPosition" in st:
                self.cur_focus_var.set("現在の位置 %.2f" % st["lensPosition"])
        connected = snap["connected"]
        self._update_phone_status(st if connected else {})
        now = time.monotonic()
        if connected and not self._was_connected:
            self._screen_user_t = now  # 接続直後は送った設定が反映される前なので、iPhone側の値で上書きしない
        self._was_connected = connected
        self._update_v13(st if connected else {}, connected, snap, now)
        self.root.after(100, self._poll)

    def _update_v13(self, st, connected, snap, now):
        """省電力・録画・遅延・音声の表示と、温度履歴の記録 (1秒ごと)。"""
        # 温度履歴 (1秒ごとに記録、グラフは1秒に1回まで描き直す)
        if now - self._last_sample >= 1.0:
            self._last_sample = now
            self.thermal_log.sample(now, connected, st.get("thermalState"), snap["recv_fps"])
            self._graph_dirty = True
            self._housekeeping()
        hot = self.thermal_log.hot_seconds
        self.st_vars["hot"].set(format_mmss(hot) if connected else "-")
        if self._graph_dirty and now - getattr(self, "_graph_drawn", 0.0) >= 1.0:
            try:
                if self.nb.index("current") == STATUS_TAB_INDEX:
                    self._graph_dirty = False
                    self._graph_drawn = now
                    self._draw_graph()
            except Exception:
                pass

        # 遅延
        avg = self.link.latency.averages() if connected else {}
        for key, var in self.lat_vars.items():
            v = avg.get(key)
            var.set("%.0f ms" % v if isinstance(v, (int, float)) else "-")

        # 省電力
        if connected and "screenBlack" in st:
            black = bool(st.get("screenBlack"))
            if now - self._screen_user_t > 3.0 and black != self.settings["screen_black"]:
                self.settings["screen_black"] = black
                self.screen_black_var.set(black)
                self._schedule_save()
            auto = st.get("autoBlackSeconds")
            extra = "（自動: %s）" % self._value_to_label(SCREEN_AUTO_LABELS, auto) if auto in SCREEN_AUTO_VALUES else ""
            self.screen_state_var.set("現在のiPhoneの画面: %s%s" % ("黒" if black else "通常", extra))
        else:
            self.screen_state_var.set("")

        # 録画
        rec = st.get("recording")
        rec = rec if isinstance(rec, dict) else {}
        active = bool(rec.get("active"))
        if connected:
            if now >= self._rec_lock_until:
                self.rec_btn.state(["!disabled"])
            self.rec_btn.config(text="録画停止" if active else "録画開始")
        else:
            self.rec_btn.state(["disabled"])
            self.rec_btn.config(text="録画開始")
        if connected and rec:
            self.rec_vars["state"].set("● 録画中  %s" % format_mmss(rec.get("seconds", 0)) if active else "停止中")
            self.rec_labels["state"].config(foreground="#c0392b" if active else self._default_fg)
            self.rec_vars["file"].set(rec.get("fileName") or "-")
            size = rec.get("bytes")
            self.rec_vars["size"].set(format_bytes(size) if (active or size) else "-")
            free = rec.get("freeBytes")
            self.rec_vars["free"].set(format_bytes(free) if free else "-")
            self.rec_error_var.set(rec.get("error") or "")
        else:
            self.rec_vars["state"].set("-")
            for k in ("file", "size", "free"):
                self.rec_vars[k].set("-")
            self.rec_labels["state"].config(foreground=self._default_fg)
            self.rec_error_var.set("")

        # 音声の状態表示
        if AUDIO_AVAILABLE and self.settings["mic_enabled"]:
            mic = st.get("mic") if isinstance(st.get("mic"), dict) else {}
            if self.audio.error:
                self.audio_err_var.set(self.audio.error)
                self.audio_info_var.set("")
            elif connected and mic and mic.get("available") is False:
                self.audio_err_var.set("iPhoneのマイクが使えません。iPhoneの設定でPhoneCamのマイクを許可してください")
                self.audio_info_var.set("")
            else:
                self.audio_err_var.set("")
                info = "出力先: %s" % self.audio.device_name if self.audio.running else ""
                if self.audio.running and connected and mic.get("sampleRate"):
                    info += "　（iPhone %d Hz / 溜まり %d ms）" % (mic["sampleRate"], round(self.audio.buffered_ms()))
                self.audio_info_var.set(info)
        elif AUDIO_AVAILABLE:
            self.audio_err_var.set("")
            self.audio_info_var.set("")

    def _housekeeping(self):
        """1秒ごと: 音声ストリームが止まっていたら、3秒以上あけて開き直す。"""
        if AUDIO_AVAILABLE and self.settings["mic_enabled"] and not self.audio.running:
            if time.monotonic() - self._audio_retry_t >= 3.0:
                self._apply_audio()

    def _update_phone_status(self, st):
        thermal = st.get("thermalState")
        self.st_vars["thermal"].set(THERMAL_LABELS.get(thermal, "-") if thermal else "-")
        level = st.get("pressureLevel")
        self.st_vars["pressure"].set(PRESSURE_LABELS.get(level, "-") if level else "-")
        self.st_vars["factors"].set(format_pressure_factors(st.get("pressureFactors")) if level else "-")
        sfps = st.get("sentFps")
        self.st_vars["sentfps"].set("%.1f" % sfps if isinstance(sfps, (int, float)) else "-")
        cam_d, net_d = st.get("cameraDroppedFrames"), st.get("networkDroppedFrames")
        self.st_vars["dropped"].set(
            "カメラ %d / 通信 %d" % (cam_d, net_d)
            if isinstance(cam_d, (int, float)) and isinstance(net_d, (int, float)) else "-")
        enc = st.get("encodeMs")
        self.st_vars["encode"].set("%.1f ms" % enc if isinstance(enc, (int, float)) else "-")
        color = {"fair": "#b7791f", "serious": "#d9730d", "critical": "#c0392b"}.get(thermal, self._default_fg)
        try:
            self.st_labels["thermal"].config(
                foreground=color, font=("", 9, "bold") if thermal == "critical" else "TkDefaultFont")
        except Exception:
            pass
        if "batteryLevel" in st or "batteryState" in st:
            self.st_vars["battery"].set(format_battery(st.get("batteryLevel", -1), st.get("batteryState", "unknown")))
        else:
            self.st_vars["battery"].set("-")
        lp = st.get("lowPowerMode")
        self.st_vars["lowpower"].set("-" if lp is None else ("オン" if lp else "オフ"))
        pm = st.get("processingMs")
        self.st_vars["processing"].set("%.1f ms" % pm if isinstance(pm, (int, float)) else "-")

        warn = is_thermal_warning(thermal)
        if warn and not self.warn_var.get():
            self.warn_var.set(THERMAL_WARNING)
            self.warn_label.pack(side="bottom", fill="x", before=self.nb)
        elif not warn and self.warn_var.get():
            self.warn_var.set("")
            self.warn_label.pack_forget()

    def _apply_ranges(self, st):
        key = (st.get("isoMin"), st.get("isoMax"), st.get("zoomMax"), st.get("focusSupported"), st.get("torchAvailable"))
        if key == self._range_cache.get("k"):
            return
        self._range_cache["k"] = key
        try:
            if st.get("isoMin") is not None and st.get("isoMax") is not None and st["isoMax"] > st["isoMin"]:
                self.iso_scale.config(from_=st["isoMin"], to=st["isoMax"])
            if st.get("zoomMax") and st["zoomMax"] > 1.0:
                self.zoom_scale.config(from_=1.0, to=st["zoomMax"])
        except Exception:
            pass
        self._update_enabled()

    def on_close(self):
        self._closing = True
        try:
            if self.setup is not None:
                self.setup.close()
        except Exception:
            pass
        try:
            self.link.stop()
            self.link.vcam.close()
            self.watcher.reset()
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        save_settings(self.settings)
        self.root.destroy()


def webbrowser_open(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _bind_setup():
    """phonecam_setup へ、本体の関数を渡す (呼び出し時に本体側の最新の定義を参照する)。"""
    if phonecam_setup is None:
        return
    phonecam_setup.bind(
        is_frozen=lambda: _is_frozen(),
        registration_status=lambda: registration_status(),
        register_camera=lambda: helper_register(),
        run_elevated=lambda params, timeout_ms=ELEVATED_TIMEOUT_MS: run_elevated(params, timeout_ms),
        script_path=lambda: os.path.abspath(__file__),
    )


_bind_setup()


def main():
    helper = helper_main(sys.argv[1:])
    if helper is not None:
        return helper
    if tk is None:
        print("tkinter が使用できません。Python を公式インストーラーで再インストールしてください。")
        return 1
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


def _show_fatal(exc):
    """windowed の exe ではコンソールが無いので、起動時エラーをダイアログで表示する。"""
    import traceback
    detail = traceback.format_exc()
    try:
        r = tk.Tk() if tk is not None else None
        if r is not None:
            r.withdraw()
            messagebox.showerror("PhoneCam", "起動中にエラーが発生しました。\n\n%s" % detail[-1500:])
            r.destroy()
            return
    except Exception:
        pass
    try:
        sys.stderr.write(detail)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        _show_fatal(e)
        code = 1
    sys.exit(code)
