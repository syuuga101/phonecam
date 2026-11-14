# -*- coding: utf-8 -*-
"""iPhone のセットアップ案内 (ウィザードの手順ページ用) の、画面に依存しない部分。

- usbmux (Apple Mobile Device Service) への問い合わせ: 接続の有無 / 信頼 (ペアリング) 済みか / PhoneCam アプリに届くか
- 各手順の状態 (✓ / 待機 / 手動確認) の判定
- SideStore に追加する配布元 (ソース) の URL と、追加用ディープリンク

手順の文言は翻訳キーで持つ (画面に出すときに t() で引く)。SideStore の入れ方は公式の手順に合わせている:
iloader で最初の 1 回だけ USB で入れ、あとは iPhone だけ (LocalDevVPN をオン・Wi-Fi) で更新する。
"""

import os
import plistlib
import socket
import struct

try:
    from phonecam.i18n import t as _i18n_t
except Exception:  # pragma: no cover
    _i18n_t = None

USBMUX_ADDR = ("127.0.0.1", 27015)
PHONE_PORT = 5555
CLIENT = {"ClientVersionString": "phonecam", "ProgName": "phonecam"}

# 配布元のファイル (AltStore 形式。SideStore も同じ形式を読む)
SOURCE_FILE = "sidestore-source.json"
APP_BUNDLE_ID = "com.example.phonecam"  # ios/project.yml の PRODUCT_BUNDLE_IDENTIFIER と同じ

# 状態
DONE = "done"        # ✓
WAIT = "wait"        # まだ (自動で更新される)
MANUAL = "manual"    # 自動では確認できない (利用者が目で見て進める)
UNKNOWN = "unknown"  # 確認に失敗
ISSUE = "issue"      # 要対応 (⚠ オレンジ表示)


def tr(key, **kw):
    """翻訳 (phonecam.i18n が読めないときはキーのまま)。"""
    if _i18n_t is None:
        return key
    try:
        return _i18n_t(key, **kw) if kw else _i18n_t(key)
    except Exception:
        return key


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


# ---------------------------------------------------------------------------
# 手順の定義と状態判定
# ---------------------------------------------------------------------------

def _step(sid, nlines):
    return {"id": sid, "title": "setup.iphone.%s.title" % sid,
            "lines": ["setup.iphone.%s.l%d" % (sid, n) for n in range(1, nlines + 1)]}


# title / lines は翻訳キー (画面に出すときに t() で引く)
IPHONE_STEPS = [
    _step("usb", 2),        # USB でつなぐ
    _step("trust", 2),      # 「このコンピュータを信頼」
    _step("iloader", 3),    # iloader で SideStore を入れる
    _step("devmode", 2),    # デベロッパ App を信頼・デベロッパモード
    _step("vpn", 3),        # LocalDevVPN を入れて Connect
    _step("sidestore", 4),  # SideStore にサインインして 7 DAYS を更新
    _step("phonecam", 3),   # SideStore に配布元を追加して PhoneCam を入れる
    _step("test", 2),       # 接続テスト
]

# すでに AltStore Classic を使っている人への案内 (iloader の段階の折りたたみ)
MIGRATE_TITLE = "setup.iphone.migrate.title"
MIGRATE_LINES = ["setup.iphone.migrate.l1", "setup.iphone.migrate.l2", "setup.iphone.migrate.l3"]

# ペアリングファイルが切れたとき (iOS を更新したあとなど) の入れ直し
PAIRING_TITLE = "setup.iphone.pairing.title"
PAIRING_LINES = ["setup.iphone.pairing.l1", "setup.iphone.pairing.l2", "setup.iphone.pairing.l3",
                 "setup.iphone.pairing.l4", "setup.iphone.pairing.l5", "setup.iphone.pairing.l6"]

# SideStore を入れるときの条件 (導入チェックの下に出す)
PREREQ_TITLE = "setup.iphone.prereq.title"
PREREQ_INTRO = "setup.iphone.prereq.intro"
PREREQ_LINES = ["setup.iphone.prereq.l1", "setup.iphone.prereq.l2", "setup.iphone.prereq.l3",
                "setup.iphone.prereq.l4", "setup.iphone.prereq.l5"]
PREREQ_NOTE = "setup.iphone.prereq.note"

STEP_IDS = [s["id"] for s in IPHONE_STEPS]
STEP_BY_ID = {s["id"]: s for s in IPHONE_STEPS}
AUTO_ADVANCE = ("usb", "trust")  # 満たされていたら、初めて開いたときに限って自動で次へ進める手順
# 自動では確認しきれず、利用者が「済んだ」を押す手順
MANUAL_STEPS = ("iloader", "devmode", "vpn", "sidestore", "phonecam")


def step_status(step_id, snap, ctx=None):
    """(state, 表示文)。snap は query_usbmux の結果。ctx = {"iloader": bool, "app": bool, "link": bool, "confirmed": bool}。"""
    ctx = ctx or {}
    snap = snap or {}
    if step_id == "usb":
        if not snap.get("service"):
            return ISSUE, tr("setup.iphone.status.no_driver")
        if snap.get("usb"):
            return DONE, tr("setup.iphone.status.found")
        return WAIT, tr("setup.iphone.status.waiting")
    if step_id == "trust":
        if not snap.get("usb"):
            return WAIT, tr("setup.iphone.status.connect_first")
        trusted = snap.get("trusted")
        if trusted is True:
            return DONE, tr("setup.iphone.status.trusted")
        if trusted is False:
            return WAIT, tr("setup.iphone.status.unlock_trust")
        return UNKNOWN, tr("setup.iphone.status.cannot_check")
    confirmed = ctx.get("confirmed")  # 利用者が「済んだ」を押した手順
    if step_id in MANUAL_STEPS:
        if confirmed:
            return DONE, tr("setup.wizard.confirmed_done")
        if step_id == "iloader" and not ctx.get("iloader"):
            return ISSUE, tr("setup.wizard.iloader_missing")
        return MANUAL, tr("setup.wizard.manual_check")
    if step_id == "test":
        if ctx.get("link") or ctx.get("app"):
            return DONE, tr("setup.iphone.status.test_done")
        if not snap.get("usb"):
            return WAIT, tr("setup.iphone.status.test_no_usb")
        return WAIT, tr("setup.iphone.status.test_open_app")
    return UNKNOWN, ""


def should_auto_advance(step_id, state, auto_done):
    """初めてその手順を開いたときに満たされていれば True。戻ってきたときは (auto_done に入るので) 進めない。"""
    return step_id in AUTO_ADVANCE and state == DONE and step_id not in auto_done


# ---------------------------------------------------------------------------
# SideStore の配布元 (ソース)
# ---------------------------------------------------------------------------

def source_url(repo):
    if not repo or "/" not in repo:
        return None
    return "https://github.com/%s/releases/latest/download/%s" % (repo, SOURCE_FILE)


def source_deeplink(repo):
    """iPhone のカメラで読むと SideStore が開いて、配布元を追加できるリンク。"""
    url = source_url(repo)
    return ("sidestore://source?url=" + url) if url else None
