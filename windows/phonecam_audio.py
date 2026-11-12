# -*- coding: utf-8 -*-
"""VB-CABLE を入れたときに「既定の音の出力先／入力元」が CABLE に切り替わってしまうのを防ぐ。

- インストール前に、現在の既定デバイス (再生・録音 × 3つの役割) を設定に控えておく
- インストール後 (約20秒) と、次の2回の起動時に、既定が VB-Audio CABLE になっていたら元に戻す
- 何も控えていないときは、既定デバイスを一切変更しない

Core Audio (IMMDeviceEnumerator) は ctypes で直接呼びます (comtypes は不要)。
既定の変更には、広く使われている IPolicyConfig (SoundSwitch / AudioDeviceCmdlets と同じ) を使います。
この処理は管理者ヘルパーではなく、通常権限の本体プロセスで (ユーザーごとの設定として) 行います。
"""

import ctypes
import os
import threading
import time
import uuid

FLOWS = ("render", "capture")
ROLES = ("console", "multimedia", "communications")
FLOW_INDEX = {"render": 0, "capture": 1}   # EDataFlow
ROLE_INDEX = {"console": 0, "multimedia": 1, "communications": 2}  # ERole
CABLE_WORDS = ("cable input", "cable output", "vb-audio")

SETTING_BACKUP = "audio_defaults_backup"
SETTING_PENDING = "vbcable_restore_pending"
PENDING_STARTUPS = 2   # インストール後、あと何回の起動で確認するか
POLL_SECONDS = 20
POLL_INTERVAL = 1.0

FAIL_MESSAGE = ("音の出力先を自動で戻せませんでした。タスクバーのスピーカーのアイコンをクリックして、"
                "出力先を元のデバイス（スピーカーやヘッドホン）に戻してください。")


def is_cable_name(name):
    low = (name or "").lower()
    return any(w in low for w in CABLE_WORDS)


def restored_message(names):
    names = [n for n in names if n]
    label = "」「".join(dict.fromkeys(names)) if names else "元のデバイス"
    return "VB-CABLEを入れると音の出力先が切り替わることがあるため、元の「%s」に戻しました" % label


# ---------------------------------------------------------------------------
# 判断ロジック (Windows に依存しない。API は差し替え可能)
# ---------------------------------------------------------------------------

def empty_defaults():
    return {f: {r: None for r in ROLES} for f in FLOWS}


def make_backup(api):
    """現在の既定を {render:{role:id}, capture:{...}, names:{id:name}} にする。取得できなければ None。"""
    cur = api.get_defaults()
    backup = {f: {r: (cur.get(f, {}).get(r) or None) for r in ROLES} for f in FLOWS}
    ids = {i for f in FLOWS for i in backup[f].values() if i}
    if not ids:
        return None
    backup["names"] = {}
    for i in ids:
        try:
            backup["names"][i] = api.device_info(i).get("name") or ""
        except Exception:
            backup["names"][i] = ""
    return backup


def valid_backup(b):
    if not isinstance(b, dict):
        return False
    for f in FLOWS:
        d = b.get(f)
        if not isinstance(d, dict):
            return False
        for r in ROLES:
            v = d.get(r)
            if v is not None and not isinstance(v, str):
                return False
    return any(isinstance(b[f].get(r), str) and b[f][r] for f in FLOWS for r in ROLES)


def plan_restore(backup, current, info_fn):
    """戻すべき (flow, role, 元のID) の一覧。
    戻すのは、いまの既定が VB-Audio CABLE のデバイスで、控えたデバイスがまだ有効なときだけ
    (利用者が自分で別のデバイスに変えた場合は触らない)。名前が取れないときは、控えと違うなら戻す。"""
    plan = []
    for f in FLOWS:
        for r in ROLES:
            want = (backup.get(f) or {}).get(r)
            have = (current.get(f) or {}).get(r)
            if not want or have == want:
                continue
            try:
                want_info = info_fn(want)
            except Exception:
                want_info = {}
            if not (want_info.get("exists") and want_info.get("active")):
                continue
            have_info = {}
            if have:
                try:
                    have_info = info_fn(have)
                except Exception:
                    have_info = {}
            name = have_info.get("name")
            if name:
                if not is_cable_name(name):
                    continue
            elif have is None:
                continue  # 既定が無い状態は触らない
            plan.append((f, r, want))
    return plan


def restore_pass(api, backup):
    """1回分の確認と復元。{"restored": [(flow, role, id)], "failed": [(flow, role, id)]}。"""
    out = {"restored": [], "failed": []}
    if not valid_backup(backup):
        return out
    current = api.get_defaults()
    for f, r, dev in plan_restore(backup, current, api.device_info):
        try:
            ok = bool(api.set_default(f, r, dev))
        except Exception:
            ok = False
        (out["restored"] if ok else out["failed"]).append((f, r, dev))
    return out


def summarize(results, backup):
    """poll の結果を画面用の文面にする: {"restored": bool, "failed": bool, "message": str}。"""
    names = backup.get("names", {}) if isinstance(backup, dict) else {}
    done = [x for x in results["restored"]]
    failed = [x for x in results["failed"]]
    # 失敗した後に成功した同じ役割は失敗に数えない
    failed = [x for x in failed if (x[0], x[1]) not in {(a, b) for a, b, _ in done}]
    if failed:
        return {"restored": bool(done), "failed": True, "message": FAIL_MESSAGE}
    if done:
        return {"restored": True, "failed": False,
                "message": restored_message([names.get(dev, "") for _, _, dev in done if
                                             not is_cable_name(names.get(dev, ""))])}
    return {"restored": False, "failed": False, "message": ""}


def poll(api, backup, seconds=POLL_SECONDS, interval=POLL_INTERVAL, sleep=time.sleep, clock=time.monotonic):
    """seconds の間 interval ごとに restore_pass を行い、結果をまとめて返す。"""
    total = {"restored": [], "failed": []}
    end = clock() + seconds
    while True:
        try:
            r = restore_pass(api, backup)
        except Exception:
            r = {"restored": [], "failed": []}
        total["restored"] += r["restored"]
        total["failed"] += r["failed"]
        if clock() >= end:
            break
        sleep(interval)
    return total


class RestoreGuard:
    """設定 (get/set) と音声 API をつなぐ。before_install → (インストール) → after_install / startup_check。"""

    def __init__(self, get_setting, set_setting, api_factory=None, sleep=time.sleep, clock=time.monotonic):
        self.get = get_setting
        self.set = set_setting
        self.api_factory = api_factory or (lambda: WinAudioApi() if os.name == "nt" else None)
        self.sleep = sleep
        self.clock = clock

    def pending(self):
        try:
            return int(self.get(SETTING_PENDING) or 0)
        except Exception:
            return 0

    def before_install(self):
        """VB-CABLE をインストールする直前に呼ぶ。控えられたら True (何も控えられなければ以後一切触らない)。"""
        api = None
        try:
            api = self.api_factory()
            if api is None:
                return False
            backup = make_backup(api)
        except Exception:
            backup = None
        finally:
            _close(api)
        if not backup:
            return False
        self.set(SETTING_BACKUP, backup)
        self.set(SETTING_PENDING, PENDING_STARTUPS)
        return True

    def _run(self, seconds):
        backup = self.get(SETTING_BACKUP)
        if not valid_backup(backup):
            return {"restored": False, "failed": False, "message": ""}
        api = None
        try:
            api = self.api_factory()
            if api is None:
                return {"restored": False, "failed": False, "message": ""}
            res = poll(api, backup, seconds=seconds, sleep=self.sleep, clock=self.clock)
        except Exception:
            return {"restored": False, "failed": True, "message": FAIL_MESSAGE}
        finally:
            _close(api)
        return summarize(res, backup)

    def after_install(self):
        """インストールのあと約20秒、既定を見張って戻す (ワーカースレッドで呼ぶ)。"""
        if not valid_backup(self.get(SETTING_BACKUP)):
            return {"restored": False, "failed": False, "message": ""}
        return self._run(POLL_SECONDS)

    def startup_check(self):
        """起動時 (残りがあるときだけ)。残り回数を1つ減らし、0 になったら控えを消す。
        戻り値は None (何もしない) か、after_install と同じ結果。ワーカースレッドで呼ぶ。"""
        n = self.pending()
        if n <= 0:
            return None
        if not valid_backup(self.get(SETTING_BACKUP)):
            self.set(SETTING_PENDING, 0)
            return None
        self.set(SETTING_PENDING, n - 1)
        result = self._run(POLL_SECONDS)
        if n - 1 <= 0:
            self.set(SETTING_BACKUP, None)
        return result


def _close(api):
    try:
        if api is not None and hasattr(api, "close"):
            api.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core Audio (ctypes)。Windows 専用。このスレッドで CoInitialize し、close() で解放する
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    u = uuid.UUID(text)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
CLSID_PolicyConfigClient = "870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"
IID_IPolicyConfig = "F8679F50-850A-41CF-9C72-430F290290C8"
PKEY_FRIENDLYNAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)  # PKEY_Device_FriendlyName
CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 2
DEVICE_STATE_ACTIVE = 1
VT_LPWSTR = 31

# vtable の位置 (IUnknown の3つ = 0..2 を含む)
IDX_RELEASE = 2
IDX_ENUM_GETDEFAULT = 4      # IMMDeviceEnumerator::GetDefaultAudioEndpoint
IDX_ENUM_GETDEVICE = 5       # IMMDeviceEnumerator::GetDevice
IDX_DEV_OPENPROPS = 4        # IMMDevice::OpenPropertyStore
IDX_DEV_GETID = 5            # IMMDevice::GetId
IDX_DEV_GETSTATE = 6         # IMMDevice::GetState
IDX_PROPS_GETVALUE = 5       # IPropertyStore::GetValue
IDX_POLICY_SETDEFAULT = 13   # IPolicyConfig::SetDefaultEndpoint (GetMixFormat=3 ... SetPropertyValue=12)


def _functype():
    return getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


def _method(ptr, index, *argtypes):
    """COM インターフェース ptr (整数) の vtable[index] を、(this, *args) -> HRESULT の関数として返す。"""
    vtbl = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.c_void_p))[0]
    addr = ctypes.cast(ctypes.c_void_p(vtbl), ctypes.POINTER(ctypes.c_void_p))[index]
    return _functype()(ctypes.c_long, ctypes.c_void_p, *argtypes)(addr)


class WinAudioApi:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows only")
        self._ole = ctypes.windll.ole32
        self._ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._ole.CoInitializeEx.restype = ctypes.c_long
        self._ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self._ole.CoCreateInstance.restype = ctypes.c_long
        self._ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self._ole.PropVariantClear.argtypes = [ctypes.c_void_p]
        hr = self._ole.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        self._uninit = hr >= 0  # RPC_E_CHANGED_MODE (負) のときは、すでに初期化済みなので解放しない
        self._enum = None
        try:
            self._enum = self._create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
        except Exception:
            self.close()
            raise

    def _create(self, clsid, iid):
        p = ctypes.c_void_p()
        hr = self._ole.CoCreateInstance(ctypes.byref(_guid(clsid)), None, CLSCTX_ALL, ctypes.byref(_guid(iid)),
                                        ctypes.byref(p))
        if hr < 0 or not p.value:
            raise OSError("CoCreateInstance failed (0x%08X)" % (hr & 0xFFFFFFFF))
        return p.value

    @staticmethod
    def _release(ptr):
        if ptr:
            try:
                _method(ptr, IDX_RELEASE)(ptr)
            except Exception:
                pass

    def close(self):
        self._release(self._enum)
        self._enum = None
        if self._uninit:
            self._uninit = False
            try:
                self._ole.CoUninitialize()
            except Exception:
                pass

    def _default_id(self, flow, role):
        dev = ctypes.c_void_p()
        hr = _method(self._enum, IDX_ENUM_GETDEFAULT, ctypes.c_uint, ctypes.c_uint,
                     ctypes.POINTER(ctypes.c_void_p))(self._enum, FLOW_INDEX[flow], ROLE_INDEX[role], ctypes.byref(dev))
        if hr < 0 or not dev.value:
            return None
        try:
            return self._device_id(dev.value)
        finally:
            self._release(dev.value)

    def _device_id(self, dev):
        idp = ctypes.c_void_p()
        hr = _method(dev, IDX_DEV_GETID, ctypes.POINTER(ctypes.c_void_p))(dev, ctypes.byref(idp))
        if hr < 0 or not idp.value:
            return None
        try:
            return ctypes.wstring_at(idp.value)
        finally:
            self._ole.CoTaskMemFree(idp)

    def _get_device(self, dev_id):
        dev = ctypes.c_void_p()
        hr = _method(self._enum, IDX_ENUM_GETDEVICE, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p))(
            self._enum, dev_id, ctypes.byref(dev))
        return dev.value if hr >= 0 and dev.value else None

    def get_defaults(self):
        return {f: {r: self._default_id(f, r) for r in ROLES} for f in FLOWS}

    def device_info(self, dev_id):
        """{"exists", "active", "name"}。存在しなければ exists=False。"""
        info = {"exists": False, "active": False, "name": ""}
        dev = self._get_device(dev_id)
        if not dev:
            return info
        try:
            info["exists"] = True
            state = ctypes.c_uint(0)
            if _method(dev, IDX_DEV_GETSTATE, ctypes.POINTER(ctypes.c_uint))(dev, ctypes.byref(state)) >= 0:
                info["active"] = state.value == DEVICE_STATE_ACTIVE
            info["name"] = self._friendly_name(dev)
        finally:
            self._release(dev)
        return info

    def _friendly_name(self, dev):
        store = ctypes.c_void_p()
        hr = _method(dev, IDX_DEV_OPENPROPS, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            dev, 0, ctypes.byref(store))  # STGM_READ
        if hr < 0 or not store.value:
            return ""
        try:
            key = _PROPERTYKEY()
            key.fmtid = _guid(PKEY_FRIENDLYNAME[0])
            key.pid = PKEY_FRIENDLYNAME[1]
            pv = _PROPVARIANT()
            hr = _method(store.value, IDX_PROPS_GETVALUE, ctypes.POINTER(_PROPERTYKEY),
                         ctypes.POINTER(_PROPVARIANT))(store.value, ctypes.byref(key), ctypes.byref(pv))
            if hr < 0:
                return ""
            try:
                if pv.vt == VT_LPWSTR and pv.p:
                    return ctypes.wstring_at(pv.p)
                return ""
            finally:
                self._ole.PropVariantClear(ctypes.byref(pv))
        finally:
            self._release(store.value)

    def set_default(self, flow, role, dev_id):
        try:
            pc = self._create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
        except Exception:
            return False
        try:
            hr = _method(pc, IDX_POLICY_SETDEFAULT, ctypes.c_wchar_p, ctypes.c_uint)(pc, dev_id, ROLE_INDEX[role])
            return hr >= 0
        finally:
            self._release(pc)


def run_in_thread(fn, callback=None):
    """fn() を別スレッドで実行する (COM を使うので専用スレッドで)。結果を callback(result) へ。"""
    def work():
        try:
            res = fn()
        except Exception:
            res = None
        if callback:
            callback(res)
    t = threading.Thread(target=work, name="audio-restore", daemon=True)
    t.start()
    return t
