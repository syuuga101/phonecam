# -*- coding: utf-8 -*-
"""iPhone のセットアップ案内 (ウィザードの手順ページ用) の、画面に依存しない部分。

- usbmux (Apple Mobile Device Service) への問い合わせ: 接続の有無 / 信頼 (ペアリング) 済みか / PhoneCam アプリに届くか
- 各手順の状態 (✓ / 待機 / 手動確認) の判定
- AltStore のソース URL と、追加用ディープリンク
"""

import os
import plistlib
import socket
import struct
import subprocess

USBMUX_ADDR = ("127.0.0.1", 27015)
PHONE_PORT = 5555
CLIENT = {"ClientVersionString": "phonecam", "ProgName": "phonecam"}

ALTSTORE_SOURCE_FILE = "altstore-source.json"
ALTSTORE_BUNDLE_ID = "com.example.phonecam"  # ios/project.yml の PRODUCT_BUNDLE_IDENTIFIER と同じ

# 状態
DONE = "done"        # ✓
WAIT = "wait"        # まだ (自動で更新される)
MANUAL = "manual"    # 自動では確認できない (利用者が目で見て進める)
UNKNOWN = "unknown"  # 確認に失敗
ISSUE = "issue"      # 要対応 (⚠ オレンジ表示)
ALTSERVER_IMAGE = "AltServer.exe"


# ---------------------------------------------------------------------------
# usbmux
# ---------------------------------------------------------------------------

def pack_message(payload, tag):
    body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    return struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body


def build_list_devices(tag=1):
    msg = dict(CLIENT)
    msg["MessageType"] = "ListDevices"
    return pack_message(msg, tag)


def build_read_pair_record(udid, tag=2):
    msg = dict(CLIENT)
    msg["MessageType"] = "ReadPairRecord"
    msg["PairRecordID"] = udid
    return pack_message(msg, tag)


def build_connect(device_id, port, tag=3):
    msg = dict(CLIENT)
    msg["MessageType"] = "Connect"
    msg["DeviceID"] = device_id
    msg["PortNumber"] = socket.htons(port)
    return pack_message(msg, tag)


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def recv_message(sock):
    header = _read_exact(sock, 16)
    length = struct.unpack("<I", header[:4])[0]
    if length < 16 or length > 4 * 1024 * 1024:
        raise ValueError("bad length")
    body = _read_exact(sock, length - 16) if length > 16 else b""
    return plistlib.loads(body)


def parse_usb_devices(resp):
    """ListDevices の応答から USB 接続のものだけを [{device_id, udid}] で返す。"""
    out = []
    for d in (resp or {}).get("DeviceList", []) or []:
        props = d.get("Properties", {}) or {}
        if props.get("ConnectionType") != "USB":
            continue
        out.append({"device_id": d.get("DeviceID"), "udid": props.get("SerialNumber") or ""})
    return out


def parse_pair_record(resp):
    """ReadPairRecord の応答: 記録が返ってきたら True (= この PC を信頼済み)。"""
    data = (resp or {}).get("PairRecordData")
    return bool(data)


def parse_connect(resp):
    """Connect の応答: (成功か, 番号)。番号 3 は iPhone 側でアプリが待ち受けていない。"""
    number = (resp or {}).get("Number", -1)
    return number == 0, number


def lockdown_record_exists(udid, env=None):
    """usbmux から読めないときの補助: ProgramData の Lockdown フォルダにペアリング記録があるか。"""
    env = os.environ if env is None else env
    base = env.get("ProgramData") or env.get("ALLUSERSPROFILE")
    if not base or not udid:
        return None
    return os.path.isfile(os.path.join(base, "Apple", "Lockdown", "%s.plist" % udid))


def query_usbmux(addr=USBMUX_ADDR, timeout=1.5, connector=None):
    """現在の状態: {service, usb, udid, device_id, trusted}。例外は投げない。
    trusted は 記録あり=True / なし=False / 確認できない=None。"""
    connector = connector or socket.create_connection
    snap = {"service": False, "usb": False, "udid": "", "device_id": None, "trusted": None}
    try:
        sock = connector(addr, timeout=timeout)
    except Exception:
        return snap
    snap["service"] = True
    try:
        try:
            sock.settimeout(timeout)
        except Exception:
            pass
        sock.sendall(build_list_devices(1))
        devs = parse_usb_devices(recv_message(sock))
        if not devs:
            return snap
        snap["usb"] = True
        snap["udid"] = devs[0]["udid"]
        snap["device_id"] = devs[0]["device_id"]
        if snap["udid"]:
            sock.sendall(build_read_pair_record(snap["udid"], 2))
            snap["trusted"] = True if parse_pair_record(recv_message(sock)) else False
            if snap["trusted"] is False:
                # 記録が見つからないだけなのか、問い合わせ自体が非対応なのかを、ファイルでも確かめる
                if lockdown_record_exists(snap["udid"]):
                    snap["trusted"] = True
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return snap


def probe_phonecam(snap, addr=USBMUX_ADDR, port=PHONE_PORT, timeout=2.0, connector=None):
    """iPhone の PhoneCam アプリ (ポート 5555) へ usbmux 経由でつながるか。つながったらすぐ閉じる。"""
    if not snap.get("usb") or snap.get("device_id") is None:
        return False
    connector = connector or socket.create_connection
    try:
        sock = connector(addr, timeout=timeout)
    except Exception:
        return False
    try:
        sock.settimeout(timeout)
        sock.sendall(build_connect(snap["device_id"], port, 1))
        ok, _ = parse_connect(recv_message(sock))
        return ok
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def altserver_running(runner=None):
    """AltServer.exe のプロセスが動いているか。psutil があればそれ、なければ tasklist。例外は投げない。"""
    if runner is None:
        try:
            import psutil  # type: ignore
            for p in psutil.process_iter(["name"]):
                if (p.info.get("name") or "").lower() == ALTSERVER_IMAGE.lower():
                    return True
            return False
        except ImportError:
            pass
        except Exception:
            pass
    try:
        if runner is not None:
            out = runner(["tasklist", "/FI", "IMAGENAME eq " + ALTSERVER_IMAGE, "/FO", "CSV", "/NH"])
        else:
            kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "timeout": 8}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + ALTSERVER_IMAGE, "/FO", "CSV", "/NH"], **kw)
            out = (r.stdout or b"").decode("mbcs" if os.name == "nt" else "utf-8", "replace")
        return ('"%s"' % ALTSERVER_IMAGE).lower() in (out or "").lower()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 手順の定義と状態判定
# ---------------------------------------------------------------------------

IPHONE_STEPS = [
    {"id": "usb", "title": "iPhoneをUSBで接続",
     "lines": ["LightningケーブルでiPhoneをPCにつなぎます。", "iPhoneの画面のロックを解除しておきます。"]},
    {"id": "trust", "title": "「このコンピュータを信頼」",
     "lines": ["iPhoneに「このコンピュータを信頼しますか?」と出たら「信頼」を押します。",
               "パスコードの入力を求められたら入力します。"]},
    {"id": "itunes", "title": "iTunesでWi-Fi同期をオン",
     "lines": ["iTunesでiPhoneを開き、「Wi-Fi経由でこのiPhoneと同期」にチェックを入れて「適用」を押します。",
               "7日ごとの自動更新に使います。更新を手動で行うなら省略できます。"]},
    {"id": "altstore", "title": "AltStoreをiPhoneに入れる",
     "lines": ["AltServerを起動し、タスクトレイのAltServerのアイコン → Install AltStore → このiPhone を選びます。",
               "Apple IDとパスワードを入力します。サブのApple IDでも大丈夫です。"]},
    {"id": "settings", "title": "iPhone側の設定",
     "lines": ["設定 > プライバシーとセキュリティ > デベロッパモード をオンにして、iPhoneを再起動します。",
               "設定 > 一般 > VPNとデバイス管理 で自分のApple IDを開き、「信頼」を押します。"]},
    {"id": "phonecam", "title": "PhoneCamを入れる",
     "lines": ["AltStoreのソースにPhoneCamを追加すると、アプリの更新もAltStoreでできます。",
               "iPhoneでQRコードを読み取るか、下のリンクをiPhoneで開きます。"]},
    {"id": "test", "title": "接続テスト",
     "lines": ["iPhoneでPhoneCamを開いたままにします（初回はカメラとローカルネットワークを「許可」）。",
               "つながると自動で次へ進めます。"]},
]
# AltStore の更新 (Refresh) は「どのPCのAltServerからでも」できるが、そのPCが次の条件を満たしている必要がある
ALTSTORE_REQUIREMENTS = [
    "Apple公式サイト版の iTunes と iCloud が入っている（Microsoft Store版は不可）",
    "そのPCにiPhoneをUSBでつないで、「このコンピュータを信頼」を一度押してある",
    "iTunesで「Wi-Fi経由でこのiPhoneと同期」がオンになっている",
    "AltServerが起動している（タスクトレイにアイコンがある）",
    "同じネットワークにいて、VPNはオフ。Windowsのネットワークは「プライベート」",
]
ALTSTORE_NEW_PC_NOTE = ("新しいPCで更新できないときは、そのPCで AltServer → Install AltStore を一度実行してください。"
                        "そのPCとiPhoneのペアリングがやり直され、入れてあるアプリはそのまま残ります。")

# エラー 2003 (AltServer could not find this device) が出たとき
ALT_2003_TITLE = "エラー2003「AltServer could not find this device」が出るとき"
ALT_2003_CHECKLIST = [
    "iPhoneをUSBでつないだまま、ロックを解除して更新する",
    "タスクトレイのAltServerを終了して、もう一度起動する",
    "iTunesでiPhoneを開き、「Wi-Fi経由でこのiPhoneと同期」にチェックを入れて「適用」を押す",
    "iPhoneとPCを同じWi-Fiにつなぐ。VPN（Tailscaleなど）はオフにする",
    "それでもだめなら、このPCから AltServer → Install AltStore でAltStoreを入れ直す（入れてあるアプリは残ります）",
]

STEP_IDS = [s["id"] for s in IPHONE_STEPS]
STEP_BY_ID = {s["id"]: s for s in IPHONE_STEPS}
AUTO_ADVANCE = ("usb", "trust")  # 満たされていたら、初めて開いたときに限って自動で次へ進める手順


def step_status(step_id, snap, ctx=None):
    """(state, 表示文)。snap は query_usbmux の結果。ctx = {"altserver": bool, "app": bool, "link": bool}。"""
    ctx = ctx or {}
    snap = snap or {}
    if step_id == "usb":
        if not snap.get("service"):
            return ISSUE, "Apple USBドライバが見つかりません"
        if snap.get("usb"):
            return DONE, "iPhoneが見つかりました"
        return WAIT, "iPhoneを待っています"
    if step_id == "trust":
        if not snap.get("usb"):
            return WAIT, "先にiPhoneをつないでください"
        t = snap.get("trusted")
        if t is True:
            return DONE, "信頼済みです"
        if t is False:
            return WAIT, "iPhoneのロックを解除して「信頼」を押してください"
        return UNKNOWN, "自動では確認できません"
    confirmed = ctx.get("confirmed")  # 利用者が「済んだ」を押した手順
    if step_id == "altstore":
        if ctx.get("altserver_running"):
            return DONE, "AltServerは起動しています"
        if confirmed:
            return DONE, "確認済みです"
        if not ctx.get("altserver"):
            return ISSUE, "AltServerが未導入です（前の画面でインストールできます）"
        return ISSUE, "AltServerを起動してください"
    if step_id in ("itunes", "settings", "phonecam"):
        if confirmed:
            return DONE, "確認済みです"
        return MANUAL, "ご自身で確認してください"
    if step_id == "test":
        if ctx.get("link") or ctx.get("app"):
            return DONE, "PhoneCamアプリとつながりました"
        if not snap.get("usb"):
            return WAIT, "iPhoneがUSBで見つかりません"
        return WAIT, "iPhoneでPhoneCamを開いてください"
    return UNKNOWN, ""


def should_auto_advance(step_id, state, auto_done):
    """初めてその手順を開いたときに満たされていれば True。戻ってきたときは (auto_done に入るので) 進めない。"""
    return step_id in AUTO_ADVANCE and state == DONE and step_id not in auto_done


# ---------------------------------------------------------------------------
# AltStore ソース
# ---------------------------------------------------------------------------

def source_url(repo):
    if not repo or "/" not in repo:
        return None
    return "https://github.com/%s/releases/latest/download/%s" % (repo, ALTSTORE_SOURCE_FILE)


def source_deeplink(repo):
    url = source_url(repo)
    return ("altstore://source?url=" + url) if url else None
