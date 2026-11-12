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

try:
    import phonecam_iphone as iphone  # iPhone のセットアップ案内 (usbmux への問い合わせ・手順の判定)
except Exception:  # pragma: no cover
    iphone = None

try:
    import phonecam_qr  # 依存なしの QR コード生成
except Exception:  # pragma: no cover
    phonecam_qr = None


# ---------------------------------------------------------------------------
# 本体との連携
# ---------------------------------------------------------------------------

_host = {}


def bind(**fns):
    """本体から関数を受け取る: is_frozen / registration_status / register_camera / run_elevated / script_path
    (任意: link_connected = 受信アプリが iPhone につながっているか)"""
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
    {"id": "vbcable", "name": "VB-CABLE", "purpose": "iPhoneのマイクをOBSで使う（任意）。今のスピーカーとマイクは既定のまま変わりません", "required": False},
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
MAX_UNZIPPED_BYTES = 1024 * 1024 * 1024   # zip 爆弾対策 (展開後の合計)
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
    store = installed_store_packages("itunes")
    if store and (not nonstore or "AppleInc.AppleDevices" in store):
        names = "・".join("「%s」" % STORE_LABELS.get(p, p) for p in store)
        return {"state": STATE_WARN,
                "detail": "Microsoft Store版の%sが入っています。AltServerと一緒に使えないため、"
                          "インストール時に削除して公式サイト版のiTunesに入れ替えます" % names}
    if service and nonstore:
        return {"state": STATE_OK, "detail": ""}
    if service:
        return {"state": STATE_WARN, "detail": "USB接続はOK・AltServerには公式サイト版が必要"}
    return {"state": STATE_MISSING, "detail": ""}


# Microsoft Store 版のパッケージ名。公式サイト版と共存できず、AltServer は公式サイト版が必要なので入れ替える
# itunes には「Appleデバイス」アプリも含める。入っていると iTunes でiPhoneを扱えず、AltServer がiPhoneを見つけられない (エラー2003)
STORE_PACKAGES = {"icloud": ["AppleInc.iCloud"], "itunes": ["AppleInc.iTunes", "AppleInc.AppleDevices"]}
STORE_LABELS = {"AppleInc.iCloud": "iCloud", "AppleInc.iTunes": "iTunes", "AppleInc.AppleDevices": "Appleデバイス"}


APPX_REPO_KEY = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages"


def store_package_installed(cid):
    r"""Microsoft Store 版 (AppleInc.iCloud_* など) が今のユーザーに入っているか。
    %LOCALAPPDATA%\Packages のフォルダはアンインストール後も残ることがあるので使わず、
    インストール済みパッケージの登録 (HKCU の AppModel\Repository\Packages) を見る。"""
    return bool(installed_store_packages(cid))


def installed_store_packages(cid):
    """cid に関係する Store パッケージのうち、今のユーザーに入っているものの名前一覧"""
    wanted = STORE_PACKAGES.get(cid) or []
    wr = _winreg()
    if not wanted or wr is None:
        return []
    found = []
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, APPX_REPO_KEY) as root:
            i = 0
            while True:
                try:
                    name = wr.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                for pkg in wanted:
                    if name.startswith(pkg + "_") and pkg not in found:
                        found.append(pkg)
    except OSError:
        return []
    return found


def _store_icloud_installed():
    return store_package_installed("icloud")


def remove_store_package(cid, runner=None):
    """Store 版を現在のユーザーから削除する (管理者権限は不要。通常権限のプロセスで呼ぶこと)。
    戻り値: (成功したか, メッセージ)"""
    pkgs = STORE_PACKAGES.get(cid) or []
    if not pkgs:
        return True, ""
    script = "; ".join(
        "$p = Get-AppxPackage -Name '%s'; if ($p) { $p | Remove-AppxPackage -ErrorAction Stop }" % pkg for pkg in pkgs)
    script += "; if (%s) { exit 2 } else { exit 0 }" % " -or ".join("(Get-AppxPackage -Name '%s')" % pkg for pkg in pkgs)
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        if runner is not None:
            code = runner(cmd)
        else:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            code = subprocess.run(cmd, creationflags=flags, timeout=300,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except Exception as e:
        return False, "Microsoft Store版を削除できませんでした (%s)" % e
    if code != 0:
        return False, "Microsoft Store版を削除できませんでした (終了コード %s)。設定 > アプリ から手動でアンインストールしてください" % code
    return True, "Microsoft Store版（%s）を削除しました" % "・".join(STORE_LABELS.get(p, p) for p in pkgs)


def detect_icloud(entries=None):
    entries = uninstall_entries() if entries is None else entries
    # 公式サイト版はアンインストール情報に「iCloud」で登録される。失敗したインストールの残りファイルで
    # 誤判定しないよう、ファイルの有無ではなく登録で判定する
    if _has_entry(entries, "icloud", exact=True):
        return {"state": STATE_OK, "detail": ""}
    if _store_icloud_installed():
        # Store 版があると公式サイト版のインストールは失敗する (終了コード 1603)。AltStore が動いていればそのままでよい
        return {"state": STATE_WARN, "detail": "Microsoft Store版が入っています（インストール時に削除して公式サイト版に入れ替えます）"}
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
    if not str(url).lower().startswith("https://"):
        raise DownloadError("安全でないURL (https以外) からはダウンロードしません")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "PhoneCamSetup/%s" % APP_VERSION})
    expected = 0
    try:
        with opener(req, timeout=timeout) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
            if isinstance(final_url, str) and not final_url.lower().startswith("https://"):
                raise DownloadError("ダウンロード先が安全でないURL (https以外) に転送されたため中止しました")
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
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            target = os.path.abspath(os.path.join(root, info.filename))
            # dest の外へ出るパス (../ や絶対パス・ドライブ指定) は拒否
            if (target != root and not target.startswith(root + os.sep)) or name.startswith("/") \
                    or ".." in name.split("/") or (len(name) > 1 and name[1] == ":"):
                raise DownloadError("zipの中身が不正です")
            total += info.file_size
        if total > MAX_UNZIPPED_BYTES:
            raise DownloadError("zipの展開後のサイズが大きすぎます")
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
# インストーラーの署名確認 (Authenticode)
# ---------------------------------------------------------------------------
# 管理者権限で実行する前に、ダウンロードしたインストーラーの電子署名が有効で、
# 署名者が想定どおりの発行元かを確認する。署名者の判定ロジック (evaluate_signature) は
# 実際の確認処理 (verify_authenticode) と切り離してあり、確認処理を差し替えてテストできる。

# コンポーネントごとの想定する発行元。match: "exact" = 証明書の名前 (CN または O) と完全一致 (大文字小文字は無視) /
# "contains" = 部分一致。unsigned_ok: 署名のないファイルを許すか (許さない既定)
SIGNER_POLICY = {
    "vcredist": {"publisher": "Microsoft Corporation", "names": ["Microsoft Corporation"], "match": "exact", "unsigned_ok": False},
    "itunes": {"publisher": "Apple Inc.", "names": ["Apple Inc."], "match": "exact", "unsigned_ok": False},
    "icloud": {"publisher": "Apple Inc.", "names": ["Apple Inc."], "match": "exact", "unsigned_ok": False},
    "vbcable": {"publisher": "VB-Audio Software (Vincent Burel)", "names": ["VB-Audio", "Vincent Burel"], "match": "contains", "unsigned_ok": False},
    # AltServer は公式の配布物が署名されているか確認できていない。署名されていれば発行元を確認し、
    # 署名が無いときだけは「ジョブファイルのSHA-256による改ざん検知」(従来の保護) に頼って実行する。
    "altserver": {"publisher": "Riley Testut / AltStore", "names": ["Riley", "Testut", "AltStore"], "match": "contains",
                  "unsigned_ok": True},
}

# WinVerifyTrust の戻り値 (HRESULT)
TRUST_E_NOSIGNATURE = 0x800B0100
TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
_UNSIGNED_CODES = (TRUST_E_NOSIGNATURE, TRUST_E_SUBJECT_FORM_UNKNOWN, TRUST_E_PROVIDER_UNKNOWN, 0x80070002, 0x80070003)


def classify_trust_result(ret, last_error):
    """WinVerifyTrust の結果 → "valid" / "unsigned" (署名なし) / "invalid" (署名はあるが無効・改ざん・失効など)。"""
    if (ret & 0xFFFFFFFF) == 0:
        return "valid"
    if (last_error & 0xFFFFFFFF) in _UNSIGNED_CODES or (ret & 0xFFFFFFFF) in _UNSIGNED_CODES:
        return "unsigned"
    return "invalid"


def _signer_matches(policy, signers):
    names = [n.strip().casefold() for n in signers if isinstance(n, str) and n.strip()]
    for want in policy["names"]:
        w = want.casefold()
        for n in names:
            if policy["match"] == "exact":
                if n == w:
                    return True
            # 部分一致は語順を問わない (例: 実際の署名者は "BUREL VINCENT Entrepreneur individuel")
            elif w in n or all(tok in n.split() for tok in w.split()):
                return True
    return False


def evaluate_signature(cid, info):
    """署名確認の結果 info = {"result": "valid"/"unsigned"/"invalid"/"error", "signers": [名前...], "code": int}
    から実行してよいかを決める。戻り値: (許可するか, 日本語メッセージ, 署名なしで許可したか)"""
    policy = SIGNER_POLICY.get(cid)
    if policy is None:
        return False, "このインストーラーは署名の確認対象として登録されていないため、実行しませんでした", False
    result = (info or {}).get("result")
    if result == "valid":
        if _signer_matches(policy, (info or {}).get("signers") or []):
            return True, "署名を確認しました（%s）" % policy["publisher"], False
        found = ", ".join(n for n in (info.get("signers") or []) if n) or "不明"
        return False, ("インストーラーの署名者が想定と違うため、実行しませんでした（想定: %s / 実際: %s）。"
                       "ダウンロードをやり直してください" % (policy["publisher"], found)), False
    if result == "unsigned":
        if policy["unsigned_ok"]:
            return True, "署名のないファイルです（%s は署名なしで配布されています）。ファイルの改ざん検知のみで実行します" % policy["publisher"], True
        return False, "インストーラーに電子署名がないため、実行しませんでした（%s の署名が必要です）" % policy["publisher"], False
    if result == "invalid":
        return False, "インストーラーの電子署名が無効です（改ざんされているか、証明書を確認できません）。実行しませんでした", False
    return False, "インストーラーの署名を確認できなかったため、実行しませんでした (%s)" % ((info or {}).get("detail") or "不明なエラー"), False


def _wintrust():
    """ctypes で WinVerifyTrust 周りの定義を作る (Windows のみ)。"""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pcwszFilePath", ctypes.c_wchar_p),
                    ("hFile", ctypes.c_void_p), ("pgKnownSubject", ctypes.c_void_p)]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", ctypes.c_uint32),
                    ("fdwRevocationChecks", ctypes.c_uint32), ("dwUnionChoice", ctypes.c_uint32),
                    ("pFile", ctypes.c_void_p), ("dwStateAction", ctypes.c_uint32),
                    ("hWVTStateData", ctypes.c_void_p), ("pwszURLReference", ctypes.c_wchar_p),
                    ("dwProvFlags", ctypes.c_uint32), ("dwUIContext", ctypes.c_uint32),
                    ("pSignatureSettings", ctypes.c_void_p)]

    class CRYPT_PROVIDER_CERT(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_uint32), ("pCert", ctypes.c_void_p)]

    return ctypes, wintypes, GUID, WINTRUST_FILE_INFO, WINTRUST_DATA, CRYPT_PROVIDER_CERT


def verify_authenticode(path):
    """WinVerifyTrust (WINTRUST_ACTION_GENERIC_VERIFY_V2 / 失効確認なし / UI なし) で署名を検証し、署名者名を読む。
    戻り値: {"result": "valid"/"unsigned"/"invalid"/"error", "code": 戻り値, "signers": [CN, O], "detail": str}。
    Windows 以外・例外のときは "error" (呼び出し側は実行しない)。"""
    if os.name != "nt":
        return {"result": "error", "code": 0, "signers": [], "detail": "Windows 以外では確認できません"}
    try:
        ctypes, wintypes, GUID, FILE_INFO, WT_DATA, PROV_CERT = _wintrust()
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        wintrust.WinVerifyTrust.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_void_p]
        wintrust.WinVerifyTrust.restype = ctypes.c_int32
        wintrust.WTHelperProvDataFromStateData.argtypes = [ctypes.c_void_p]
        wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
        wintrust.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        wintrust.WTHelperGetProvCertFromChain.restype = ctypes.POINTER(PROV_CERT)
        crypt32.CertGetNameStringW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                               ctypes.c_wchar_p, ctypes.c_uint32]
        crypt32.CertGetNameStringW.restype = ctypes.c_uint32

        action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        fi = FILE_INFO(ctypes.sizeof(FILE_INFO), os.path.abspath(path), None, None)
        data = WT_DATA()
        data.cbStruct = ctypes.sizeof(WT_DATA)
        data.dwUIChoice = 2            # WTD_UI_NONE
        data.fdwRevocationChecks = 0   # WTD_REVOKE_NONE
        data.dwUnionChoice = 1         # WTD_CHOICE_FILE
        data.pFile = ctypes.cast(ctypes.pointer(fi), ctypes.c_void_p)
        data.dwStateAction = 1         # WTD_STATEACTION_VERIFY (署名者を読むため、状態を残す)
        INVALID = ctypes.c_void_p(-1)  # INVALID_HANDLE_VALUE (デスクトップのウィンドウを使わない)
        ctypes.set_last_error(0)
        ret = wintrust.WinVerifyTrust(INVALID, ctypes.byref(action), ctypes.byref(data))
        last = ctypes.get_last_error()
        signers = []
        try:
            if data.hWVTStateData:
                prov = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
                sgnr = wintrust.WTHelperGetProvSignerFromChain(prov, 0, 0, 0) if prov else None
                cert = wintrust.WTHelperGetProvCertFromChain(sgnr, 0) if sgnr else None
                ctx = cert.contents.pCert if cert else None
                if ctx:
                    for kind, para in ((4, None), (3, ctypes.cast(ctypes.c_char_p(b"2.5.4.10"), ctypes.c_void_p))):
                        buf = ctypes.create_unicode_buffer(512)
                        n = crypt32.CertGetNameStringW(ctx, kind, 0, para, buf, 512)
                        if n > 1 and buf.value:
                            signers.append(buf.value)
        finally:
            data.dwStateAction = 2     # WTD_STATEACTION_CLOSE
            wintrust.WinVerifyTrust(INVALID, ctypes.byref(action), ctypes.byref(data))
        return {"result": classify_trust_result(ret, last), "code": ret & 0xFFFFFFFF, "signers": signers, "detail": ""}
    except Exception as e:
        return {"result": "error", "code": 0, "signers": [], "detail": str(e)}


def check_installer_signature(cid, path, verifier=None):
    """実行前の署名確認。戻り値: (許可するか, 日本語メッセージ, 署名なしで許可したか)。verifier は差し替え用。"""
    try:
        info = (verifier or verify_authenticode)(path)
    except Exception as e:
        info = {"result": "error", "detail": str(e), "signers": []}
    return evaluate_signature(cid, info)


# ---------------------------------------------------------------------------
# ジョブ (管理者権限のヘルパーが実行する内容)
# ---------------------------------------------------------------------------

def step_register_camera():
    return {"id": "camera", "type": "register_camera", "title": "PhoneCamカメラを登録"}


def step_exe(cid, title, path, args, ok_codes=(0, 3010, 1641), timeout=900, retry_args=None, lenient=False, reboot=False,
             stage_dir=None):
    # stage_dir: 同じフォルダのほかのファイル (ドライバなど) も必要なインストーラーは、フォルダごと安全な場所へコピーして実行する
    return {"id": cid, "type": "exe", "title": title, "path": path, "args": list(args), "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout, "retry_args": retry_args, "lenient": lenient, "reboot": reboot,
            "stage_dir": stage_dir}


def step_msi(cid, title, path, ok_codes=(0, 3010, 1641), timeout=900, stage_dir=None):
    return {"id": cid, "type": "msi", "title": title, "path": path, "sha256": sha256_file(path),
            "ok_codes": list(ok_codes), "timeout": timeout, "stage_dir": stage_dir}


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


def _stage_for_run(path, stage_dir=None):
    """管理者権限で実行するファイルを、一般ユーザーが書き換えられない場所 (ProgramData 以下に管理者が作った
    フォルダ) へコピーする。確認 (SHA-256・署名) と実行の間に差し替えられるのを防ぐため。
    戻り値: (実行するパス, 後始末する関数)。Windows 以外 (テスト) ではそのまま使う。"""
    if os.name != "nt":
        return path, (lambda: None)
    import uuid
    base = os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "PhoneCam", "run-" + uuid.uuid4().hex)
    os.makedirs(base)
    if stage_dir and os.path.isdir(stage_dir):
        src_root = os.path.abspath(stage_dir)
        dst_root = os.path.join(base, "pkg")
        shutil.copytree(src_root, dst_root)
        rel = os.path.relpath(os.path.abspath(path), src_root)
        if rel.startswith(".."):
            raise RuntimeError("インストーラーの場所が不正です")
        run_path = os.path.join(dst_root, rel)
    else:
        run_path = os.path.join(base, os.path.basename(path))
        shutil.copy2(path, run_path)
    return run_path, (lambda: shutil.rmtree(base, ignore_errors=True))


def _msiexec_path():
    if os.name == "nt":
        return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "msiexec.exe")
    return "msiexec"


# winget で入れてよいパッケージ (ジョブファイルに別のIDが書かれていても実行しない)
ALLOWED_WINGET = frozenset(v["winget"] for v in SOURCES.values() if v.get("winget"))


def _run_installer(step, kind, path, res, runner, verifier):
    """コピー済みのインストーラー (path) を、SHA-256 → 電子署名の順に確認して実行する。"""
    if sha256_file(path) != step["sha256"]:
        res["detail"] = "インストーラーが変更されていたため実行しませんでした"
        return res
    allowed, message, unsigned = check_installer_signature(step.get("id"), path, verifier)
    res["signature"] = message
    res["unsigned"] = unsigned
    if not allowed:
        res["detail"] = message
        return res
    if kind == "msi":
        cmd = [_msiexec_path(), "/i", path, "/qn", "/norestart"]
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
        if code == 1603 and step.get("id") in STORE_PACKAGES:
            res["detail"] += "。Microsoft Store版が残っていると失敗します。設定 > アプリ からStore版をアンインストールして、もう一度お試しください"
    return res


def execute_step(step, runner=None, verifier=None):
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
            if not step.get("sha256"):
                res["detail"] = "インストーラーの検証情報がないため実行しませんでした"
                return res
            # 一般ユーザーが触れない場所へコピーしてから、その実物を確認して実行する
            path, cleanup = _stage_for_run(path, step.get("stage_dir"))
            try:
                return _run_installer(step, kind, path, res, runner, verifier)
            finally:
                cleanup()
        if kind == "winget":
            if step.get("pkg") not in ALLOWED_WINGET:
                res["detail"] = "許可されていないwingetパッケージのため実行しませんでした"
                return res
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


def run_job(job_path, expected_sha=None, runner=None, verifier=None):
    """管理者権限で実行される --setup。結果を result.json へ書き、終了コードを返す。
    0 = 全て成功 / 3010 = 成功・要再起動 / 1 = 失敗あり。"""
    result = {"steps": [], "error": ""}
    result_path = None
    try:
        # 管理者権限で何を実行するかは job.json で決まるので、呼び出し側が渡したハッシュと一致するものだけ実行する
        # (ハッシュが渡されていない場合も実行しない)
        if not expected_sha:
            raise RuntimeError("ジョブファイルの検証情報がないため実行しませんでした")
        if sha256_file(job_path).lower() != str(expected_sha).strip().lower():
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
            result["steps"].append(execute_step(step, runner=runner, verifier=verifier))
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
            # 「要確認」(Store版など) も公式サイト版に入れ替える
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
        self.store_remover = remove_store_package
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
                return step_msi(cid, title, msi, stage_dir=root)
            exe = find_file(root, ["setup.exe"])
            if exe:
                # 無人インストールが効かない場合は、通常のウィンドウ付きでもう一度実行する
                return step_exe(cid, title, exe, ["/quiet", "/norestart"], retry_args=[], stage_dir=root)
            raise DownloadError("zipの中にインストーラーが見つかりませんでした")
        if cid == "vbcable":
            root = safe_extract_zip(dest, os.path.join(self.dir, "vbcable"))
            exe = find_file(root, ["vbcable_setup_x64.exe", "vbcable_setup.exe"])
            if not exe:
                raise DownloadError("zipの中にインストーラーが見つかりませんでした")
            # VB-CABLE は終了コードがあてにならないので、導入後の再検出で判定する
            return step_exe(cid, title, exe, ["-i", "-h"], timeout=300, lenient=True, reboot=True, stage_dir=root)
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

        # Store 版は公式サイト版と共存できないので、管理者ジョブの前に通常権限で削除する (Store アプリはユーザー単位のため)
        kept = []
        for st in steps:
            cid = st.get("id")
            if cid in STORE_PACKAGES and store_package_installed(cid):
                name = COMPONENT_BY_ID[cid]["name"]
                emit("stage", text="%s のMicrosoft Store版を削除しています…" % name)
                ok, msg = self.store_remover(cid)
                emit("log", text="%s: %s" % (name, msg))
                if not ok:
                    out["results"][cid] = {"ok": False, "detail": msg, "code": None}
                    continue
            kept.append(st)
        steps = kept

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

    def __init__(self, root, engine=None, detector=None, can_start=None, on_busy=None, on_changed=None,
                 audio_guard=None):
        self.root = root
        self.audio_guard = audio_guard  # VB-CABLE の導入で既定の音声デバイスが変わるのを戻す (phonecam_audio.RestoreGuard)
        self.audio_message = ""
        self.audio_polling = False
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
        if (self.busy or self.checking or self.audio_polling) and not self.closed:
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
        elif kind == "audio":
            self.audio_polling = False
            self.audio_message = (payload or {}).get("message", "")
            self._notify("audio")
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
        guard = self.audio_guard if "vbcable" in ids else None
        if guard is not None:
            try:
                guard.before_install()  # 通常権限のこのプロセスで、現在の既定の音声デバイスを控える
            except Exception:
                guard = None
        self.audio_message = ""
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
            if guard is not None:
                self.audio_polling = True  # "done" より先に立てる (UI の更新ループを止めないため)
            self._q.put(("done", out))
            if guard is not None:
                try:
                    res = guard.after_install()
                except Exception:
                    res = {"message": ""}
                self._q.put(("audio", res))

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

# 画面の拡大率 (DPI / 96)。フォントは tk の scaling でそろうが、ピクセル指定の余白・幅は px() で拡大する
SCALE = 1.0


def set_scale(f):
    global SCALE
    try:
        SCALE = max(1.0, min(3.0, float(f)))
    except Exception:
        SCALE = 1.0


def px(n):
    return max(1, int(round(n * SCALE)))


def detect_scale(root):
    """tk の実際の DPI から拡大率を求める (取れなければ 1.0)。"""
    try:
        return float(root.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0


def make_button(parent, text, command, primary=False, state="normal"):
    """角のない平らなボタン。"""
    if primary:
        b = tk.Button(parent, text=text, command=command, bg=ACCENT, fg="#ffffff", activebackground="#0077ed",
                      activeforeground="#ffffff", relief="flat", bd=0, padx=px(22), pady=px(7), cursor="hand2",
                      font=(FONT, 10, "bold"), disabledforeground="#c7c7cc", state=state)
    else:
        b = tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG, activebackground="#e8e8ed",
                      activeforeground=FG, relief="flat", bd=0, padx=px(16), pady=px(6), cursor="hand2",
                      font=(FONT, 10), disabledforeground="#c7c7cc", state=state)
    return b


def make_link(parent, text, command, bg=BG):
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=SUB, activebackground=bg, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2", font=(FONT, 9), padx=px(4), pady=px(2))
    return b


def bind_wrap(label, container, reserve=0, minimum=140):
    """label の wraplength を container の実際の幅に合わせる (固定ピクセルにしない)。
    reserve はほかの列が使う幅 (数値、または呼ぶと数値を返す関数)。"""
    def fit(_e=None):
        try:
            w = container.winfo_width()
            if w > 1:
                r = reserve() if callable(reserve) else reserve
                label.configure(wraplength=max(px(minimum), int(w - r)))
        except Exception:
            pass
    container.bind("<Configure>", fit, add="+")
    return fit


def wrapped_label(parent, text, container=None, reserve=0, **kw):
    """折り返すラベル。初期の wraplength を持たせ、container の幅に追従させる。"""
    kw.setdefault("bg", BG)
    kw.setdefault("fg", SUB)
    kw.setdefault("font", (FONT, 10))
    kw.setdefault("justify", "left")
    kw.setdefault("anchor", "w")
    lbl = tk.Label(parent, text=text, wraplength=px(440), **kw)
    if container is not None:
        bind_wrap(lbl, container, reserve)
    return lbl


class ThinProgress:
    """細い直線のプログレスバー。"""

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, height=px(4), bg=PANEL, highlightthickness=0, bd=0)
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
            self.canvas.create_rectangle(0, 0, int(w * self.value), px(4), fill=ACCENT, width=0)
        except Exception:
            pass


class ScrollFrame:
    """縦にスクロールできる領域 (Canvas + 内側の Frame)。中身が収まるときはスクロールバーを隠す。"""

    def __init__(self, parent, bg=BG):
        self.outer = tk.Frame(parent, bg=bg)
        self.canvas = tk.Canvas(self.outer, bg=bg, highlightthickness=0, bd=0, yscrollincrement=px(24))
        self.vsb = tk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar_visible = False
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _e=None):
        try:
            self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        except Exception:
            pass

    def _on_canvas(self, e):
        try:
            self.canvas.itemconfigure(self._win, width=e.width)
        except Exception:
            pass

    def _on_yscroll(self, lo, hi):
        try:
            self.vsb.set(lo, hi)
            need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
            if need and not self.bar_visible:
                self.vsb.pack(side="right", fill="y")
                self.bar_visible = True
            elif not need and self.bar_visible:
                self.vsb.pack_forget()
                self.bar_visible = False
        except Exception:
            pass

    def wheel(self, e):
        try:
            if self.bar_visible:
                self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        except Exception:
            pass

    def to_top(self):
        try:
            self.canvas.yview_moveto(0)
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
            tk.Label(self.frame, text="確認しています…", bg=BG, fg=SUB, font=(FONT, 10)).pack(anchor="w", pady=px(20))
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
        inner.pack(fill="x", padx=px(2), pady=px(9))
        # 列: 0 = 状態アイコン (固定) / 1 = 文章 (伸びる) / 2 = チェックやボタン (固定)
        inner.columnconfigure(0, minsize=px(34))
        inner.columnconfigure(1, weight=1)

        if res is not None:
            icon, color, word = ("✓", GOOD, "完了") if res.get("ok") else ("✗", BAD, "失敗")
        else:
            icon, color, word = ICONS.get(state, ICONS[STATE_WARN])
        icon_lbl = tk.Label(inner, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold"))
        icon_lbl.grid(row=0, column=0, sticky="nw")
        action = tk.Frame(inner, bg=BG)
        action.grid(row=0, column=2, sticky="ne", padx=(px(10), 0))
        text_col = tk.Frame(inner, bg=BG)
        text_col.grid(row=0, column=1, sticky="ew")
        head = tk.Frame(text_col, bg=BG)
        head.pack(anchor="w")
        tk.Label(head, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text="  " + word, fg=color, bg=BG, font=(FONT, 9)).pack(side="left")
        detail = (res or {}).get("detail") if res is not None else st.get("detail", "")

        def reserve():
            return px(34) + action.winfo_reqwidth() + px(24)

        wrapped_label(text_col, comp["purpose"], inner, reserve, font=(FONT, 9)).pack(fill="x")
        if detail:
            wrapped_label(text_col, detail, inner, reserve, font=(FONT, 9),
                          fg=(BAD if res is not None else WARN)).pack(fill="x")
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


def find_itunes():
    return _any_file([os.path.join("iTunes", "iTunes.exe")])


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else tempfile.gettempdir()


def _open_folder_select(path):
    """エクスプローラーでファイルの場所を開く (失敗したらフォルダだけ開く)。"""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
    except Exception:
        pass
    _startfile(os.path.dirname(path))


# 画面ページの番号: 0 ようこそ / 1 確認 / 2 インストール / 3〜9 iPhone の手順 (7つ)
PAGE_WELCOME, PAGE_CHECK, PAGE_INSTALL, PAGE_IPHONE = 0, 1, 2, 3
POLL_MS = 2000


class SetupWizard:
    """初回セットアップ / やり直し用のウィザード (リサイズ可能な Toplevel)。
    start="iphone" のときは iPhone の手順だけを開く (セットアップタブの「iPhoneのセットアップ」)。"""

    TITLES = ["ようこそ", "必要なソフトの確認", "インストール中", "iPhoneの準備"]

    def __init__(self, root, controller, on_finish, on_skip=None, open_url=None, launch=None, start="setup"):
        self.root = root
        self.controller = controller
        self.on_finish = on_finish
        self.on_skip = on_skip or on_finish
        self.open_url = open_url or webbrowser.open
        self.launch = launch or _startfile
        self.mode = start
        self.page = 0
        self.finished = False
        self._placed = False
        set_scale(detect_scale(root))
        self.top = tk.Toplevel(root)
        self.top.title("PhoneCam セットアップ")
        self.top.configure(bg=BG)
        try:
            self.top.minsize(px(520), px(440))
            self.top.resizable(True, True)
            self.top.transient(root)
            self.top.protocol("WM_DELETE_WINDOW", self._close)
        except Exception:
            pass
        # フッター (ボタン) は一番下に固定。本文だけがスクロールする
        self.footer_wrap = tk.Frame(self.top, bg=BG)
        self.footer_wrap.pack(side="bottom", fill="x")
        tk.Frame(self.footer_wrap, bg=LINE, height=1).pack(fill="x")
        self.footer = tk.Frame(self.footer_wrap, bg=BG)
        self.footer.pack(fill="x", padx=px(28), pady=px(14))
        self.scroll = ScrollFrame(self.top)
        self.scroll.outer.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(28), pady=(px(24), px(10)))
        try:
            self.top.bind("<MouseWheel>", self.scroll.wheel)
        except Exception:
            pass
        self.panel = None
        self.log_visible = False
        self.log_text = None
        self._listener = self._on_controller
        controller.listen(self._listener)
        # iPhone ページの状態
        self.snap = {}
        self.app_ok = False
        self._visited = set()
        self._first = False
        self._probing = False
        self._probe_result = None
        self._next_probe = 0.0
        self._pump_job = None
        self._dl_state = None
        self.show(PAGE_IPHONE if start == "iphone" else PAGE_WELCOME)
        try:
            self.top.grab_set()
        except Exception:
            pass

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
        self.audio_label = None
        self.chip = None
        self.dl_label = None
        self.dl_btn = None

    def _title(self, text, sub=None):
        tk.Label(self.body, text=text, bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w").pack(anchor="w")
        if sub:
            wrapped_label(self.body, sub, self.body).pack(fill="x", pady=(px(6), px(14)))

    def show(self, index):
        self.page = index
        self._clear()
        if index >= PAGE_IPHONE:
            self._page_step(index - PAGE_IPHONE)
        else:
            [self._page_welcome, self._page_check, self._page_install][index]()
        self.scroll.to_top()
        self._fit_window()
        if index >= PAGE_IPHONE:
            self._start_pump()

    def _fit_window(self):
        """中身の大きさに合わせてウィンドウを大きくする (画面の85%まで。小さくはしない)。"""
        try:
            self.top.update_idletasks()
            pad_w = 2 * px(28) + px(24)
            need_w = max(self.body.winfo_reqwidth() + pad_w, self.footer_wrap.winfo_reqwidth(), px(560))
            need_h = (self.body.winfo_reqheight() + px(24) + px(10) + self.footer_wrap.winfo_reqheight())
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            cur_w, cur_h = self.top.winfo_width(), self.top.winfo_height()
            if not self._placed:
                cur_w = cur_h = 1
            w = min(max(cur_w, need_w), int(sw * 0.85))
            h = min(max(cur_h, need_h), int(sh * 0.85))
            if not self._placed:
                try:
                    rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                    rw, rh = self.root.winfo_width(), self.root.winfo_height()
                except Exception:
                    rx = ry = 0
                    rw, rh = sw, sh
                x = max(0, min(sw - w, rx + (rw - w) // 2))
                y = max(0, min(sh - h - px(40), ry + max(0, (rh - h) // 3)))
                self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
                self._placed = True
            elif (w, h) != (cur_w, cur_h):
                self.top.geometry("%dx%d" % (w, h))
        except Exception:
            pass

    # -- ページ1 ----------------------------------------------------------
    def _page_welcome(self):
        tk.Label(self.body, text="PhoneCam へようこそ", bg=BG, fg=FG, font=(FONT, 22, "bold"), anchor="w").pack(
            anchor="w", pady=(px(36), 0))
        wrapped_label(self.body, "iPhoneをOBSのカメラにするために必要なソフトを確認して、足りないものをまとめて入れます。\n"
                                 "すでに入っているものは飛ばします。管理者の確認は最初に1回だけ表示されます。\n"
                                 "そのあと、iPhoneにPhoneCamを入れる手順もご案内します。",
                      self.body, font=(FONT, 11)).pack(fill="x", pady=(px(16), 0))
        make_link(self.footer, "スキップ", self._skip).pack(side="left")
        make_button(self.footer, "次へ", lambda: self.show(PAGE_CHECK), primary=True).pack(side="right")

    # -- ページ2 ----------------------------------------------------------
    def _page_check(self):
        self._title("必要なソフトの確認")
        self.panel = SetupPanel(self.body, self.controller, "wizard")
        self.panel.pack(fill="x")
        self.mic_var = tk.BooleanVar(value=self.controller.mic)
        # VB-CABLE が導入済みなら選ぶ必要がないので表示しない (_sync_mic_option で出し入れする)
        self.mic_box = tk.Frame(self.body, bg=BG)
        tk.Checkbutton(self.mic_box, text="iPhoneをマイクとしても使う", variable=self.mic_var, bg=BG,
                       activebackground=BG, fg=FG, font=(FONT, 10), bd=0, highlightthickness=0,
                       command=self._mic_changed).pack(anchor="w")
        tk.Label(self.mic_box, text="OBSやDiscordでiPhoneの音声を使うための「VB-CABLE」（無料）を一緒に入れます。"
                                    "PCのマイクを使う場合は不要です。",
                 bg=BG, fg=SUB, font=(FONT, 9), justify="left", anchor="w",
                 wraplength=px(480)).pack(anchor="w", fill="x", padx=(px(22), 0))
        self._sync_mic_option()
        make_link(self.footer, "スキップ", self._skip).pack(side="left")
        self.next_btn = make_button(self.footer, "インストール", self._start_install, primary=True)
        self.next_btn.pack(side="right")
        make_button(self.footer, "再チェック", self.controller.recheck).pack(side="right", padx=(0, px(10)))
        self.panel.render()
        self._update_next_label()
        if not self.controller.checked:
            self.controller.recheck()

    def _sync_mic_option(self):
        box = getattr(self, "mic_box", None)
        if box is None:
            return
        try:
            installed = self.controller.statuses.get("vbcable", {}).get("state") == STATE_OK
            if installed:
                box.pack_forget()
            elif not box.winfo_ismapped():
                box.pack(anchor="w", fill="x", pady=(px(12), 0))
        except Exception:
            pass

    def _mic_changed(self):
        self.controller.set_mic(self.mic_var.get())
        self.panel.render()
        self._update_next_label()
        self._fit_window()

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
            self.show(PAGE_IPHONE)
            return
        if self.controller.start(ids):
            self.show(PAGE_INSTALL)

    # -- ページ3 ----------------------------------------------------------
    def _page_install(self):
        self._title("インストール中")
        self.stage_label = wrapped_label(self.body, self.controller.stage, self.body, fg=FG)
        self.stage_label.pack(fill="x")
        self.bar = ThinProgress(self.body)
        self.bar.pack(fill="x", pady=(px(8), px(6)))
        self.bar.set(self.controller.progress)
        self.detail_btn = make_link(self.body, "詳細 ▸", self._toggle_log)
        self.detail_btn.pack(anchor="w")
        self.log_frame = tk.Frame(self.body, bg=BG)
        self.log_text = tk.Text(self.log_frame, height=7, bg=PANEL, fg=SUB, relief="flat", bd=0, font=(FONT, 8),
                                wrap="word", highlightthickness=0, padx=px(8), pady=px(6))
        sb = tk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_visible = False
        self._refill_log()
        self.result_holder = tk.Frame(self.body, bg=BG)
        self.note_label = wrapped_label(self.body, "", self.body, fg=WARN)
        self.audio_label = wrapped_label(self.body, "", self.body, fg=SUB, font=(FONT, 9))
        self.next_btn = make_button(self.footer, "次へ", lambda: self.show(PAGE_IPHONE), primary=True, state="disabled")
        self.next_btn.pack(side="right")
        self.retry_btn = make_button(self.footer, "もう一度試す", self._retry)
        if not self.controller.busy and self.controller.outcome is not None:
            self._show_outcome()
        self._show_audio_note()

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(px(4), 0))
            self.detail_btn.config(text="詳細 ▾")
        else:
            self.log_frame.pack_forget()
            self.detail_btn.config(text="詳細 ▸")
        self._fit_window()

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
        self.show(PAGE_CHECK)

    def _show_audio_note(self):
        """VB-CABLE を入れたあとの、音の出力先に関する案内 (戻した / 戻せなかった)。"""
        msg = getattr(self.controller, "audio_message", "")
        if self.audio_label is None or not msg:
            return
        try:
            self.audio_label.config(text=msg)
            self.audio_label.pack(fill="x", pady=(px(8), 0))
            self._fit_window()
        except Exception:
            pass

    def _show_outcome(self):
        out = self.controller.outcome or {}
        if self.stage_label is not None:
            self.stage_label.config(text="完了しました" if not out.get("error") and all(
                r.get("ok") for r in out.get("results", {}).values()) else "一部のインストールが完了しませんでした")
        if self.bar is not None:
            self.bar.set(1.0)
        self._render_results(out)
        self.result_holder.pack(fill="x", pady=(px(8), 0))
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        if out.get("reboot"):
            notes.append("再起動が必要です。セットアップ後にPCを再起動してください。")
        for m in out.get("manual", []):
            notes.append("%s は自動で入れられませんでした。「%s」" % (m["name"], m["text"]))
        if notes:
            self.note_label.config(text="\n".join(notes))
            self.note_label.pack(fill="x", pady=(px(8), 0))
        for m in out.get("manual", []):
            url = m["url"]
            make_link(self.body, "%s の公式ページを開く" % m["name"], lambda u=url: self.open_url(u)).pack(anchor="w")
        if self.next_btn is not None:
            self.next_btn.config(state="normal")
        failed = [cid for cid, r in out.get("results", {}).items() if not r.get("ok")]
        if failed and self.retry_btn is not None:
            self.retry_btn.pack(side="right", padx=(0, px(10)))
        self._fit_window()

    def _render_results(self, out):
        """結果の行 (インストールした項目だけ) を描画する。"""
        ids = out.get("ids", [])
        for cid in ids:
            comp = COMPONENT_BY_ID[cid]
            r = out["results"].get(cid, {"ok": False, "detail": ""})
            row = tk.Frame(self.result_holder, bg=BG)
            row.pack(fill="x", pady=px(3))
            row.columnconfigure(1, weight=1)
            icon, color = ("✓", GOOD) if r.get("ok") else ("✗", BAD)
            tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="nw",
                                                                                   padx=(0, px(10)))
            col = tk.Frame(row, bg=BG)
            col.grid(row=0, column=1, sticky="ew")
            tk.Label(col, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
            if r.get("detail"):
                wrapped_label(col, r["detail"], row, px(40), fg=BAD if not r.get("ok") else WARN,
                              font=(FONT, 9)).pack(fill="x")

    # -- ページ4〜10: iPhone の手順 ----------------------------------------
    def _ctx(self):
        alt = bool(find_altserver())
        if not alt:
            try:
                alt = self.controller.statuses.get("altserver", {}).get("state") == STATE_OK
            except Exception:
                pass
        link = False
        fn = _host.get("link_connected")
        if fn:
            try:
                link = bool(fn())
            except Exception:
                link = False
        return {"altserver": alt, "app": self.app_ok, "link": link}

    def _page_step(self, i):
        steps = iphone.IPHONE_STEPS
        step = steps[i]
        sid = step["id"]
        self._step_id = sid
        self._step_index = i
        self._first = sid not in self._visited
        self._visited.add(sid)
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=str(i + 1), bg=BG, fg=ACCENT, font=(FONT, 40, "bold")).pack(side="left")
        tk.Label(head, text="全%d段階" % len(steps), bg=BG, fg=SUB, font=(FONT, 9)).pack(side="right", anchor="s")
        tk.Label(self.body, text=step["title"], bg=BG, fg=FG, font=(FONT, 18, "bold"), anchor="w").pack(anchor="w")
        for line in step["lines"]:
            wrapped_label(self.body, line, self.body, font=(FONT, 10)).pack(fill="x", pady=(px(6), 0))
        self.chip = tk.Label(self.body, text="確認しています…", bg=PANEL, fg=SUB, font=(FONT, 10), padx=px(12),
                             pady=px(5), anchor="w")
        self.chip.pack(anchor="w", pady=(px(16), px(12)))
        extra = getattr(self, "_extra_" + sid, None)
        if extra:
            extra()
        # フッター
        make_link(self.footer, "あとで", self._skip_iphone).pack(side="left")
        last = i == len(steps) - 1
        if last:
            self.next_btn = make_button(self.footer, "完了", self._finish, primary=True, state="disabled")
        else:
            self.next_btn = make_button(self.footer, "次へ", lambda: self.show(self.page + 1), primary=True)
        self.next_btn.pack(side="right")
        if not (i == 0 and self.mode == "iphone"):
            make_button(self.footer, "戻る", self._back).pack(side="right", padx=(0, px(10)))
        self._render_chip()

    def _back(self):
        if self.page == PAGE_IPHONE:
            self.controller.recheck()
            self.show(PAGE_CHECK)
        else:
            self.show(self.page - 1)

    def _btn_row(self, items):
        """ボタンを2つずつ並べる (幅が足りなくても切れない)。items = [(text, command, enabled)]"""
        row = None
        for n, (text, cmd, enabled) in enumerate(items):
            if n % 2 == 0:
                row = tk.Frame(self.body, bg=BG)
                row.pack(anchor="w", pady=(0, px(8)))
            b = make_button(row, text, cmd, state="normal" if enabled else "disabled")
            b.pack(side="left", padx=(0, px(10)))

    def _extra_itunes(self):
        path = find_itunes()
        self._btn_row([("iTunesを開く", lambda: self.launch(path), bool(path))])
        if not path:
            wrapped_label(self.body, "iTunesが見つかりません。前の画面でインストールできます。", self.body,
                          font=(FONT, 9)).pack(fill="x")

    def _extra_altstore(self):
        path = find_altserver()
        self._btn_row([("AltServerを起動", lambda: self.launch(path), bool(path)),
                       ("公式の手順を開く", lambda: self.open_url(FAQ_URL), True)])
        wrapped_label(self.body, "うまくいかないときは、AltServerを「管理者として実行」してみてください。",
                      self.body, font=(FONT, 9)).pack(fill="x")

    def _extra_phonecam(self):
        repo = APP_REPO
        url = iphone.source_url(repo)
        deeplink = iphone.source_deeplink(repo)
        if url and phonecam_qr is not None:
            box = tk.Frame(self.body, bg=BG)
            box.pack(anchor="w", pady=(0, px(8)))
            cv = tk.Canvas(box, highlightthickness=0, bd=0, bg="#ffffff")
            try:
                phonecam_qr.draw_on_canvas(cv, phonecam_qr.make_qr(deeplink, "M"), cell=px(4), quiet=3)
                cv.pack(side="left")
            except Exception:
                cv.destroy()
            side = tk.Frame(box, bg=BG)
            side.pack(side="left", padx=(px(16), 0), anchor="n")
            wrapped_label(side, "iPhoneのカメラでQRコードを読み取り、AltStoreで開きます。", self.body, px(260),
                          font=(FONT, 9)).pack(fill="x")
            wrapped_label(side, "追加できたら、AltStoreで「PhoneCam」を入手します。", self.body, px(260),
                          font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
            ent = tk.Entry(self.body, font=(FONT, 9), relief="flat", bg=PANEL, fg=FG, readonlybackground=PANEL,
                           highlightthickness=0, bd=0)
            ent.insert(0, url)
            ent.configure(state="readonly")
            ent.pack(fill="x", ipady=px(6), pady=(0, px(8)))
            self._btn_row([("URLをコピー", lambda: self._copy(url), True)])
        wrapped_label(self.body, "QRが使えないときは、PhoneCam.ipaを直接入れる方法もあります。AltServerのアイコンを"
                                 "Shiftキーを押しながらクリック → 「Sideload .ipa…」→ 保存したPhoneCam.ipa を選びます。"
                                 "（この方法だと、アプリの更新は自分で入れ直します）",
                      self.body, font=(FONT, 9)).pack(fill="x")
        wrapped_label(self.body, "インストール時に「AltServer.ServerError 2005」が出たときは、AltStoreの「My Apps」で"
                                 "AltStoreを一度「Refresh」してから、もう一度インストールしてください。",
                      self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
        ipa = ipa_url()
        self.dl_btn = make_button(self.body, "PhoneCam.ipaをダウンロード", self._download_ipa,
                                  state="normal" if ipa else "disabled")
        self.dl_btn.pack(anchor="w", pady=(px(8), 0))
        self.dl_label = wrapped_label(self.body, "", self.body, font=(FONT, 9))
        self.dl_label.pack(fill="x", pady=(px(4), 0))
        if self._dl_state and self._dl_state[0] == "ok":
            self.dl_label.config(text="保存しました: %s" % self._dl_state[1])

    def _copy(self, text):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            if self.chip is not None:
                self.chip.config(text="✓ URLをコピーしました", fg=GOOD)
        except Exception:
            pass

    def _download_ipa(self):
        url = ipa_url()
        if not url or (self._dl_state and self._dl_state[0] == "busy"):
            return
        self._dl_state = ("busy", "")
        if self.dl_btn is not None:
            self.dl_btn.config(state="disabled")
        if self.dl_label is not None:
            self.dl_label.config(text="ダウンロード中…", fg=SUB)
        dest = os.path.join(downloads_dir(), "PhoneCam.ipa")

        def work():
            try:
                download_file(url, dest, "zip")
                self._dl_state = ("ok", dest)
            except Exception as e:
                self._dl_state = ("err", str(e) if isinstance(e, DownloadError) else friendly_net_error(e))

        threading.Thread(target=work, name="ipa-download", daemon=True).start()
        self.top.after(300, self._dl_pump)

    def _dl_pump(self):
        if self.finished:
            return
        st = self._dl_state
        if st and st[0] == "busy":
            self.top.after(300, self._dl_pump)
            return
        try:
            if self.dl_btn is not None:
                self.dl_btn.config(state="normal")
            if st and st[0] == "ok":
                if self.dl_label is not None:
                    self.dl_label.config(text="保存しました: %s" % st[1], fg=GOOD)
                _open_folder_select(st[1])
            elif st and self.dl_label is not None:
                self.dl_label.config(text=st[1], fg=BAD)
        except Exception:
            pass

    # -- 状態の自動更新 (2秒ごと) ------------------------------------------
    def _start_pump(self):
        self._next_probe = 0.0
        if self._pump_job is None:
            self._pump_job = self.top.after(200, self._pump)

    def _pump(self):
        self._pump_job = None
        if self.finished or self.page < PAGE_IPHONE:
            return
        try:
            if self._probe_result is not None:
                snap, app = self._probe_result
                self._probe_result = None
                self.snap, self.app_ok = snap, app
                self._next_probe = time.monotonic() + POLL_MS / 1000.0
                if self._apply():
                    return
            if not self._probing and time.monotonic() >= self._next_probe:
                self._probing = True
                want_app = getattr(self, "_step_id", "") == "test"
                linked = bool(self._ctx().get("link"))

                def work():
                    try:
                        snap = iphone.query_usbmux()
                        app = False
                        if want_app and not linked:
                            app = iphone.probe_phonecam(snap)
                        self._probe_result = (snap, app)
                    except Exception:
                        self._probe_result = ({}, False)
                    finally:
                        self._probing = False

                threading.Thread(target=work, name="iphone-probe", daemon=True).start()
        except Exception:
            pass
        self._pump_job = self.top.after(250, self._pump)

    def _render_chip(self):
        if self.chip is None:
            return
        state, text = iphone.step_status(self._step_id, self.snap, self._ctx())
        if state == iphone.DONE:
            self.chip.config(text="✓ " + text, fg=GOOD)
        elif state == iphone.WAIT:
            self.chip.config(text="● " + text, fg=SUB)
        elif state == iphone.MANUAL:
            self.chip.config(text="○ " + text, fg=SUB)
        else:
            self.chip.config(text="! " + text, fg=WARN)
        if self._step_id == "test" and self.next_btn is not None:
            self.next_btn.config(state="normal" if state == iphone.DONE else "disabled")
        return state

    def _apply(self):
        """新しい検出結果を画面へ。自動で次へ進んだら True。"""
        state = self._render_chip()
        if self._first and iphone.should_auto_advance(self._step_id, state, set()):
            self.show(self.page + 1)
            return True
        return False

    # -- 終了 -------------------------------------------------------------
    def _finish(self):
        self._end(self.on_finish)

    def _skip(self):
        self._end(self.on_skip)

    def _skip_iphone(self):
        self._end(self.on_finish if self.mode == "setup" else self.on_skip)

    def _close(self):
        if self.controller.busy:
            return
        if self.page >= PAGE_IPHONE:
            self._skip_iphone()
        else:
            self._skip()

    def _end(self, cb):
        if self.finished:
            return
        self.finished = True
        self.controller.unlisten(self._listener)
        if self._pump_job is not None:
            try:
                self.top.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
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
            if self.page == PAGE_CHECK and self.panel is not None:
                self.panel.render()
                self._sync_mic_option()
                self._update_next_label()
                self._fit_window()
        elif what == "stage" and self.stage_label is not None:
            self.stage_label.config(text=self.controller.stage)
        elif what == "progress" and self.bar is not None:
            self.bar.set(self.controller.progress)
        elif what == "log":
            self._refill_log()
        elif what == "audio" and self.page == PAGE_INSTALL:
            self._show_audio_note()
        elif what == "done" and self.page == PAGE_INSTALL:
            self._show_outcome()


def _startfile(path):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass
