# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ

iPhone (PhoneCam アプリ) から H.264 / HEVC 映像を受信してデコードし、
仮想カメラ (PhoneCam 専用カメラ / OBS Virtual Camera / Unity Capture) へ出力します。
"""

import ctypes
import json
import os
import plistlib
import socket
import struct
import sys
import threading
import time

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
MSG_CONTROL = 10

def _app_dir():
    """設定ファイルを置くフォルダ。exe 化されているときは exe と同じ場所。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(_app_dir(), "phonecam_settings.json")

# 専用仮想カメラ (softcam)。CLSID は .github/workflows/windows.yml でパッチして埋め込むものと同一
SOFTCAM_DLL = "softcam.dll"
PHONECAM_CLSID = "{BD7AEDAC-501F-4685-9B20-BC10F70BD144}"
# softcam へ渡す画像の行順。True = 上から下 (softcam 側が DIB 用に反転する)。逆さまに映るなら False にする
SOFTCAM_TOP_DOWN = True


def softcam_path():
    return os.path.join(_app_dir(), SOFTCAM_DLL)


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
    "rotate180": False,
    "orientation": "auto",
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
}

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4K": (3840, 2160)}

BACKENDS = ("phonecam", "obs", "unitycapture")
CODECS = ("h264", "hevc")
BG_MODES = ("off", "blur", "green")
BG_QUALITIES = ("fast", "balanced")
# (キー, 最小, 最大) — 範囲外の値は丸める
NUMERIC_RANGES = {
    "bg_strength": (0.0, 1.0),
    "brightness": (-0.5, 0.5),
    "contrast": (0.5, 1.5),
    "saturation": (0.0, 2.0),
    "sharpness": (0.0, 1.0),
}
COLOR_DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 0.0}


def sanitize_settings(s):
    """読み込んだ設定の新規項目を検証し、不正値は既定値へ戻す (古い設定ファイルとの互換用)。"""
    for key, allowed in (("backend", BACKENDS), ("codec", CODECS),
                         ("bg_mode", BG_MODES), ("bg_quality", BG_QUALITIES)):
        if s.get(key) not in allowed:
            s[key] = DEFAULT_SETTINGS[key]
    for key, (lo, hi) in NUMERIC_RANGES.items():
        try:
            v = float(s[key])
        except Exception:
            v = DEFAULT_SETTINGS[key]
        s[key] = min(hi, max(lo, v))
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


def save_settings(settings, path=SETTINGS_PATH):
    try:
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
    return c


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
# 仮想カメラ出力
# ---------------------------------------------------------------------------

def softcam_registered_path():
    """レジストリに登録されている PhoneCam カメラの DLL パス。未登録なら None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID\\%s\\InprocServer32" % PHONECAM_CLSID) as k:
            return winreg.QueryValueEx(k, "")[0]
    except Exception:
        return None


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


class VirtualCamOutput:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self.cam = None
        self.size = None
        self.fail_until = 0.0
        self.reset_requested = False
        self._softcam = None
        self._cam_kind = None  # 現在開いているバックエンド

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
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
        self.cam = None
        self.size = None
        self._cam_kind = None

    def request_reset(self):
        self.reset_requested = True

    # -- 出力 -----------------------------------------------------------
    def _open_softcam(self, w, h):
        path = softcam_path()
        if not os.path.isfile(path):
            raise PhoneCamError(
                "softcam.dll が見つかりません。配布版(PhoneCamReceiverフォルダ)を使い、"
                "register.bat を実行してください。OBS Virtual Camera など他の出力先も選べます")
        if self._softcam is None:
            try:
                self._softcam = SoftcamSender(path)
            except OSError as e:
                raise PhoneCamError("softcam.dll を読み込めません: %s" % e)
        sender = self._softcam
        sender.close()
        sender.create(w, h)
        reg = softcam_registered_path()
        if reg is None:
            self.state.update(error_text="PhoneCamカメラが未登録です。register.bat を実行してください（管理者権限が必要です）")
        elif os.path.normcase(os.path.abspath(reg)) != os.path.normcase(os.path.abspath(path)):
            self.state.update(error_text="PhoneCamカメラの登録先が別のフォルダです。フォルダを移動した場合は register.bat をもう一度実行してください")
        else:
            self.state.update(error_text="")
        return sender

    def send(self, img, fps):
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
        if self.cam is not None and (self.size != (w, h) or self._cam_kind != backend):
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
                except PhoneCamError as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    self.state.update(error_text=str(e))
                    return
                except Exception as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    self.state.update(error_text="PhoneCamカメラを開始できません。register.bat を実行済みか確認してください。詳細: %s" % e)
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
    def __init__(self, settings, state, get_mode_ip):
        self.settings = settings
        self.state = state
        self.get_mode_ip = get_mode_ip
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
        self.vcam.close()
        self.state.update(status_text="停止中", connected=False, recv_fps=0.0, decode_ms=0.0, resolution="-")

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
        self.vcam.close()

    def _session(self, sock, stop):
        if av is None or np is None:
            raise PhoneCamError("av / numpy がインストールされていません。install.bat を実行してください")
        decoders = DecoderManager(make_decoder)
        self.state.update(status_text="接続済み", connected=True, error_text="")

        # 保存済み設定を全部送る + キーフレーム要求
        full = build_control(self.settings, "all")
        full["keyframe"] = True
        with self._send_lock:
            sock.sendall(encode_msg(MSG_CONTROL, json.dumps(full).encode("utf-8")))

        last_kf = 0.0
        frame_count = 0
        fps_t0 = time.time()
        decode_avg = 0.0

        while not stop.is_set():
            msg_type, payload = read_msg(sock, stop)
            codec_name = codec_for_msg(msg_type)
            if codec_name is not None:
                t0 = time.perf_counter()
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
                img = frame.to_ndarray(format=self.vcam.frame_format())
                if self.settings.get("mirror"):
                    img = img[:, ::-1]
                if self.settings.get("rotate180"):
                    img = img[::-1, ::-1]
                img = np.ascontiguousarray(img)
                fps_val = self.state.phone_status.get("fps", 60) or 60
                self.vcam.send(img, fps_val)
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


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

LENS_LABELS = [("wide", "広角"), ("ultrawide", "超広角")]
RES_LABELS = ["720p", "1080p", "4K"]
FPS_LABELS = [30, 60]
MODE3_LABELS = [("auto", "自動"), ("locked", "固定"), ("manual", "マニュアル")]
SHUTTER_VALUES = [30, 50, 60, 100, 120, 250, 500, 1000]
ORIENT_LABELS = [("auto", "自動"), ("landscape", "横"), ("portrait", "縦")]
BACKEND_LABELS = [("phonecam", "PhoneCam（専用カメラ）"), ("obs", "OBS Virtual Camera"), ("unitycapture", "Unity Capture")]
CODEC_LABELS = [("h264", "H.264"), ("hevc", "HEVC(H.265)")]
BG_MODE_LABELS = [("off", "オフ"), ("blur", "ぼかし"), ("green", "切り抜き(緑)")]
BG_QUALITY_LABELS = [("fast", "速度優先"), ("balanced", "バランス")]
THERMAL_LABELS = {"nominal": "正常", "fair": "やや高い", "serious": "高い", "critical": "危険"}
BATTERY_STATE_LABELS = {"charging": "充電中", "full": "満充電", "unplugged": "放電中", "unknown": "不明"}
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
        self.settings = load_settings()
        self.state = SharedState()
        self.link = Link(self.settings, self.state, self._get_mode_ip)
        self.wanted = False
        self._pending = {}
        self._save_job = None
        self.focus_widgets = []
        self.manual_widgets = {}
        self._range_cache = {}

        root.title("PhoneCam 受信")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.minsize(560, 560)
        root.geometry("620x700")
        self._build_ui()
        self._update_enabled()
        self._poll()

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
        self._build_video(p1)
        p2 = self._page("画質調整")
        self._build_exposure(p2)
        self._build_wb(p2)
        self._build_focus(p2)
        self._build_zoom(p2)
        p3 = self._page("エフェクト")
        self._build_background(p3)
        self._build_color(p3)
        p4 = self._page("状態")
        self._build_phone_status(p4)

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

    def _build_video(self, parent):
        f = self._section(parent, "映像")
        ttk.Label(f, text="レンズ").grid(row=0, column=0, sticky="w", pady=2)
        self.lens_var = tk.StringVar(value=self._value_to_label(LENS_LABELS, self.settings["lens"]))
        cb = ttk.Combobox(f, textvariable=self.lens_var, values=[l for _, l in LENS_LABELS], state="readonly", width=14)
        cb.grid(row=0, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("lens", self._label_to_value(LENS_LABELS, self.lens_var.get()), "video"))
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

        self.mirror_var = tk.BooleanVar(value=bool(self.settings["mirror"]))
        ttk.Checkbutton(f, text="左右反転", variable=self.mirror_var,
                        command=lambda: self._changed("mirror", bool(self.mirror_var.get()), None)).grid(row=4, column=0, columnspan=2, sticky="w")
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

        ttk.Label(f, text="コーデック").grid(row=8, column=0, sticky="w", pady=2)
        self.codec_var = tk.StringVar(value=self._value_to_label(CODEC_LABELS, self.settings["codec"]))
        cb = ttk.Combobox(f, textvariable=self.codec_var, values=[l for _, l in CODEC_LABELS], state="readonly", width=14)
        cb.grid(row=8, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._changed("codec", self._label_to_value(CODEC_LABELS, self.codec_var.get()), "codec"))
        self._hint(f, 9, "HEVC は同じ画質をより低いビットレートで送れます（Wi-Fi向き）")
        self._hint(f, 10, "出力先「PhoneCam（専用カメラ）」は OBS や Discord のカメラ一覧に「PhoneCam」と表示されます。"
                          "OBSの仮想カメラと同時に使えます（初回は register.bat が必要）")

    def _on_backend(self):
        self._changed("backend", self._label_to_value(BACKEND_LABELS, self.backend_var.get()), None)
        self.link.vcam.request_reset()

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
        for row, (key, name) in enumerate([("thermal", "温度"), ("battery", "バッテリー"),
                                           ("lowpower", "低電力モード"), ("processing", "エフェクト処理時間")]):
            ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value="-")
            lbl = ttk.Label(f, textvariable=var)
            lbl.grid(row=row, column=1, sticky="w", padx=6)
            self.st_vars[key] = var
            self.st_labels[key] = lbl
        self._hint(f, 4, "接続中のiPhoneから約1秒ごとに更新されます", pady=(6, 0))

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
        self._update_phone_status(st if snap["connected"] else {})
        self.root.after(100, self._poll)

    def _update_phone_status(self, st):
        thermal = st.get("thermalState")
        self.st_vars["thermal"].set(THERMAL_LABELS.get(thermal, "-") if thermal else "-")
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
        try:
            self.link.stop()
        except Exception:
            pass
        save_settings(self.settings)
        self.root.destroy()


def main():
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
