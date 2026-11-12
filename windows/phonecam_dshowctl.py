"""PhoneCam: DirectShow フィルタ (softcam 拡張) のカメラ設定ダイアログとの橋渡し。

OBS の「ビデオの設定」で開くシステムのカメラ設定ダイアログ (映像プロシージャ アンプ / カメラ制御) を
スライダーで動かすと、フィルタ DLL が共有メモリ "PhoneCam/Controls" に要求を書く。この module はそれを読んで
受信アプリの制御 JSON に変換し (poll)、iPhone の現在値・範囲をダイアログへ書き戻す (publish)。

共有メモリのレイアウトは windows/softcam_ext/PhoneCamControls.h と同じ (リトルエンディアン, 4096 バイト):
  ヘッダ 64B: u32 magic 'PCCT' | version=1 | request_seq | ack_seq | state_seq | slot_count=8 | slot_size=64 | reserved
  スロット i (offset 64+64*i): i32 min,max,step,default,value,flags,caps,supported | u32 changed_seq | i32 reserved[7]
  flags/caps: Auto=1, Manual=2。スロット: 0明るさ 1コントラスト 2彩度 3シャープネス 4ホワイトバランス 5ズーム 6フォーカス 7露出
Windows 以外では open() が False を返し、他のメソッドは何もしない。
"""
import math
import struct
import sys
import threading

MAGIC = 0x54434350  # 'PCCT'
VERSION = 1
BLOCK_SIZE = 4096
HEADER_SIZE = 64
SLOT_SIZE = 64
SLOT_COUNT = 8
MAP_NAME = "PhoneCam/Controls"
MUTEX_NAME = "PhoneCam/ControlsMutex"

AUTO = 1
MANUAL = 2

(S_BRIGHTNESS, S_CONTRAST, S_SATURATION, S_SHARPNESS,
 S_WHITEBALANCE, S_ZOOM, S_FOCUS, S_EXPOSURE) = range(8)

_HDR = struct.Struct("<8I")          # 先頭32バイト (残りはパディング)
_SLOT = struct.Struct("<8iI7i")      # 64バイト
assert _SLOT.size == SLOT_SIZE

# (min, max, step, default, value, flags, caps, supported) — C++ 側 FillDefaults と同じ
DEFAULTS = [
    (-50, 50, 1, 0, 0, MANUAL, MANUAL, 1),
    (50, 150, 1, 100, 100, MANUAL, MANUAL, 1),
    (0, 200, 1, 100, 100, MANUAL, MANUAL, 1),
    (0, 100, 1, 0, 0, MANUAL, MANUAL, 1),
    (2500, 8000, 50, 5000, 5000, AUTO, AUTO | MANUAL, 1),
    (100, 1000, 1, 100, 100, MANUAL, MANUAL, 1),
    (0, 100, 1, 50, 50, AUTO, AUTO | MANUAL, 1),
    (-13, -1, 1, -7, -7, AUTO, AUTO | MANUAL, 1),
]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def shutter_to_exposure(shutter):
    """シャッター 1/N 秒 → DirectShow の露出値 (log2 秒, -13..-1)。"""
    try:
        return int(_clamp(round(-math.log2(max(1.0, float(shutter)))), -13, -1))
    except (TypeError, ValueError):
        return -7


def exposure_to_shutter(value, snap=None):
    """DirectShow の露出値 (log2 秒) → シャッター 1/N の N。snap にリストを渡すと一番近い値にする。"""
    n = 2.0 ** (-int(value))
    if snap:
        return int(min(snap, key=lambda s: abs(math.log2(s) - math.log2(n))))
    return int(round(n))


# ---- メモリ背景 (Windows の共有メモリ / テスト用 bytearray) ----
class _BytesBackend:
    """テスト用: bytearray をそのまま使う。"""

    def __init__(self, size=BLOCK_SIZE):
        self.buf = bytearray(size)

    def lock(self):
        return True

    def unlock(self):
        pass

    def read(self, off, n):
        return bytes(self.buf[off:off + n])

    def write(self, off, data):
        self.buf[off:off + len(data)] = data

    def close(self):
        pass


class _WinBackend:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k = k
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateFileMappingW.restype = wintypes.HANDLE
        k.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        a.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        a.SetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_void_p, wintypes.BOOL]

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]

        sd = ctypes.create_string_buffer(64)  # SECURITY_DESCRIPTOR は 40 バイト以下
        sa = None
        if a.InitializeSecurityDescriptor(sd, 1) and a.SetSecurityDescriptorDacl(sd, True, None, False):
            sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), ctypes.cast(sd, ctypes.c_void_p), False)
        psa = ctypes.byref(sa) if sa is not None else None
        self.mutex = k.CreateMutexW(psa, False, MUTEX_NAME)
        if not self.mutex:
            raise OSError("CreateMutexW failed")
        INVALID = wintypes.HANDLE(-1)
        self.map = k.CreateFileMappingW(INVALID, psa, 0x04, 0, BLOCK_SIZE, MAP_NAME)  # PAGE_READWRITE
        if not self.map:
            k.CloseHandle(self.mutex)
            raise OSError("CreateFileMappingW failed")
        self.view = k.MapViewOfFile(self.map, 0xF001F, 0, 0, BLOCK_SIZE)  # FILE_MAP_ALL_ACCESS
        if not self.view:
            k.CloseHandle(self.map)
            k.CloseHandle(self.mutex)
            raise OSError("MapViewOfFile failed")
        self._held = False

    def lock(self):
        r = self.k.WaitForSingleObject(self.mutex, 500)
        self._held = r in (0, 0x80)  # WAIT_OBJECT_0 / WAIT_ABANDONED
        return self._held

    def unlock(self):
        if self._held:
            self.k.ReleaseMutex(self.mutex)
            self._held = False

    def read(self, off, n):
        return self.ct.string_at(self.view + off, n)

    def write(self, off, data):
        self.ct.memmove(self.view + off, bytes(data), len(data))

    def close(self):
        try:
            self.k.UnmapViewOfFile(self.view)
            self.k.CloseHandle(self.map)
            self.k.CloseHandle(self.mutex)
        except Exception:
            pass


class ControlBridge:
    def __init__(self, backend=None, shutter_snap=None):
        """shutter_snap: シャッター値の候補 (例 SHUTTER_VALUES)。指定すると露出の値を一番近い候補にする。"""
        self._backend = backend
        self._own_backend = backend is None
        self._snap = list(shutter_snap) if shutter_snap else None
        self._lock = threading.Lock()
        self._opened = backend is not None
        self._last_pub = None

    # ---- 開閉 ----
    def open(self):
        with self._lock:
            if self._opened:
                return True
            if not sys.platform.startswith("win"):
                return False
            try:
                self._backend = _WinBackend()
            except Exception:
                self._backend = None
                return False
            self._opened = True
        return self._with_block(lambda ctx: True) is not False

    def close(self):
        with self._lock:
            if self._backend is not None and self._own_backend:
                self._backend.close()
            if self._own_backend:
                self._backend = None
                self._opened = False

    @property
    def is_open(self):
        return self._opened and self._backend is not None

    # ---- 低レベル ----
    def _with_block(self, fn):
        """ミューテックスを取り、ブロックが未初期化なら初期化してから fn(read_hdr, write_hdr, slots, write_slot) を呼ぶ。"""
        be = self._backend
        if be is None:
            return False
        if not be.lock():
            return False
        try:
            hdr = list(_HDR.unpack(be.read(0, _HDR.size)))
            if hdr[0] == 0:
                be.write(0, bytes(BLOCK_SIZE))
                hdr = [MAGIC, VERSION, 0, 0, 0, SLOT_COUNT, SLOT_SIZE, 0]
                be.write(0, _HDR.pack(*hdr))
                for i, d in enumerate(DEFAULTS):
                    be.write(HEADER_SIZE + SLOT_SIZE * i, _SLOT.pack(*d, 0, *([0] * 7)))
            if hdr[0] != MAGIC or hdr[1] != VERSION or hdr[5] != SLOT_COUNT or hdr[6] != SLOT_SIZE:
                return False
            slots = []
            for i in range(SLOT_COUNT):
                f = _SLOT.unpack(be.read(HEADER_SIZE + SLOT_SIZE * i, SLOT_SIZE))
                slots.append(list(f[:9]))  # min,max,step,def,value,flags,caps,supported,changed_seq
            ctx = {"hdr": hdr, "slots": slots}
            result = fn(ctx)

            def put_slot(i):
                s = slots[i]
                be.write(HEADER_SIZE + SLOT_SIZE * i, _SLOT.pack(*s, *([0] * 7)))
            if ctx.get("dirty_slots"):
                for i in ctx["dirty_slots"]:
                    put_slot(i)
            if ctx.get("dirty_hdr"):
                be.write(0, _HDR.pack(*hdr))
            return result
        finally:
            be.unlock()

    # ---- ダイアログ → 受信アプリ ----
    def poll_changes(self):
        """ダイアログでの変更を [{'key','value','group','control'}] で返す (設定キー名 / 値 / グループ / 制御JSON)。
        変更が無ければ []。取り出した変更は確認済み (ack_seq) になる。"""
        if not self.is_open:
            return []

        def run(ctx):
            hdr, slots = ctx["hdr"], ctx["slots"]
            if hdr[2] == hdr[3]:  # request_seq == ack_seq
                return []
            ack = hdr[3]
            pending = sorted(((s[8], i) for i, s in enumerate(slots) if _newer(s[8], ack)))
            hdr[3] = hdr[2]
            ctx["dirty_hdr"] = True
            out = []
            for _seq, i in pending:
                out.extend(self._slot_to_changes(i, slots[i]))
            return out

        r = self._with_block(run)
        return r if isinstance(r, list) else []

    def poll(self):
        """poll_changes() の制御 JSON だけ。例: [{'color': {'brightness': 0.1}}, {'zoom': 2.0}]"""
        return [c["control"] for c in self.poll_changes() if not c.get("redundant")]

    def _slot_to_changes(self, i, s):
        lo, hi, value, flags = s[0], s[1], s[4], s[5]
        auto = flags == AUTO
        value = _clamp(value, lo, hi)

        def ch(key, val, group, control):
            return {"key": key, "value": val, "group": group, "control": control}

        def chr_(key, val, group, control):  # 同じスロットの 2 つ目の設定キー (制御 JSON は 1 つ目に含まれる)
            d = ch(key, val, group, control)
            d["redundant"] = True
            return d

        if i == S_BRIGHTNESS:
            v = round(_clamp(value / 100.0, -0.5, 0.5), 4)
            return [ch("brightness", v, "color", {"color": {"brightness": v}})]
        if i == S_CONTRAST:
            v = round(_clamp(value / 100.0, 0.5, 1.5), 4)
            return [ch("contrast", v, "color", {"color": {"contrast": v}})]
        if i == S_SATURATION:
            v = round(_clamp(value / 100.0, 0.0, 2.0), 4)
            return [ch("saturation", v, "color", {"color": {"saturation": v}})]
        if i == S_SHARPNESS:
            v = round(_clamp(value / 100.0, 0.0, 1.0), 4)
            return [ch("sharpness", v, "color", {"color": {"sharpness": v}})]
        if i == S_WHITEBALANCE:
            if auto:
                return [ch("wb_mode", "auto", "wb", {"wb": {"mode": "auto"}})]
            return [ch("wb_mode", "manual", "wb", {"wb": {"mode": "manual", "temperature": int(value)}}),
                    chr_("temperature", int(value), "wb", {"wb": {"temperature": int(value)}})]
        if i == S_ZOOM:
            v = round(max(1.0, value / 100.0), 2)
            return [ch("zoom", v, "zoom", {"zoom": v})]
        if i == S_FOCUS:
            if auto:
                return [ch("focus_mode", "auto", "focus", {"focus": {"mode": "auto"}})]
            v = round(_clamp(value / 100.0, 0.0, 1.0), 3)
            return [ch("focus_mode", "manual", "focus", {"focus": {"mode": "manual", "position": v}}),
                    chr_("focus_pos", v, "focus", {"focus": {"position": v}})]
        if i == S_EXPOSURE:
            if auto:
                return [ch("exp_mode", "auto", "exposure", {"exposure": {"mode": "auto"}})]
            n = exposure_to_shutter(value, self._snap)
            return [ch("exp_mode", "manual", "exposure", {"exposure": {"mode": "manual", "shutter": n}}),
                    chr_("shutter", n, "exposure", {"exposure": {"shutter": n}})]
        return []

    # ---- 受信アプリ → ダイアログ ----
    def publish(self, status, settings):
        """iPhone の状態 (phone_status) と PC 側の設定 (settings) を共有メモリへ書き、ダイアログの値・範囲に反映する。
        未取り込みの要求があるスロットの値は上書きしない。値が変わらなければ書き込みを省く。"""
        if not self.is_open:
            return False
        status = status or {}
        settings = settings or {}
        desired = self._desired_slots(status, settings)
        key = repr(desired)
        if key == self._last_pub:
            return True

        def run(ctx):
            hdr, slots = ctx["hdr"], ctx["slots"]
            changed = False
            dirty = []
            for i, want in enumerate(desired):
                s = slots[i]
                pending = _newer(s[8], hdr[3])
                new = list(s)
                new[0:4] = want[0:4]       # min,max,step,default
                new[6], new[7] = want[6], want[7]   # caps, supported
                if not pending:
                    new[4], new[5] = want[4], want[5]
                if new != s:
                    slots[i] = new
                    dirty.append(i)
                    changed = True
            if changed:
                hdr[4] = (hdr[4] + 1) & 0xFFFFFFFF
                ctx["dirty_hdr"] = True
                ctx["dirty_slots"] = dirty
            return True

        ok = self._with_block(run)
        if ok:
            self._last_pub = key
        return bool(ok)

    @staticmethod
    def _desired_slots(status, settings):
        def g(d, k, default=None):
            v = d.get(k, default)
            return default if v is None else v

        def num(v, default):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        out = []
        # 色補正 (PC側の設定)
        out.append((-50, 50, 1, 0, int(_clamp(round(num(g(settings, "brightness", 0.0), 0.0) * 100), -50, 50)), MANUAL, MANUAL, 1))
        out.append((50, 150, 1, 100, int(_clamp(round(num(g(settings, "contrast", 1.0), 1.0) * 100), 50, 150)), MANUAL, MANUAL, 1))
        out.append((0, 200, 1, 100, int(_clamp(round(num(g(settings, "saturation", 1.0), 1.0) * 100), 0, 200)), MANUAL, MANUAL, 1))
        out.append((0, 100, 1, 0, int(_clamp(round(num(g(settings, "sharpness", 0.0), 0.0) * 100), 0, 100)), MANUAL, MANUAL, 1))
        # ホワイトバランス
        wb_auto = g(settings, "wb_mode", "auto") != "manual"
        temp = num(g(settings, "temperature", 5000), 5000.0)
        if wb_auto and status.get("temperature"):
            temp = num(status.get("temperature"), temp)
        out.append((2500, 8000, 50, 5000, int(_clamp(round(temp), 2500, 8000)), AUTO if wb_auto else MANUAL, AUTO | MANUAL, 1))
        # ズーム (iPhone の zoomMax まで)
        zmax = num(status.get("zoomMax"), 10.0)
        zmax = _clamp(zmax if zmax > 1.0 else 10.0, 1.0, 10.0)
        zmax_i = max(100, int(math.floor(zmax * 100)))
        zoom = int(_clamp(round(num(g(settings, "zoom", 1.0), 1.0) * 100), 100, zmax_i))
        out.append((100, zmax_i, 1, 100, zoom, MANUAL, MANUAL, 1))
        # フォーカス
        f_ok = bool(status.get("focusSupported", True))
        f_auto = g(settings, "focus_mode", "auto") != "manual"
        fpos = num(g(settings, "focus_pos", 0.5), 0.5)
        if f_auto and status.get("lensPosition") is not None:
            fpos = num(status.get("lensPosition"), fpos)
        out.append((0, 100, 1, 50, int(_clamp(round(fpos * 100), 0, 100)), AUTO if f_auto else MANUAL, AUTO | MANUAL, 1 if f_ok else 0))
        # 露出 (log2 秒)
        e_auto = g(settings, "exp_mode", "auto") != "manual"
        if e_auto and num(status.get("exposureSeconds"), 0.0) > 0:
            ev = int(_clamp(round(math.log2(num(status.get("exposureSeconds"), 0.01))), -13, -1))
        else:
            ev = shutter_to_exposure(g(settings, "shutter", 100))
        out.append((-13, -1, 1, -7, ev, AUTO if e_auto else MANUAL, AUTO | MANUAL, 1))
        return out


def _newer(seq, ack):
    """seq > ack (u32 のラップアラウンド考慮)。seq==0 は未変更。"""
    if seq == 0:
        return False
    return ((seq - ack) & 0xFFFFFFFF) != 0 and ((seq - ack) & 0xFFFFFFFF) < 0x80000000
