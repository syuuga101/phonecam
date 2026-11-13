# -*- coding: utf-8 -*-
"""専用仮想カメラ softcam の DLL 場所・登録/解除 (regsvr32)・--register/--unregister ヘルパ
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from phonecam.core.deps import phonecam_setup
from phonecam.core.paths import _app_dir, _is_frozen
from phonecam.core.deps import messagebox, tk


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


SOFTCAM_REV_FILE = "softcam.rev"


def softcam_rev():
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
    return _file_hash12(softcam_path())


def softcam_install_root():
    base = os.environ.get("ProgramData") or r"C:\ProgramData"
    return os.path.join(base, "PhoneCam", "softcam")


def softcam_install_dir(hash12):
    return os.path.join(softcam_install_root(), hash12)


def softcam_installed_path():
    """埋め込み DLL の現在のバージョン用のインストール先 DLL パス。埋め込みが無ければ None。"""
    try:
        return os.path.join(softcam_install_dir(softcam_rev()), SOFTCAM_DLL)
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
    h = softcam_rev()
    dst_dir = softcam_install_dir(h)
    os.makedirs(dst_dir, exist_ok=True)
    pairs = [(src, os.path.join(dst_dir, SOFTCAM_DLL))]
    if os.path.isfile(softcam32_path()):
        pairs.append((softcam32_path(), os.path.join(dst_dir, SOFTCAM_DLL32)))
    for s, d in pairs:
        if os.path.isfile(d) and _file_hash12(d) == _file_hash12(s):
            continue  # 同じ内容なら上書きしない (使用中でも失敗しない)
        try:
            shutil.copyfile(s, d)
        except OSError:
            if not os.path.isfile(d):
                raise  # 同じ版のフォルダに使用中の DLL がある場合は、それをそのまま使う
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


def bind_setup_host(link_connected=None):
    """phonecam_setup (ウィザード/管理者ジョブ) に本体のホスト関数を渡す。
    Tk 版・Qt 版のどの入口から呼んでも同じ関数が bind される (管理者ヘルパ --setup も register_camera を使う)。"""
    if phonecam_setup is None:
        return
    from phonecam.core.elevate import ELEVATED_TIMEOUT_MS, run_elevated
    from phonecam.core.paths import WINDOWS_DIR
    fns = dict(
        is_frozen=lambda: _is_frozen(),
        registration_status=lambda: registration_status(),
        register_camera=lambda: helper_register(),
        run_elevated=lambda params, timeout_ms=ELEVATED_TIMEOUT_MS: run_elevated(params, timeout_ms),
        script_path=lambda: os.path.join(WINDOWS_DIR, "phonecam_receiver.py"),
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
            if phonecam_setup is None:
                return 1
            bind_setup_host()  # register_camera などのホスト関数 (Qt 入口では ui.app が読まれない)
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
