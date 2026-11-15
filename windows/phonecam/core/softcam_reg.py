# -*- coding: utf-8 -*-
"""専用仮想カメラ softcam の DLL 場所・登録/解除 (regsvr32)・--register/--unregister ヘルパ
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from phonecam.core.paths import _app_dir, _is_frozen
from phonecam.i18n import t


# 専用仮想カメラ (softcam)。CLSID は build-slots.ps1 と同一
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


CAMERA_SLOTS = (1, 2, 3, 4)


def camera_namespace(slot):
    if slot not in CAMERA_SLOTS:
        raise ValueError("Invalid camera slot")
    return "PhoneCam" if slot == 1 else f"PhoneCam{slot}"


def camera_display_name(slot):
    """OBS などの DirectShow 一覧に出る名前。softcam_ext/build-slots.ps1 の $display と同じ規則
    (スロット 1 は "PhoneCam"、2 以降は "PhoneCam 2"…)。画面に出す「OBS での名前」はすべてここから取る。"""
    camera_namespace(slot)
    return "PhoneCam" if slot == 1 else "PhoneCam %d" % slot


def camera_clsid(slot):
    camera_namespace(slot)
    return PHONECAM_CLSID if slot == 1 else "{BD7AEDAC-501F-4685-9B20-BC10F70BD1%02X}" % (0x43 + slot)


def _slot_dir(root, slot):
    camera_namespace(slot)
    return root if slot == 1 else os.path.join(root, str(slot))


def softcam_path(slot=1):
    return os.path.join(_slot_dir(_embedded_dir(), slot), SOFTCAM_DLL)


def softcam32_path(slot=1):
    return os.path.join(_slot_dir(_embedded_dir(), slot), SOFTCAM_DLL32)


def _file_hash12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


SOFTCAM_REV_FILE = "softcam.rev"


def softcam_rev(slot=1):
    """インストール先フォルダ名になる softcam の版 (12文字)。
    CI が softcam のソース (タグ・拡張パッチ・REVISION) から計算して exe に埋め込む。
    DLL は MSVC のリンク時刻などでビルドごとに数バイト変わるため、DLL のハッシュでは比べない
    (比べると、アプリを更新するたびに「更新が必要」になって管理者権限の再登録が要る)。
    softcam.rev が無い (ソース実行・古いビルド) ときだけ DLL のハッシュで代用する。"""
    try:
        with open(os.path.join(_embedded_dir(), SOFTCAM_REV_FILE), "r", encoding="ascii") as f:
            rev = f.read().strip().lower()
        if re.fullmatch(r"[0-9a-f]{12}", rev):
            return rev
    except Exception:
        pass
    return _file_hash12(softcam_path(slot))


def softcam_install_root():
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return os.path.join(base, "PhoneCam", "softcam")


def softcam_install_dir(hash12):
    return os.path.join(softcam_install_root(), hash12)


def softcam_installed_path(slot=1):
    """埋め込み DLL の現在のバージョン用のインストール先 DLL パス。埋め込みが無ければ None。"""
    try:
        return os.path.join(_slot_dir(softcam_install_dir(softcam_rev(slot)), slot), SOFTCAM_DLL) if os.path.isfile(softcam_path(slot)) else None
    except Exception:
        return None


def _norm_path(p):
    p = str(p).strip().strip('"').rstrip("\\/ ")
    return os.path.normcase(os.path.abspath(p))


def _same_path(a, b):
    return _norm_path(a) == _norm_path(b)


def softcam_registered_path(slot=1, view=0):
    """レジストリに登録されている PhoneCam カメラの DLL パス。未登録なら None。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID\\%s\\InprocServer32" % camera_clsid(slot),
                            0, winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if view == 32 else 0)) as k:
            return winreg.QueryValueEx(k, "")[0]
    except Exception:
        return None


VIDEO_INPUT_CATEGORY = "{860BB310-5D01-11d0-BD3B-00A0C911CE86}"


def directshow_friendly_name(slot=1, view=0):
    """OBS などが DirectShow の「ビデオ入力デバイス」として見る名前 (レジストリの FriendlyName)。
    HKCR\\CLSID\\{860BB310-...}\\Instance の下で、このスロットの CLSID を持つ項目を探す。無ければ None。"""
    try:
        import winreg
        clsid = camera_clsid(slot).lower()
        flags = winreg.KEY_READ | (winreg.KEY_WOW64_32KEY if view == 32 else 0)
        base = "CLSID\\%s\\Instance" % VIDEO_INPUT_CATEGORY
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, base, 0, flags) as root:
            index = 0
            while True:
                try:
                    key_name = winreg.EnumKey(root, index)
                except OSError:
                    return None
                index += 1
                try:
                    with winreg.OpenKey(root, key_name, 0, flags) as item:
                        try:
                            owner = str(winreg.QueryValueEx(item, "CLSID")[0]).lower()
                        except OSError:
                            owner = key_name.lower()
                        if owner == clsid or key_name.lower() == clsid:
                            return str(winreg.QueryValueEx(item, "FriendlyName")[0])
                except OSError:
                    continue
    except Exception:
        return None


def slot_report(slot=1):
    """OBS に出ないときの確認用: スロットの登録状態・CLSID・登録された DLL・DirectShow 上の名前 (64bit / 32bit)。"""
    try:
        status = registration_status(slot)
    except Exception:
        status = "unavailable"
    return {
        "slot": slot,
        "clsid": camera_clsid(slot),
        "status": status,
        "dll": {view: softcam_registered_path(slot, view) for view in (0, 32)},
        "name": {view: directshow_friendly_name(slot, view) for view in (0, 32)},
    }


_last_outdated_logged = {}


def _log_outdated(reg, expected, slot=1):
    """「更新が必要」と判定した理由 (登録先と期待するパス) を phonecam_error.log に1行残す。同じ内容は1回だけ。"""
    key = (reg, expected)
    if _last_outdated_logged.get(slot) == key:
        return
    _last_outdated_logged[slot] = key
    try:
        from phonecam.core import crashlog
        why = "same path but file missing" if _same_path(reg, expected) else "registered path differs"
        crashlog.log_error("softcam outdated", "softcam outdated: slot=%s %s registered=%r expected=%r rev=%s exists=%s" % (
            slot, why, reg, expected, softcam_rev(slot), os.path.isfile(expected)))
    except Exception:
        pass


def registration_status(slot=1):
    """exe 版での登録状態。"ok" / "missing"(未登録) / "outdated"(更新が必要) / "unavailable"(埋め込み DLL なし)。"""
    expected = softcam_installed_path(slot)
    if expected is None:
        return "unavailable"
    reg = softcam_registered_path(slot)
    if not reg:
        return "missing"
    if _same_path(reg, expected) and os.path.isfile(expected):
        return "ok"
    _log_outdated(reg, expected, slot)
    return "outdated"


def softcam_load_path(slot=1):
    """送る側が読み込む softcam.dll。登録済み(OK)ならその DLL、そうでなければ埋め込みの DLL。"""
    if _is_frozen():
        try:
            if registration_status(slot) == "ok":
                return softcam_installed_path(slot)
        except Exception:
            pass
    return softcam_path(slot)


def register_hint():
    """登録が必要なときの案内文。"""
    if _is_frozen():
        return t("core.reg.hint_exe")
    return t("core.reg.hint_src")


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
    """Preserve every revision still referenced by a registered filter."""
    root = softcam_install_root()
    retained = {keep_hash}
    for slot in CAMERA_SLOTS:
        for view in (0, 32):
            path = softcam_registered_path(slot, view)
            if path:
                try:
                    relative = os.path.relpath(path, root)
                except ValueError:  # a manually registered DLL can be on a different drive
                    continue
                retained.add(relative.split(os.sep)[0])
    if os.path.isdir(root):
        for name in os.listdir(root):
            if name not in retained and re.fullmatch(r"[0-9a-f]{12}", name):
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)


class RegisterResult:
    """--register の結果。x64 / x86 は regsvr32 まで成功したか (x86 は 32bit 版 DLL が同梱されていないとき None)。
    bool() は 64bit 側の成功 (これまでの True / False と同じ)。"""

    def __init__(self, x64=False, x86=None):
        self.x64, self.x86 = bool(x64), (None if x86 is None else bool(x86))

    def __bool__(self):
        return self.x64

    @property
    def partial(self):
        """64bit だけ登録できて、32bit が失敗した状態。"""
        return self.x64 and self.x86 is False

    def __repr__(self):
        return "RegisterResult(x64=%r, x86=%r)" % (self.x64, self.x86)


EXIT_REGISTER_PARTIAL = 3   # --register: 64bit だけ登録できた (32bit の登録に失敗) ときの終了コード


def _copy_verified(src, dst):
    """src を dst へコピーし、dst の中身が src と同じことを照合し直す。一致すれば True。
    使用中でコピーできなくても、すでに同じ内容なら True (別の内容が残っているときは False)。"""
    try:
        if os.path.isfile(dst) and _file_hash12(dst) == _file_hash12(src):
            return True  # 同じ内容なら上書きしない (使用中でも失敗しない)
        try:
            shutil.copyfile(src, dst)
        except OSError:
            pass
        return os.path.isfile(dst) and _file_hash12(dst) == _file_hash12(src)
    except OSError:
        return False


def helper_register(slot=1):
    """管理者権限で実行される --register。RegisterResult を返す (64bit / 32bit を別々に)。"""
    src = softcam_path(slot)
    h = softcam_rev(slot)
    dst_dir = _slot_dir(softcam_install_dir(h), slot)
    os.makedirs(dst_dir, exist_ok=True)
    x64_dst = os.path.join(dst_dir, SOFTCAM_DLL)
    has32 = os.path.isfile(softcam32_path(slot))
    x86_dst = os.path.join(dst_dir, SOFTCAM_DLL32)
    x64_copied = _copy_verified(src, x64_dst)
    x86_copied = _copy_verified(softcam32_path(slot), x86_dst) if has32 else None
    x64_exe, x86_exe = _regsvr32_paths()
    # 中身が一致しない DLL は登録しない (古い DLL を「登録できた」と見せない)
    x64 = x64_copied and _run_regsvr32(x64_exe, x64_dst)
    x86 = None
    if has32:
        x86 = bool(x86_copied) and _run_regsvr32(x86_exe, x86_dst)
    if x64:
        _cleanup_old_install_dirs(keep_hash=h)
    return RegisterResult(x64, x86)


def x86_registration_status(slot=1):
    """32bit 版の登録状態。"ok" / "missing" / "unavailable"(32bit 版 DLL を同梱していない)。"""
    try:
        if not os.path.isfile(softcam32_path(slot)):
            return "unavailable"
        installed = softcam_installed_path(slot)
        reg = softcam_registered_path(slot, 32)
        if not installed or not reg:
            return "missing"
        expected = os.path.join(os.path.dirname(installed), SOFTCAM_DLL32)
        return "ok" if _same_path(reg, expected) and os.path.isfile(expected) else "missing"
    except Exception:
        return "unavailable"


def helper_unregister(slot=1):
    """Unregister the selected filter in both registry views."""
    ok = True
    for executable, view in zip(_regsvr32_paths(), (0, 32)):
        registered = softcam_registered_path(slot, view)
        if not registered:
            continue
        dll = registered
        if not os.path.isfile(dll):
            installed = softcam_installed_path(slot)
            dll = (os.path.join(os.path.dirname(installed), SOFTCAM_DLL32)
                   if installed and view == 32 else installed)
        if not dll or not os.path.isfile(dll) or not _run_regsvr32(executable, dll, unregister=True):
            ok = False
    _cleanup_old_install_dirs()
    return ok


def _error_box(text):
    """管理者ヘルパ (--register / --setup など) の失敗を知らせる。Qt を初期化しないことがあるので、Win32 の MessageBoxW を直接呼ぶ。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), "PhoneCam", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _setup_module():
    """phonecam_setup (4000 行超) は --setup / ウィザードのときだけ読む。"""
    from phonecam.core import deps
    return deps.phonecam_setup


def bind_setup_host(link_connected=None):
    """phonecam_setup (ウィザード/管理者ジョブ) に本体のホスト関数を渡す。
    どの入口から呼んでも同じ関数が bind される (管理者ヘルパ --setup も register_camera を使う)。"""
    phonecam_setup = _setup_module()
    if phonecam_setup is None:
        return
    from phonecam.core.elevate import ELEVATED_TIMEOUT_MS, run_elevated
    from phonecam.core.paths import WINDOWS_DIR
    fns = dict(
        is_frozen=lambda: _is_frozen(),
        registration_status=lambda: registration_status(),
        register_camera=lambda: helper_register(),
        run_elevated=lambda params, timeout_ms=ELEVATED_TIMEOUT_MS: run_elevated(params, timeout_ms),
        script_path=lambda: os.path.join(WINDOWS_DIR, "phonecam_qt.py"),
    )
    if link_connected is not None:
        fns["link_connected"] = link_connected
    phonecam_setup.bind(**fns)


def helper_main(argv):
    """--register / --unregister が渡されたときの処理 (GUI なし)。それ以外なら None。"""
    args = list(argv)
    if "--setup" in args:
        # 管理者権限で実行されるセットアップ (job.json の内容を順に実行して result.json に書く)
        try:
            job = args[args.index("--setup") + 1]
            sha = args[args.index("--sha256") + 1] if "--sha256" in args else None
            phonecam_setup = _setup_module()
            if phonecam_setup is None:
                return 1
            bind_setup_host()  # register_camera などのホスト関数 
            return phonecam_setup.run_job(job, expected_sha=sha)
        except Exception as e:
            _error_box(t("core.reg.setup_failed", error=e))
            return 1
    if "--register" in args:
        action, fn, msg = "register", helper_register, t("core.reg.register_failed")
    elif "--unregister" in args:
        action, fn, msg = "unregister", helper_unregister, t("core.reg.unregister_failed")
    else:
        return None
    try:
        slot = int(args[args.index("--camera-slot") + 1]) if "--camera-slot" in args else 1
        if slot not in CAMERA_SLOTS:
            return 1
        ok = fn(slot)
    except Exception as e:
        _error_box(t("core.reg.details", message=msg, error=e))
        return 1
    if not ok:
        _error_box(msg)
        return 1
    if getattr(ok, "partial", False):
        return EXIT_REGISTER_PARTIAL
    return 0
