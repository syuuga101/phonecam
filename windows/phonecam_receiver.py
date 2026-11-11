# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ

iPhone (PhoneCam アプリ) から H.264 映像を受信してデコードし、
仮想カメラ (OBS Virtual Camera / Unity Capture) へ出力します。
"""

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

MSG_VIDEO = 1
MSG_STATUS = 3
MSG_CONTROL = 10

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phonecam_settings.json")


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
    "backend": "obs",
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
    return s


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

class VirtualCamOutput:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self.cam = None
        self.size = None
        self.fail_until = 0.0
        self.reset_requested = False

    def close(self):
        if self.cam is not None:
            try:
                self.cam.close()
            except Exception:
                pass
        self.cam = None
        self.size = None

    def request_reset(self):
        self.reset_requested = True

    def send(self, img, fps):
        if self.reset_requested:
            self.reset_requested = False
            self.close()
            self.fail_until = 0.0
        h, w = img.shape[0], img.shape[1]
        if self.cam is not None and self.size != (w, h):
            self.close()
        if self.cam is None:
            if pyvirtualcam is None:
                self.state.update(error_text="pyvirtualcam がインストールされていません。install.bat を実行してください")
                return
            now = time.time()
            if now < self.fail_until:
                return
            backend = "unitycapture" if self.settings.get("backend") == "unitycapture" else "obs"
            try:
                self.cam = pyvirtualcam.Camera(
                    w, h, max(1, int(fps)), fmt=pyvirtualcam.PixelFormat.RGB, backend=backend
                )
                self.size = (w, h)
                self.state.update(error_text="")
            except Exception as e:
                self.cam = None
                self.fail_until = now + 3.0
                if backend == "obs":
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

def make_decoder():
    ctx = av.CodecContext.create("h264", "r")
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
        decoder = make_decoder()
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
            if msg_type == MSG_VIDEO:
                t0 = time.perf_counter()
                try:
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
                img = frame.to_ndarray(format="rgb24")
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
BACKEND_LABELS = [("obs", "OBS Virtual Camera"), ("unitycapture", "Unity Video Capture")]
THROTTLE_MS = 50  # 1コントロールあたり最大 20 msg/s


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
        root.minsize(560, 600)
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
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        self._build_connection(body)
        self._build_video(body)
        self._build_exposure(body)
        self._build_wb(body)
        self._build_focus(body)
        self._build_zoom(body)

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

    # -- 接続 ---------------------------------------------------------------
    def toggle_connect(self):
        if not self.wanted:
            if self.settings["mode"] == "wifi" and not self.settings["ip"].strip():
                messagebox.showerror("PhoneCam", "iPhoneのIPアドレスを入力してください（iPhoneのアプリ画面に表示されています）")
                return
            if av is None or np is None or pyvirtualcam is None:
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
        self.root.after(100, self._poll)

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


if __name__ == "__main__":
    sys.exit(main())
