# -*- coding: utf-8 -*-
"""softcam の共有メモリ読み取り・使用中判定 (ConsumerWatcher)
"""

import ctypes
import struct
import threading
import time


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


def parse_softcam_header(raw):
    """共有メモリ先頭 16 byte → {"width","height","active","ver","hb"}。不正なら None。"""
    if raw is None or len(raw) < SHMEM_HEADER_BYTES:
        return None
    _off, w, h, _fr, active, ver, sender_hb, hb = struct.unpack("<IHHfBBBB", bytes(raw[:SHMEM_HEADER_BYTES]))
    return {"width": w, "height": h, "active": bool(active), "ver": ver, "hb": hb, "shb": sender_hb}


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
    """softcam の共有メモリから receiver_heartbeat などを読む。カメラが無いときは None。
    UI スレッドが読み、受信スレッドが (カメラの作り直しで) 閉じることがあるので、ロックで守る。"""

    def __init__(self, opener=Win32Mapping, name=SOFTCAM_SHMEM_NAME):
        self._opener = opener
        self._name = name
        self._m = None
        self._lock = threading.Lock()
        self._hold_until = 0.0

    def read_info(self):
        """ヘッダの内容 (parse_softcam_header) か、共有メモリが無い/読めないときは None。"""
        with self._lock:
            if self._m is None:
                if time.monotonic() < self._hold_until:
                    return None  # カメラの作り直し中: 古い共有メモリを掴み直さない
                try:
                    self._m = self._opener(self._name)
                except Exception:
                    self._m = None
                    return None
            try:
                return parse_softcam_header(self._m.read())
            except Exception:
                self._close_locked()
                return None

    def read(self):
        info = self.read_info()
        return None if info is None else info["hb"]

    def _close_locked(self):
        m, self._m = self._m, None
        if m is not None:
            try:
                m.close()
            except Exception:
                pass

    def close(self, hold=0.0):
        """閉じる。hold 秒のあいだは開き直さない。
        (こちらが古い共有メモリを開いたままだと、softcam が同じ名前で新しいカメラを作れないため)"""
        with self._lock:
            self._close_locked()
            if hold > 0:
                self._hold_until = time.monotonic() + hold


WATCH_STATE_LABELS = {"in_use": "使用中", "idle": "未使用", "nomem": "共有メモリなし"}


class ConsumerWatcher:
    """仮想カメラを開いているアプリがあるか。
    主: receiver_heartbeat が最近変化した (8bit で一周するので、前回値との不一致だけを見る)。
    補助: connected_min_version が 0 → 0以外 になった (アプリが新しく開いた) ことも「使われた」とみなす。"""

    def __init__(self, reader, window=CONSUMER_WINDOW_SEC):
        self.reader = reader
        self.window = window
        self.info = None
        self._last = None
        self._last_ver = None
        self._changed_t = None

    def reset(self):
        """カメラを作り直したときに呼ぶ (古い共有メモリを掴み続けないため)。"""
        self.reader.close()
        self.info = None
        self._last = None
        self._last_ver = None
        self._changed_t = None

    def _read_info(self):
        fn = getattr(self.reader, "read_info", None)
        if fn is not None:
            return fn()
        hb = self.reader.read()  # read() だけを持つ読み取り側 (テスト用など)
        return None if hb is None else {"hb": hb, "ver": None, "width": 0, "height": 0, "active": True}

    def poll(self, now):
        info = self._read_info()
        self.info = info
        if info is None:
            self._last = None
            self._last_ver = None
            return
        hb, ver = info["hb"], info.get("ver")
        if self._last is not None and hb != self._last:
            self._changed_t = now
        elif self._last_ver == 0 and ver:
            self._changed_t = now
        self._last = hb
        self._last_ver = ver

    def in_use(self, now):
        return self._changed_t is not None and (now - self._changed_t) <= self.window

    def state(self, now):
        """"in_use" / "idle" / "nomem" (診断表示用)。"""
        if self.info is None:
            return "nomem"
        return "in_use" if self.in_use(now) else "idle"

    def detail(self):
        """診断表示の補足 (共有メモリのサイズと、アプリが一度でも開いたか)。"""
        i = self.info
        if not i:
            return ""
        parts = ["共有メモリ %dx%d" % (i.get("width", 0), i.get("height", 0))]
        if i.get("ver") is not None:
            parts.append("アプリの接続履歴あり" if i["ver"] else "アプリの接続履歴なし")
            # 原因調査用の生の値 (受信hbが動いていれば、アプリがカメラを開いている)
            parts.append("ver=%s 送信hb=%s 受信hb=%s" % (i.get("ver"), i.get("shb", "-"), i.get("hb")))
        return "、".join(parts)
