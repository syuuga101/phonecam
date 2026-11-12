# -*- coding: utf-8 -*-
"""PhoneCam セットアップ

必要なソフト (Apple USBドライバ / iCloud / AltServer / VB-CABLE / VC++ ランタイム / PhoneCam カメラ) の
検出・ダウンロード・インストールと、初回起動のセットアップウィザードを提供します。

受信アプリ本体 (phonecam_receiver.py) とは bind() で関数を受け渡して連携します
(本体を __main__ として実行しても二重 import にならないようにするため)。
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import queue
import webbrowser
import urllib.error
import urllib.request

try:
    from phonecam_version import REPO as APP_REPO  # ビルド時に生成される ("owner/name")
except Exception:
    APP_REPO = ""

try:
    from phonecam_version import VERSION as APP_VERSION
except Exception:
    APP_VERSION = "dev"

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None


# ---------------------------------------------------------------------------
# 本体との連携
# ---------------------------------------------------------------------------

_host = {}


def bind(**fns):
    """本体から関数を受け取る: is_frozen / registration_status / register_camera / run_elevated / script_path"""
    _host.update(fns)


def _call(name, *args, **kw):
    fn = _host.get(name)
    if fn is None:
        raise RuntimeError("setup host function not bound: %s" % name)
    return fn(*args, **kw)


def _frozen():
    fn = _host.get("is_frozen")
    return bool(fn()) if fn else bool(getattr(sys, "frozen", False))


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATE_OK = "ok"            # 導入済み
STATE_MISSING = "missing"  # 未導入
STATE_WARN = "warn"        # 要確認

APPLE_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service"
USBMUX_ADDR = ("127.0.0.1", 27015)
VCREDIST_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
CABLE_KEYWORD = "CABLE Input"
FAQ_URL = "https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows"

# 追加の再起動コード
EXIT_REBOOT = (3010, 1641)
JOB_TIMEOUT_MS = 20 * 60 * 1000

# 表示順 (「必要なソフトの確認」の並び)
COMPONENTS = [
    {"id": "camera", "name": "PhoneCamカメラ", "purpose": "OBSやDiscordに表示される専用カメラ", "required": True},
    {"id": "vcredist", "name": "Visual C++ ランタイム", "purpose": "専用カメラの動作に必要", "required": True},
    {"id": "itunes", "name": "Apple USBドライバ（iTunes）", "purpose": "USBケーブルでの接続に必要（Wi-Fiだけなら不要）", "required": True},
    {"id": "icloud", "name": "iCloud", "purpose": "AltServerの動作に必要", "required": False},
    {"id": "altserver", "name": "AltServer", "purpose": "iPhoneにアプリを入れて自動更新する", "required": False, "default_on": True},
    {"id": "vbcable", "name": "VB-CABLE", "purpose": "iPhoneのマイクをOBSで使う（任意）", "required": False},
]
COMPONENT_BY_ID = {c["id"]: c for c in COMPONENTS}
# インストールする順番
INSTALL_ORDER = ["vcredist", "itunes", "icloud", "altserver", "vbcable", "camera"]

SOURCES = {
    "vcredist": {
        "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe", "file": "vc_redist.x64.exe", "kind": "exe",
        "args": ["/install", "/quiet", "/norestart"], "ok_codes": [0, 1638, 3010, 1641],
        "winget": "Microsoft.VCRedist.2015+.x64",
        "page": "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist",
    },
    "itunes": {
        "url": "https://www.apple.com/itunes/download/win64", "file": "iTunes64Setup.exe", "kind": "exe",
        "args": ["/quiet", "/norestart"], "ok_codes": [0, 3010, 1641],
        "winget": "Apple.iTunes",
        "page": "https://www.apple.com/itunes/",
    },
    "icloud": {
        "url": "https://updates.cdn-apple.com/2020/windows/001-39935-20200911-1A70AA56-F448-11EA-8CC0-99D41950005E/iCloudSetup.exe",
        "file": "iCloudSetup.exe", "kind": "exe",
        "args": ["/quiet", "/norestart"], "ok_codes": [0, 3010, 1641],
        "page": FAQ_URL,
    },
    "altserver": {
        "url": "https://cdn.altstore.io/file/altstore/altinstaller.zip", "file": "altinstaller.zip", "kind": "zip",
        "page": "https://altstore.io/",
    },
    "vbcable": {
        "url": "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip", "file": "VBCABLE_Driver_Pack46.zip",
        "kind": "zip", "page": "https://vb-audio.com/Cable/",
    },
}

MANUAL_TEXT = {
    "vcredist": "公式サイトから「vc_redist.x64.exe」をダウンロードして実行してください。",
    "itunes": "公式サイトの「Windows版iTunes（Microsoft Store版ではない方）」をダウンロードして実行してください。",
    "icloud": "AltStoreの案内ページにある「iCloud（Apple公式サイト版）」のリンクからインストールしてください。",
    "altserver": "公式サイトから「AltServer for Windows」をダウンロードし、zipを展開して Setup.exe を実行してください。",
    "vbcable": "公式サイトから zip をダウンロードして展開し、VBCABLE_Setup_x64.exe を管理者として実行してください。",
}

DEFAULT_MIN_BYTES = 100 * 1024
SIGNATURES = {"exe": (b"MZ",), "zip": (b"PK\x03\x04",), "msi": (b"\xd0\xcf\x11\xe0",)}
_CREATE_NO_WINDOW = 0x08000000


def setup_dir():
    return os.path.join(tempfile.gettempdir(), "PhoneCamSetup")


def ipa_url(repo=None):
    repo = APP_REPO if repo is None else repo
    if not repo or "/" not in repo:
        return None
    return "https://github.com/%s/releases/latest/download/PhoneCam.ipa" % repo


# ---------------------------------------------------------------------------
# 検出
# ---------------------------------------------------------------------------

def _winreg():
    try:
        import winreg
        return winreg
    except Exception:
        return None


def reg_key_exists(path, view=64):
    wr = _winreg()
    if wr is None:
        return False
    flag = wr.KEY_WOW64_64KEY if view == 64 else wr.KEY_WOW64_32KEY
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, path, 0, wr.KEY_READ | flag):
            return True
    except Exception:
        return False


def reg_value(path, name, view=64):
    wr = _winreg()
    if wr is None:
        return None
    flag = wr.KEY_WOW64_64KEY if view == 64 else wr.KEY_WOW64_32KEY
    try:
        with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, path, 0, wr.KEY_READ | flag) as k:
            return wr.QueryValueEx(k, name)[0]
    except Exception:
        return None


def uninstall_entries():
    """アンインストール情報 (HKLM の 64bit / 32bit 両方) から (表示名, バージョン) を列挙する。"""
    wr = _winreg()
    out = []
    if wr is None:
        return out
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    for flag in (wr.KEY_WOW64_64KEY, wr.KEY_WOW64_32KEY):
        try:
            with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, base, 0, wr.KEY_READ | flag) as root:
                i = 0
                while True:
                    try:
                        sub = wr.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with wr.OpenKey(root, sub) as k:
                            try:
                                name = wr.QueryValueEx(k, "DisplayName")[0]
                            except OSError:
                                continue
                            try:
                                ver = wr.QueryValueEx(k, "DisplayVersion")[0]
                            except OSError:
                                ver = ""
                            out.append((str(name), str(ver)))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def tcp_open(addr=USBMUX_ADDR, timeout=0.4):
    try:
        with socket.create_connection(addr, timeout=timeout):
            return True
    except Exception:
        return False


def _program_dirs():
    out = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(key)
        if v and v not in out:
            out.append(v)
    return out or [r"C:\Program Files"]


def _any_file(rel_paths):
    for base in _program_dirs():
        for rel in rel_paths:
            p = os.path.join(base, rel)
            if os.path.isfile(p):
                return p
    return None


def _has_entry(entries, *needles, exact=False):
    for name, _ in entries:
        low = name.strip().lower()
        for n in needles:
            if (low == n) if exact else (n in low):
                return True
    return False


def find_altserver():
    return _any_file([os.path.join("AltServer", "AltServer.exe")])


def audio_device_names():
    try:
        import sounddevice as sd
        return [str(d.get("name", "")) for d in sd.query_devices()]
    except Exception:
        return []


def detect_camera():
    if not _frozen():
        return {"state": STATE_WARN, "detail": "ソース実行ではregister.batを使ってください"}
    st = _call("registration_status")
    if st == "ok":
        return {"state": STATE_OK, "detail": ""}
    if st == "outdated":
        return {"state": STATE_MISSING, "detail": "更新が必要です"}
    if st == "unavailable":
        return {"state": STATE_WARN, "detail": "このexeにはカメラが含まれていません"}
    return {"state": STATE_MISSING, "detail": ""}


def detect_vcredist():
    if reg_value(VCREDIST_KEY, "Installed") == 1:
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_itunes(entries=None):
    entries = uninstall_entries() if entries is None else entries
    service = reg_key_exists(APPLE_SERVICE_KEY) or tcp_open()
    nonstore = _has_entry(entries, "itunes", exact=True) or bool(_any_file([os.path.join("iTunes", "iTunes.exe")]))
    if service and nonstore:
        return {"state": STATE_OK, "detail": ""}
    if service:
        return {"state": STATE_WARN, "detail": "USB接続はOK・AltServerには公式サイト版が必要"}
    return {"state": STATE_MISSING, "detail": ""}


def detect_icloud(entries=None):
    entries = uninstall_entries() if entries is None else entries
    if _has_entry(entries, "icloud", exact=True) or _any_file([os.path.join("Common Files", "Apple", "Internet Services", "iCloud.exe")]):
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_altserver(entries=None):
    entries = uninstall_entries() if entries is None else entries
    if _has_entry(entries, "altserver") or find_altserver():
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_vbcable(entries=None, devices=None):
    entries = uninstall_entries() if entries is None else entries
    if _has_entry(entries, "vb-audio virtual cable", "vb-cable"):
        return {"state": STATE_OK, "detail": ""}
    names = audio_device_names() if devices is None else devices
    if any(CABLE_KEYWORD.lower() in n.lower() for n in names):
        return {"state": STATE_OK, "detail": ""}
    return {"state": STATE_MISSING, "detail": ""}


def detect_all():
    """全コンポーネントの状態: {id: {"state", "detail"}}。個別の失敗は「要確認」にして続ける。"""
    try:
        entries = uninstall_entries()
    except Exception:
        entries = []
    table = {
        "camera": detect_camera,
        "vcredist": detect_vcredist,
        "itunes": lambda: detect_itunes(entries),
        "icloud": lambda: detect_icloud(entries),
        "altserver": lambda: detect_altserver(entries),
        "vbcable": lambda: detect_vbcable(entries),
    }
    out = {}
    for cid, fn in table.items():
        try:
            out[cid] = fn()
        except Exception as e:
            out[cid] = {"state": STATE_WARN, "detail": "確認できませんでした (%s)" % e}
    return out


# ---------------------------------------------------------------------------
# ダウンロード
# ---------------------------------------------------------------------------

class DownloadError(Exception):
    pass


def friendly_net_error(e):
    if isinstance(e, urllib.error.HTTPError):
        return "ダウンロード元が応答しませんでした (HTTP %s)" % e.code
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "ダウンロードがタイムアウトしました。ネットワークを確認してください"
    if isinstance(e, urllib.error.URLError):
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            return "ダウンロードがタイムアウトしました。ネットワークを確認してください"
        return "インターネットに接続できませんでした。ネットワークを確認してください"
    return "ダウンロードに失敗しました (%s)" % e


def validate_download(path, kind, min_bytes=DEFAULT_MIN_BYTES, expected_size=None):
    """ダウンロードしたファイルが空でなく、種類 (exe/zip/msi) の先頭シグネチャと一致するか確認する。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        raise DownloadError("ダウンロードしたファイルが見つかりません")
    if size <= 0:
        raise DownloadError("ダウンロードしたファイルが空でした")
    if size < min_bytes:
        raise DownloadError("ダウンロードしたファイルが小さすぎます（エラーページの可能性があります）")
    if expected_size and size != expected_size:
        raise DownloadError("ダウンロードが途中で切れました")
    with open(path, "rb") as f:
        head = f.read(8)
    sigs = SIGNATURES.get(kind)
    if sigs and not any(head.startswith(s) for s in sigs):
        raise DownloadError("ダウンロードしたファイルの形式が正しくありません")
    if kind == "zip" and not zipfile.is_zipfile(path):
        raise DownloadError("zipファイルが壊れています")
    return size


def download_file(url, dest, kind, progress=None, cancel=None, opener=None, timeout=30, min_bytes=DEFAULT_MIN_BYTES):
    """url を dest へ保存して検証する。progress(done, total) を随時呼ぶ (total 不明なら 0)。"""
    opener = opener or urllib.request.urlopen
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneCamSetup/%s" % APP_VERSION})
    expected = 0
    try:
        with opener(req, timeout=timeout) as resp:
            try:
                expected = int(resp.headers.get("Content-Length") or 0)
            except Exception:
                expected = 0
            done = 0
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadError("キャンセルされました")
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, expected)
    except DownloadError:
        _silent_remove(part)
        raise
    except Exception as e:
        _silent_remove(part)
        raise DownloadError(friendly_net_error(e))
    try:
        validate_download(part, kind, min_bytes=min_bytes, expected_size=expected or None)
    except DownloadError:
        _silent_remove(part)
        raise
    os.replace(part, dest)
    return dest


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def safe_extract_zip(zip_path, dest):
    """zip を dest へ展開する (dest の外へ出るパスは拒否)。"""
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    root = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = os.path.abspath(os.path.join(root, info.filename))
            if target != root and not target.startswith(root + os.sep):
                raise DownloadError("zipの中身が不正です")
        z.extractall(root)
    return root


def find_file(root, names):
    """root 以下から、ファイル名 (小文字) が names のいずれかに一致する最初のファイルを返す。"""
    wanted = [n.lower() for n in names]
    hits = {}
    for d, _, files in os.walk(root):
        for f in files:
            low = f.lower()
            if low in wanted and low not in hits:
                hits[low] = os.path.join(d, f)
    for n in wanted:
        if n in hits:
            return hits[n]
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# ジョブ (管理者権限のヘルパーが実行する内容)
# ---------------------------------------------------------------------------

def step_register_camera():
    return {"id": "camera", "type": "register_camera", "title": "PhoneCamカメラを登録"}


def step_exe(cid, title, path, args, ok_codes=(0, 3010, 1641), timeout=900, retry_args=None, lenient=False, reboot=False):
    return {"id": cid, "type": "exe", "title": title, "path": path, "args": list(args), "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout, "retry_args": retry_args, "lenient": lenient, "reboot": reboot}


def step_msi(cid, title, path, ok_codes=(0, 3010, 1641), timeout=900):
    return {"id": cid, "type": "msi", "title": title, "path": path, "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout}


def step_winget(cid, title, pkg_id, timeout=900):
    return {"id": cid, "type": "winget", "title": title, "pkg": pkg_id, "timeout": timeout}


def build_job(steps, result_path, progress_path):
    return {"version": 1, "result": result_path, "progress": progress_path, "steps": list(steps)}


def winget_args(pkg_id):
    return ["winget", "install", "--id", pkg_id, "-e", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]


# winget が「すでに導入済み / 更新なし」を返すときのコード
WINGET_OK_CODES = (0, 0x8A150061, 0x8A15002B)


def _write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_progress(path, text):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def _run_cmd(runner, args, timeout):
    flags = _CREATE_NO_WINDOW if os.name == "nt" else 0
    kw = {"timeout": timeout}
    if flags:
        kw["creationflags"] = flags
    return runner(args, **kw).returncode


def execute_step(step, runner=None):
    """1ステップを実行して結果 dict を返す (例外は失敗として返す)。"""
    runner = runner or subprocess.run
    res = {"id": step.get("id"), "title": step.get("title", ""), "ok": False, "code": None, "reboot": False, "detail": ""}
    try:
        kind = step.get("type")
        if kind == "register_camera":
            ok = bool(_call("register_camera"))
            res["ok"] = ok
            res["code"] = 0 if ok else 1
            if not ok:
                res["detail"] = "カメラの登録に失敗しました。OBSなどがPhoneCamカメラを使用中でないか確認してください"
            return res
        if kind in ("exe", "msi"):
            path = step["path"]
            if not os.path.isfile(path):
                res["detail"] = "インストーラーが見つかりません"
                return res
            if step.get("sha256") and sha256_file(path) != step["sha256"]:
                res["detail"] = "インストーラーが変更されていたため実行しませんでした"
                return res
            if kind == "msi":
                cmd = ["msiexec", "/i", path, "/qn", "/norestart"]
            else:
                cmd = [path] + list(step.get("args") or [])
            code = _run_cmd(runner, cmd, step.get("timeout", 900))
            ok_codes = [c & 0xFFFFFFFF for c in step.get("ok_codes", [0])]
            if (code & 0xFFFFFFFF) not in ok_codes and kind == "exe" and step.get("retry_args") is not None:
                code = _run_cmd(runner, [path] + list(step["retry_args"]), step.get("timeout", 900))
            res["code"] = code
            res["ok"] = (code & 0xFFFFFFFF) in ok_codes or bool(step.get("lenient"))
            res["reboot"] = (code & 0xFFFFFFFF) in EXIT_REBOOT or bool(step.get("reboot"))
            if not res["ok"]:
                res["detail"] = "インストーラーが失敗しました (終了コード %s)" % code
            return res
        if kind == "winget":
            code = _run_cmd(runner, winget_args(step["pkg"]), step.get("timeout", 900))
            res["code"] = code
            res["ok"] = (code & 0xFFFFFFFF) in WINGET_OK_CODES
            res["reboot"] = (code & 0xFFFFFFFF) in EXIT_REBOOT
            if not res["ok"]:
                res["detail"] = "wingetでのインストールに失敗しました (終了コード %s)" % code
            return res
        res["detail"] = "不明な手順です: %s" % kind
    except subprocess.TimeoutExpired:
        res["detail"] = "インストールが時間内に終わりませんでした"
    except Exception as e:
        res["detail"] = "エラー: %s" % e
    return res


def run_job(job_path, expected_sha=None, runner=None):
    """管理者権限で実行される --setup。結果を result.json へ書き、終了コードを返す。
    0 = 全て成功 / 3010 = 成功・要再起動 / 1 = 失敗あり。"""
    result = {"steps": [], "error": ""}
    result_path = None
    try:
        if expected_sha and sha256_file(job_path) != expected_sha:
            raise RuntimeError("ジョブファイルが変更されています")
        with open(job_path, "r", encoding="utf-8") as f:
            job = json.load(f)
        result_path = job.get("result") or os.path.join(os.path.dirname(os.path.abspath(job_path)), "result.json")
        progress = job.get("progress")
        steps = job.get("steps") or []
        total = len(steps)
        for i, step in enumerate(steps):
            if progress:
                _append_progress(progress, "%d|%d|%s" % (i + 1, total, step.get("title", "")))
            result["steps"].append(execute_step(step, runner=runner))
            if progress:
                _append_progress(progress, "%d|%d|done" % (i + 1, total))
    except Exception as e:
        result["error"] = str(e)
    try:
        if result_path is None:
            result_path = os.path.join(os.path.dirname(os.path.abspath(job_path)), "result.json")
        _write_json(result_path, result)
    except Exception:
        pass
    if result["error"] or any(not s["ok"] for s in result["steps"]):
        return 1
    return 3010 if any(s["reboot"] for s in result["steps"]) else 0


# ---------------------------------------------------------------------------
# 依存・選択
# ---------------------------------------------------------------------------

def default_selection(statuses, mic=False):
    """初期のチェック状態。導入済みは False、必須の未導入は True、任意は AltServer だけ True。"""
    sel = {}
    for c in COMPONENTS:
        cid = c["id"]
        st = statuses.get(cid, {}).get("state", STATE_MISSING)
        if st == STATE_OK:
            sel[cid] = False
        elif c["required"]:
            sel[cid] = st == STATE_MISSING
        elif cid == "vbcable":
            sel[cid] = bool(mic)
        else:
            sel[cid] = bool(c.get("default_on"))
    return sel


def is_selectable(cid, statuses):
    """ソース実行時のカメラのようにインストールできない行は False。"""
    if cid == "camera" and not _frozen():
        return False
    st = statuses.get(cid, {}).get("state", STATE_MISSING)
    return st != STATE_OK


def expand_selection(ids, statuses):
    """AltServer を選んだら、足りない iTunes (公式サイト版) と iCloud も追加する。並びはインストール順。"""
    chosen = set(ids)
    if "altserver" in chosen:
        for dep in ("itunes", "icloud"):
            if statuses.get(dep, {}).get("state") != STATE_OK:
                chosen.add(dep)
    if "camera" in chosen and statuses.get("vcredist", {}).get("state") == STATE_MISSING:
        chosen.add("vcredist")
    return [c for c in INSTALL_ORDER if c in chosen]


# ---------------------------------------------------------------------------
# エンジン
# ---------------------------------------------------------------------------

def _mb(n):
    return "%.1f MB" % (n / 1048576.0)


class SetupEngine:
    """ダウンロード → (1回の管理者確認で) インストール → 再検出。run() はブロックするのでワーカースレッドで呼ぶ。"""

    def __init__(self, directory=None, downloader=None, winget_finder=None, detector=None):
        self.dir = directory or setup_dir()
        self.downloader = downloader or download_file
        self.winget_finder = winget_finder or (lambda: shutil.which("winget"))
        self.detector = detector or detect_all

    # -- 1コンポーネント分のステップを作る ---------------------------------
    def _prepare(self, cid, emit, cancel, progress):
        src = SOURCES[cid]
        dest = os.path.join(self.dir, src["file"])
        emit("stage", text="%s をダウンロード中…" % COMPONENT_BY_ID[cid]["name"])
        self.downloader(src["url"], dest, src["kind"], progress=progress, cancel=cancel)
        title = "%s をインストール" % COMPONENT_BY_ID[cid]["name"]
        if cid == "altserver":
            root = safe_extract_zip(dest, os.path.join(self.dir, "altserver"))
            msi = find_file(root, ["setup.msi", "altserver.msi", "altinstaller.msi"])
            if msi:
                return step_msi(cid, title, msi)
            exe = find_file(root, ["setup.exe"])
            if exe:
                # 無人インストールが効かない場合は、通常のウィンドウ付きでもう一度実行する
                return step_exe(cid, title, exe, ["/quiet", "/norestart"], retry_args=[])
            raise DownloadError("zipの中にインストーラーが見つかりませんでした")
        if cid == "vbcable":
            root = safe_extract_zip(dest, os.path.join(self.dir, "vbcable"))
            exe = find_file(root, ["vbcable_setup_x64.exe", "vbcable_setup.exe"])
            if not exe:
                raise DownloadError("zipの中にインストーラーが見つかりませんでした")
            # VB-CABLE は終了コードがあてにならないので、導入後の再検出で判定する
            return step_exe(cid, title, exe, ["-i", "-h"], timeout=300, lenient=True, reboot=True)
        return step_exe(cid, title, dest, src["args"], ok_codes=src.get("ok_codes", (0, 3010, 1641)))

    def run(self, ids, emit=None, cancel=None):
        emit = emit or (lambda kind, **kw: None)
        out = {"results": {}, "manual": [], "reboot": False, "error": "", "statuses": {}, "ids": list(ids)}
        os.makedirs(self.dir, exist_ok=True)
        total_units = len([c for c in ids if c in SOURCES]) + len(ids)
        total_units = max(total_units, 1)
        state = {"units": 0.0}

        def set_progress(extra=0.0):
            emit("progress", value=min(1.0, (state["units"] + extra) / total_units))

        steps = []
        for cid in ids:
            name = COMPONENT_BY_ID[cid]["name"]
            if cid == "camera":
                steps.append(step_register_camera())
                continue
            if cid not in SOURCES:
                continue

            def prog(done, total, _name=name):
                if total:
                    emit("stage", text="%s をダウンロード中… %s / %s" % (_name, _mb(done), _mb(total)))
                    set_progress(done / float(total))
                else:
                    emit("stage", text="%s をダウンロード中… %s" % (_name, _mb(done)))

            try:
                steps.append(self._prepare(cid, emit, cancel, prog))
                emit("log", text="%s: ダウンロード完了" % name)
            except Exception as e:
                msg = str(e) if isinstance(e, DownloadError) else "準備に失敗しました (%s)" % e
                emit("log", text="%s: %s" % (name, msg))
                src = SOURCES[cid]
                if src.get("winget") and self.winget_finder():
                    emit("log", text="%s: wingetで代わりにインストールします" % name)
                    steps.append(step_winget(cid, "%s をインストール (winget)" % name, src["winget"]))
                else:
                    out["results"][cid] = {"ok": False, "detail": msg, "code": None}
                    out["manual"].append({"id": cid, "name": name, "url": src["page"], "text": MANUAL_TEXT.get(cid, "")})
            state["units"] += 1
            set_progress()

        if steps:
            self._run_elevated(steps, out, emit, state, total_units)
        else:
            state["units"] = total_units
        emit("progress", value=1.0)
        emit("stage", text="結果を確認中…")
        try:
            out["statuses"] = self.detector()
        except Exception as e:
            out["error"] = out["error"] or "確認に失敗しました (%s)" % e
        # インストールは成功したのに検出できない場合は「要確認」
        for cid, r in out["results"].items():
            st = out["statuses"].get(cid, {}).get("state")
            if r["ok"] and st is not None and st != STATE_OK and cid != "camera":
                r["detail"] = r["detail"] or "インストールは終わりましたが、まだ認識できません。再起動してから再チェックしてください"
            elif r["ok"] and cid == "camera" and st is not None and st != STATE_OK:
                r["ok"] = False
                r["detail"] = "登録の処理は終わりましたが、状態が変わっていません。もう一度お試しください"
        if "vbcable" in out["results"] and out["results"]["vbcable"]["ok"]:
            out["reboot"] = True
        return out

    def _run_elevated(self, steps, out, emit, state, total_units):
        job_path = os.path.join(self.dir, "job.json")
        result_path = os.path.join(self.dir, "result.json")
        progress_path = os.path.join(self.dir, "progress.log")
        for p in (result_path, progress_path):
            _silent_remove(p)
        _write_json(job_path, build_job(steps, result_path, progress_path))
        sha = sha256_file(job_path)
        params = '--setup "%s" --sha256 %s' % (job_path, sha)
        if not _frozen():
            script = _host.get("script_path")
            if script:
                params = '"%s" %s' % (script(), params)
        emit("stage", text="管理者の確認が表示されたら「はい」を押してください…")
        emit("log", text="管理者権限でインストールを開始します (%d 件)" % len(steps))
        box = []

        def work():
            try:
                box.append(_call("run_elevated", params, JOB_TIMEOUT_MS))
            except Exception as e:
                box.append(("error", str(e)))

        t = threading.Thread(target=work, name="setup-elevated", daemon=True)
        t.start()
        base_units = state["units"]
        seen = 0
        while t.is_alive():
            t.join(0.4)
            seen = self._poll_progress(progress_path, seen, emit, state, base_units, total_units)
        self._poll_progress(progress_path, seen, emit, state, base_units, total_units)
        kind, code = box[0] if box else ("error", 0)
        result = None
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
        step_by_id = {s["id"]: s for s in steps}
        if result is None:
            if kind == "cancelled":
                out["error"] = "管理者の確認がキャンセルされたため、インストールしませんでした。もう一度お試しください。"
            elif kind == "timeout":
                out["error"] = "インストールが時間内に終わりませんでした。もう一度お試しください。"
            else:
                out["error"] = "インストールを実行できませんでした (%s %s)。" % (kind, code)
            for cid in step_by_id:
                out["results"][cid] = {"ok": False, "detail": out["error"], "code": None}
            return
        if result.get("error"):
            out["error"] = result["error"]
        for r in result.get("steps", []):
            out["results"][r["id"]] = {"ok": bool(r.get("ok")), "detail": r.get("detail", ""), "code": r.get("code")}
            if r.get("reboot"):
                out["reboot"] = True
            emit("log", text="%s: %s" % (r.get("title", r.get("id")), "完了" if r.get("ok") else ("失敗 " + r.get("detail", ""))))
        for cid in step_by_id:
            out["results"].setdefault(cid, {"ok": False, "detail": "実行されませんでした", "code": None})
        state["units"] = base_units + len(steps)

    @staticmethod
    def _poll_progress(path, seen, emit, state, base_units, total_units):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except Exception:
            return seen
        for line in lines[seen:]:
            parts = line.split("|", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                i, n, title = int(parts[0]), int(parts[1]), parts[2]
                done = i if title == "done" else i - 1
                emit("progress", value=min(1.0, (base_units + done) / float(total_units)))
                if title != "done":
                    emit("stage", text="%s…" % title)
        return len(lines)


# ---------------------------------------------------------------------------
# コントローラー (UI から独立した状態管理)
# ---------------------------------------------------------------------------

class SetupController:
    """検出結果・選択状態・実行状態を持つ。UI は listeners に登録して更新を受ける。"""

    def __init__(self, root, engine=None, detector=None, can_start=None, on_busy=None, on_changed=None):
        self.root = root
        self.engine = engine or SetupEngine()
        self.detector = detector or detect_all
        self.can_start = can_start or (lambda: True)
        self.on_busy = on_busy or (lambda flag: None)
        self.on_changed = on_changed or (lambda: None)
        self.statuses = {}
        self.checked = False
        self.selection = {}
        self.touched = set()
        self.mic = False
        self.busy = False
        self.checking = False
        self.outcome = None
        self.log_lines = []
        self.stage = ""
        self.progress = 0.0
        self.listeners = []
        self.cancel = threading.Event()
        self._q = queue.Queue()
        self._pumping = False
        self.closed = False

    # -- 通知 -------------------------------------------------------------
    def listen(self, fn):
        self.listeners.append(fn)

    def unlisten(self, fn):
        if fn in self.listeners:
            self.listeners.remove(fn)

    def _notify(self, what):
        for fn in list(self.listeners):
            try:
                fn(what)
            except Exception:
                pass

    # -- スレッド → UI -----------------------------------------------------
    def _arm(self):
        if self._pumping or self.closed:
            return
        self._pumping = True
        try:
            self.root.after(100, self._tick)
        except Exception:
            self._pumping = False

    def _tick(self):
        self._pumping = False
        self.pump()
        if (self.busy or self.checking) and not self.closed:
            self._arm()

    def pump(self):
        """キューの内容を UI スレッドで処理する。"""
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(kind, payload)
            except Exception:
                pass

    def _handle(self, kind, payload):
        if kind == "stage":
            self.stage = payload
            self._notify("stage")
        elif kind == "progress":
            self.progress = payload
            self._notify("progress")
        elif kind == "log":
            self.log_lines.append(payload)
            self._notify("log")
        elif kind == "checked":
            self.statuses = payload
            self.checked = True
            self.checking = False
            self.apply_defaults()
            self._notify("statuses")
            self.on_changed()
        elif kind == "done":
            self.busy = False
            self.outcome = payload
            if payload.get("statuses"):
                self.statuses = payload["statuses"]
                self.apply_defaults()
            self.on_busy(False)
            self._notify("done")
            self._notify("statuses")
            self.on_changed()

    # -- 検出 -------------------------------------------------------------
    def recheck(self):
        if self.checking or self.busy:
            return
        self.checking = True
        self._notify("checking")

        def work():
            try:
                res = self.detector()
            except Exception as e:
                res = {c["id"]: {"state": STATE_WARN, "detail": "確認できませんでした (%s)" % e} for c in COMPONENTS}
            self._q.put(("checked", res))

        threading.Thread(target=work, name="setup-detect", daemon=True).start()
        self._arm()

    # -- 選択 -------------------------------------------------------------
    def apply_defaults(self):
        base = default_selection(self.statuses, self.mic)
        for cid, v in base.items():
            locked = COMPONENT_BY_ID[cid]["required"] and v
            if cid in self.touched and not locked and self.statuses.get(cid, {}).get("state") != STATE_OK:
                continue  # 利用者が触った任意項目はそのまま
            self.selection[cid] = v

    def set_selected(self, cid, value):
        self.touched.add(cid)
        self.selection[cid] = bool(value)

    def set_mic(self, flag):
        self.mic = bool(flag)
        self.touched.add("vbcable")
        if self.statuses.get("vbcable", {}).get("state") != STATE_OK:
            self.selection["vbcable"] = self.mic

    def selected_ids(self):
        ids = [cid for cid, v in self.selection.items() if v and is_selectable(cid, self.statuses)]
        return expand_selection(ids, self.statuses)

    # -- インストール -----------------------------------------------------
    def start(self, ids):
        """ids をインストールする。開始できなければ False。"""
        if self.busy or not ids or not self.can_start():
            return False
        ids = expand_selection(ids, self.statuses)
        self.busy = True
        self.outcome = None
        self.log_lines = []
        self.stage = "準備しています…"
        self.progress = 0.0
        self.cancel.clear()
        self.on_busy(True)
        self._notify("start")

        def emit(kind, **kw):
            self._q.put((kind, kw.get("text") if kind in ("stage", "log") else kw.get("value")))

        def work():
            try:
                out = self.engine.run(ids, emit=emit, cancel=self.cancel)
            except Exception as e:
                out = {"results": {}, "manual": [], "reboot": False, "statuses": {}, "ids": ids,
                       "error": "セットアップ中にエラーが発生しました (%s)" % e}
            self._q.put(("done", out))

        threading.Thread(target=work, name="setup-run", daemon=True).start()
        self._arm()
        return True

    def close(self):
        self.closed = True
        self.cancel.set()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

BG = "#ffffff"
PANEL = "#f5f5f7"
FG = "#1d1d1f"
SUB = "#6e6e73"
LINE = "#d2d2d7"
ACCENT = "#0071e3"
GOOD = "#248a3d"
WARN = "#b25000"
BAD = "#d70015"
FONT = "Yu Gothic UI"

ICONS = {STATE_OK: ("✓", GOOD, "導入済み"), STATE_MISSING: ("●", SUB, "未導入"), STATE_WARN: ("!", WARN, "要確認")}


def make_button(parent, text, command, primary=False, state="normal"):
    """角のない平らなボタン。"""
    if primary:
        b = tk.Button(parent, text=text, command=command, bg=ACCENT, fg="#ffffff", activebackground="#0077ed",
                      activeforeground="#ffffff", relief="flat", bd=0, padx=22, pady=7, cursor="hand2",
                      font=(FONT, 10, "bold"), disabledforeground="#c7c7cc", state=state)
    else:
        b = tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG, activebackground="#e8e8ed",
                      activeforeground=FG, relief="flat", bd=0, padx=16, pady=6, cursor="hand2",
                      font=(FONT, 10), disabledforeground="#c7c7cc", state=state)
    return b


def make_link(parent, text, command, bg=BG):
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=SUB, activebackground=bg, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2", font=(FONT, 9), padx=4, pady=2)
    return b


class ThinProgress:
    """細い直線のプログレスバー。"""

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, height=4, bg=PANEL, highlightthickness=0, bd=0)
        self.value = 0.0
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def set(self, v):
        self.value = max(0.0, min(1.0, v))
        self._draw()

    def _draw(self):
        try:
            w = self.canvas.winfo_width()
            self.canvas.delete("all")
            self.canvas.create_rectangle(0, 0, int(w * self.value), 4, fill=ACCENT, width=0)
        except Exception:
            pass


class SetupPanel:
    """コンポーネント一覧。mode = "wizard" (チェックボックス) / "tab" (行ごとのインストールボタン) / "result" (結果表示)。"""

    def __init__(self, parent, controller, mode="wizard", on_install_one=None, results=None):
        self.controller = controller
        self.mode = mode
        self.on_install_one = on_install_one
        self.results = results or {}
        self.frame = tk.Frame(parent, bg=BG)
        self.vars = {}
        self.install_buttons = {}

    def pack(self, **kw):
        self.frame.pack(**kw)

    def set_results(self, results):
        self.results = results or {}

    def render(self):
        for w in list(self.frame.winfo_children()):
            w.destroy()
        self.vars = {}
        self.install_buttons = {}
        c = self.controller
        if not c.checked:
            tk.Label(self.frame, text="確認しています…", bg=BG, fg=SUB, font=(FONT, 10)).pack(anchor="w", pady=20)
            return
        for comp in COMPONENTS:
            self._row(comp)

    def _row(self, comp):
        c = self.controller
        cid = comp["id"]
        st = c.statuses.get(cid, {"state": STATE_WARN, "detail": ""})
        state = st.get("state", STATE_WARN)
        res = self.results.get(cid)
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x")
        inner = tk.Frame(row, bg=BG)
        inner.pack(fill="x", padx=2, pady=9)
        inner.columnconfigure(1, weight=1)

        if res is not None:
            icon, color, word = ("✓", GOOD, "完了") if res.get("ok") else ("✗", BAD, "失敗")
        else:
            icon, color, word = ICONS.get(state, ICONS[STATE_WARN])
        tk.Label(inner, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold"), width=3).grid(row=0, column=0, rowspan=2, sticky="n")
        head = tk.Frame(inner, bg=BG)
        head.grid(row=0, column=1, sticky="w")
        tk.Label(head, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text="  " + word, fg=color, bg=BG, font=(FONT, 9)).pack(side="left")
        detail = (res or {}).get("detail") if res is not None else st.get("detail", "")
        sub = tk.Frame(inner, bg=BG)
        sub.grid(row=1, column=1, sticky="w")
        tk.Label(sub, text=comp["purpose"], fg=SUB, bg=BG, font=(FONT, 9), justify="left", wraplength=330).pack(anchor="w")
        if detail:
            tk.Label(sub, text=detail, fg=(BAD if res is not None else WARN), bg=BG, font=(FONT, 9), justify="left",
                     wraplength=330).pack(anchor="w")
        action = tk.Frame(inner, bg=BG)
        action.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))
        if self.mode == "wizard" and res is None:
            self._wizard_action(action, comp, state)
        elif self.mode == "tab" and res is None:
            self._tab_action(action, comp, state)
        tk.Frame(self.frame, bg=LINE, height=1).pack(fill="x")

    def _wizard_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK:
            tk.Label(action, text="スキップ", fg=SUB, bg=BG, font=(FONT, 9)).pack()
            return
        if not is_selectable(cid, c.statuses):
            return
        var = tk.BooleanVar(value=bool(c.selection.get(cid)))
        self.vars[cid] = var
        locked = comp["required"] and state == STATE_MISSING
        if locked:
            var.set(True)
        cb = tk.Checkbutton(action, variable=var, bg=BG, activebackground=BG, bd=0, highlightthickness=0,
                            command=lambda cid=cid, v=var: c.set_selected(cid, v.get()))
        if locked:
            cb.config(state="disabled")
        cb.pack()

    def _tab_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK or not is_selectable(cid, c.statuses):
            return
        label = "更新" if cid == "camera" and c.statuses.get(cid, {}).get("detail") else "インストール"
        b = make_button(action, label, lambda cid=cid: self.on_install_one and self.on_install_one(cid),
                        state=("disabled" if c.busy else "normal"))
        b.pack()
        self.install_buttons[cid] = b


class SetupWizard:
    """初回セットアップ / やり直し用のウィザード (ほぼモーダルな Toplevel)。"""

    TITLES = ["ようこそ", "必要なソフトの確認", "インストール中", "iPhoneの準備"]

    def __init__(self, root, controller, on_finish, on_skip=None, open_url=None, launch=None):
        self.root = root
        self.controller = controller
        self.on_finish = on_finish
        self.on_skip = on_skip or on_finish
        self.open_url = open_url or webbrowser.open
        self.launch = launch or _startfile
        self.page = 0
        self.finished = False
        self.top = tk.Toplevel(root)
        self.top.title("PhoneCam セットアップ")
        self.top.configure(bg=BG)
        try:
            self.top.geometry("560x520")
            self.top.resizable(False, False)
            self.top.transient(root)
            self.top.protocol("WM_DELETE_WINDOW", self._close)
            self.top.update_idletasks()
            x = root.winfo_rootx() + max(0, (root.winfo_width() - 560) // 2)
            y = root.winfo_rooty() + max(0, (root.winfo_height() - 520) // 3)
            self.top.geometry("560x520+%d+%d" % (x, y))
            self.top.grab_set()
        except Exception:
            pass
        self.footer = tk.Frame(self.top, bg=BG)
        self.footer.pack(side="bottom", fill="x", padx=28, pady=(0, 22))
        self.body = tk.Frame(self.top, bg=BG)
        self.body.pack(side="top", fill="both", expand=True, padx=28, pady=(26, 8))
        self.panel = None
        self.log_visible = False
        self._listener = self._on_controller
        controller.listen(self._listener)
        self.show(0)

    # -- 共通 -------------------------------------------------------------
    def _clear(self):
        for w in list(self.body.winfo_children()):
            w.destroy()
        for w in list(self.footer.winfo_children()):
            w.destroy()
        self.panel = None
        self.log_text = None
        self.stage_label = None
        self.bar = None
        self.next_btn = None
        self.retry_btn = None
        self.result_holder = None
        self.note_label = None

    def _title(self, text, sub=None):
        tk.Label(self.body, text=text, bg=BG, fg=FG, font=(FONT, 20, "bold")).pack(anchor="w")
        if sub:
            tk.Label(self.body, text=sub, bg=BG, fg=SUB, font=(FONT, 10), justify="left", wraplength=500).pack(anchor="w", pady=(6, 14))

    def show(self, index):
        self.page = index
        self._clear()
        [self._page_welcome, self._page_check, self._page_install, self._page_iphone][index]()

    # -- ページ1 ----------------------------------------------------------
    def _page_welcome(self):
        tk.Label(self.body, text="PhoneCam へようこそ", bg=BG, fg=FG, font=(FONT, 22, "bold")).pack(anchor="w", pady=(40, 0))
        tk.Label(self.body, justify="left", wraplength=500, bg=BG, fg=SUB, font=(FONT, 11),
                 text="iPhoneをOBSのカメラにするために必要なソフトを確認して、足りないものをまとめて入れます。\n"
                      "すでに入っているものは飛ばします。管理者の確認は最初に1回だけ表示されます。"
                 ).pack(anchor="w", pady=(16, 0))
        make_link(self.footer, "スキップ", self._skip).pack(side="left")
        make_button(self.footer, "次へ", lambda: self.show(1), primary=True).pack(side="right")

    # -- ページ2 ----------------------------------------------------------
    def _page_check(self):
        self._title("必要なソフトの確認")
        self.panel = SetupPanel(self.body, self.controller, "wizard")
        self.panel.pack(fill="x")
        self.mic_var = tk.BooleanVar(value=self.controller.mic)
        mic = tk.Checkbutton(self.body, text="iPhoneのマイクを使う（VB-CABLEを入れます）", variable=self.mic_var, bg=BG,
                             activebackground=BG, fg=FG, font=(FONT, 10), bd=0, highlightthickness=0,
                             command=self._mic_changed)
        mic.pack(anchor="w", pady=(12, 0))
        make_link(self.footer, "スキップ", self._skip).pack(side="left")
        self.next_btn = make_button(self.footer, "インストール", self._start_install, primary=True)
        self.next_btn.pack(side="right")
        make_button(self.footer, "再チェック", self.controller.recheck).pack(side="right", padx=(0, 10))
        self.panel.render()
        self._update_next_label()
        if not self.controller.checked:
            self.controller.recheck()

    def _mic_changed(self):
        self.controller.set_mic(self.mic_var.get())
        self.panel.render()
        self._update_next_label()

    def _update_next_label(self):
        if self.next_btn is None:
            return
        ids = self.controller.selected_ids()
        try:
            self.next_btn.config(text="インストール" if ids else "次へ")
        except Exception:
            pass

    def _start_install(self):
        ids = self.controller.selected_ids()
        if not ids:
            self.show(3)
            return
        if self.controller.start(ids):
            self.show(2)

    # -- ページ3 ----------------------------------------------------------
    def _page_install(self):
        self._title("インストール中")
        self.stage_label = tk.Label(self.body, text=self.controller.stage, bg=BG, fg=FG, font=(FONT, 10), anchor="w",
                                    justify="left", wraplength=500)
        self.stage_label.pack(fill="x")
        self.bar = ThinProgress(self.body)
        self.bar.pack(fill="x", pady=(8, 6))
        self.bar.set(self.controller.progress)
        self.detail_btn = make_link(self.body, "詳細 ▸", self._toggle_log)
        self.detail_btn.pack(anchor="w")
        self.log_frame = tk.Frame(self.body, bg=BG)
        self.log_text = tk.Text(self.log_frame, height=7, bg=PANEL, fg=SUB, relief="flat", bd=0, font=(FONT, 8),
                                wrap="word", highlightthickness=0, padx=8, pady=6)
        sb = tk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_visible = False
        self._refill_log()
        self.result_holder = tk.Frame(self.body, bg=BG)
        self.note_label = tk.Label(self.body, text="", bg=BG, fg=WARN, font=(FONT, 10), justify="left", wraplength=500)
        self.next_btn = make_button(self.footer, "次へ", lambda: self.show(3), primary=True, state="disabled")
        self.next_btn.pack(side="right")
        self.retry_btn = make_button(self.footer, "もう一度試す", self._retry)
        if not self.controller.busy and self.controller.outcome is not None:
            self._show_outcome()

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(4, 0))
            self.detail_btn.config(text="詳細 ▾")
        else:
            self.log_frame.pack_forget()
            self.detail_btn.config(text="詳細 ▸")

    def _refill_log(self):
        if self.log_text is None:
            return
        try:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", "\n".join(self.controller.log_lines))
            self.log_text.config(state="disabled")
            self.log_text.see("end")
        except Exception:
            pass

    def _retry(self):
        self.controller.recheck()
        self.show(1)

    def _show_outcome(self):
        out = self.controller.outcome or {}
        if self.stage_label is not None:
            self.stage_label.config(text="完了しました" if not out.get("error") and all(
                r.get("ok") for r in out.get("results", {}).values()) else "一部のインストールが完了しませんでした")
        if self.bar is not None:
            self.bar.set(1.0)
        self._render_results(out)
        self.result_holder.pack(fill="x", pady=(8, 0))
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        if out.get("reboot"):
            notes.append("再起動が必要です。セットアップ後にPCを再起動してください。")
        for m in out.get("manual", []):
            notes.append("%s は自動で入れられませんでした。「%s」" % (m["name"], m["text"]))
        if notes:
            self.note_label.config(text="\n".join(notes))
            self.note_label.pack(anchor="w", pady=(8, 0))
        for m in out.get("manual", []):
            url = m["url"]
            make_link(self.body, "%s の公式ページを開く" % m["name"], lambda u=url: self.open_url(u)).pack(anchor="w")
        if self.next_btn is not None:
            self.next_btn.config(state="normal")
        failed = [cid for cid, r in out.get("results", {}).items() if not r.get("ok")]
        if failed and self.retry_btn is not None:
            self.retry_btn.pack(side="right", padx=(0, 10))

    def _render_results(self, out):
        """結果の行 (インストールした項目だけ) を描画する。"""
        ids = out.get("ids", [])
        for cid in ids:
            comp = COMPONENT_BY_ID[cid]
            r = out["results"].get(cid, {"ok": False, "detail": ""})
            row = tk.Frame(self.result_holder, bg=BG)
            row.pack(fill="x", pady=3)
            icon, color = ("✓", GOOD) if r.get("ok") else ("✗", BAD)
            tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 11, "bold"), width=3).pack(side="left", anchor="n")
            col = tk.Frame(row, bg=BG)
            col.pack(side="left", fill="x")
            tk.Label(col, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
            if r.get("detail"):
                tk.Label(col, text=r["detail"], fg=BAD if not r.get("ok") else WARN, bg=BG, font=(FONT, 9), justify="left",
                         wraplength=440).pack(anchor="w")

    # -- ページ4 ----------------------------------------------------------
    def _page_iphone(self):
        self._title("iPhoneの準備")
        steps = [
            "AltServerを起動して、iPhoneをUSBでつなぎます（「信頼」を選びます）。",
            "タスクトレイのAltServerのアイコン → Install AltStore → iPhoneを選びます。",
            "PhoneCam.ipa をiPhoneに保存し、AltStoreの My Apps → ＋ から追加します。",
        ]
        for i, t in enumerate(steps, 1):
            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", pady=5)
            tk.Label(row, text=str(i), fg=SUB, bg=BG, font=(FONT, 14, "bold"), width=3).pack(side="left", anchor="n")
            tk.Label(row, text=t, fg=FG, bg=BG, font=(FONT, 10), justify="left", wraplength=450).pack(side="left", anchor="w")
        box = tk.Frame(self.body, bg=BG)
        box.pack(anchor="w", pady=(18, 0))
        url = ipa_url()
        if url:
            make_button(box, "iPhone用アプリをダウンロード", lambda: self.open_url(url)).pack(side="left")
        alt = find_altserver()
        if alt:
            make_button(box, "AltServerを起動", lambda: self.launch(alt)).pack(side="left", padx=(10, 0))
        make_button(self.footer, "完了", self._finish, primary=True).pack(side="right")

    # -- 終了 -------------------------------------------------------------
    def _finish(self):
        self._end(self.on_finish)

    def _skip(self):
        self._end(self.on_skip)

    def _close(self):
        if self.controller.busy:
            return
        if self.page == 3:
            self._finish()
        else:
            self._skip()

    def _end(self, cb):
        if self.finished:
            return
        self.finished = True
        self.controller.unlisten(self._listener)
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        try:
            cb()
        except Exception:
            pass

    # -- コントローラーからの通知 ------------------------------------------
    def _on_controller(self, what):
        if self.finished:
            return
        if what == "statuses" or what == "checking":
            if self.page == 1 and self.panel is not None:
                self.panel.render()
                self._update_next_label()
        elif what == "stage" and self.stage_label is not None:
            self.stage_label.config(text=self.controller.stage)
        elif what == "progress" and self.bar is not None:
            self.bar.set(self.controller.progress)
        elif what == "log":
            self._refill_log()
        elif what == "done" and self.page == 2:
            self._show_outcome()


def _startfile(path):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass
