# -*- coding: utf-8 -*-
"""iPhone へ「ペアリング要求」を送る (プロトコル v1.6)。iPhone の画面にペアリングコードをポップアップで出してもらう。

認証前の接続で、PC の名前と一回限りの要求 ID だけを送る。コードそのものはネットワークに流れない。
要求が受け付けられても認証は通らない (コードの照合は今までどおり iPhone が行う)。
古い iPhone アプリは認証前のステータスに ``pairPopup`` を付けないので、その場合は何も送らない。
"""

import json
import secrets
import struct
import threading
import time
import unicodedata

from phonecam.core.protocol import MSG_CONTROL, MSG_STATUS, PhoneCamError, encode_msg, read_exact
from phonecam.core.usbmux import wifi_connect

PAIR_REQUEST_VERSION = 1
NAME_LIMIT = 40
CAPABILITY_WAIT_SEC = 2.0     # 接続してから iPhone の認証前ステータス (pairPopup 付き) を待つ時間
REPLY_WAIT_SEC = 3.0          # 要求を送ってから受け付けの返事を待つ時間
SESSION_MAX_SEC = 180.0       # ダイアログを開いたままにできる上限 (iPhone 側は 120 秒でポップアップを閉じる)
MAX_PAYLOAD = 64 * 1024       # 認証前の iPhone から受け取る 1 メッセージの上限

# 画面に出す結果 (state)
SHOWN = "shown"               # iPhone の画面にコードが出ている
DECLINED = "declined"         # iPhone で拒否された
BACKGROUND = "background"     # iPhone のアプリが前面にない (設定画面で確認してもらう)
FALLBACK = "fallback"         # 対応していない / 返事がない / 混み合っている: 今までどおり iPhone の設定画面で確認してもらう

_STATES = {SHOWN, DECLINED, BACKGROUND}


def sanitize_name(value, limit=NAME_LIMIT):
    """表示用の PC 名: 制御文字・書式文字・行区切りを除き、前後の空白を落として limit 文字までにする。空なら "PC"。"""
    if not isinstance(value, str):
        return "PC"
    kept = []
    for ch in value:
        if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        kept.append(ch)
        if len(kept) >= limit * 2:
            break
    text = "".join(kept).strip()[:limit].strip()
    return text or "PC"


def new_request_id():
    return secrets.token_hex(8)


def build_request(request_id, name):
    """認証前に送る要求メッセージ (種別 10 の JSON)。"""
    body = {"pairRequest": {"v": PAIR_REQUEST_VERSION, "id": request_id, "name": sanitize_name(name)}}
    return encode_msg(MSG_CONTROL, json.dumps(body, ensure_ascii=False).encode("utf-8"))


def build_cancel(request_id):
    return encode_msg(MSG_CONTROL, json.dumps({"pairCancel": {"id": request_id}}).encode("utf-8"))


def supports_popup(status):
    """iPhone の認証前ステータスが、ペアリング要求に対応していると言っているか。"""
    if not isinstance(status, dict):
        return False
    value = status.get("pairPopup")
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def parse_reply(status, request_id):
    """ステータス JSON から、この要求への返事を取り出す -> state / None (無関係・不正)。
    iPhone が返す "shown" "declined" "background" 以外 ("busy" "limited" "unavailable" や未知の値) は FALLBACK にする。"""
    if not isinstance(status, dict):
        return None
    reply = status.get("pairReply")
    if not isinstance(reply, dict) or reply.get("id") != request_id:
        return None
    state = reply.get("state")
    if not isinstance(state, str):
        return FALLBACK
    return state if state in _STATES else FALLBACK


class _Until:
    """read_msg に渡す、期限か停止で真になる stop_event 代わり。"""

    def __init__(self, stop, deadline):
        self.stop, self.deadline = stop, deadline

    def is_set(self):
        return self.stop.is_set() or time.monotonic() >= self.deadline


class PairRequester:
    """未登録の iPhone へ要求を送り、返事 (state) を on_state(state) で知らせる。別スレッドで動く。
    state は "shown" -> ("declined") / "background" / "fallback"。on_state はバックグラウンドスレッドから呼ばれる。"""

    def __init__(self, ip, name, on_state, connect=wifi_connect, request_id=None):
        self.ip = ip
        self.name = name
        self.on_state = on_state
        self.request_id = request_id or new_request_id()
        self.state = None
        self._connect = connect
        self._stop = threading.Event()
        self._sock = None
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="phonecam-pairreq", daemon=True)
        self._thread.start()
        return self

    def close(self):
        """ダイアログが閉じた/コードが入力された: iPhone のポップアップを閉じてもらい、接続を切る。"""
        self._stop.set()
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is None:
            return
        if self.state == SHOWN:
            try:
                sock.sendall(build_cancel(self.request_id))
            except Exception:
                pass
        for action in (lambda: sock.shutdown(2), sock.close):
            try:
                action()
            except Exception:
                pass

    def _emit(self, state):
        if state != self.state and not self._stop.is_set():
            self.state = state
            try:
                self.on_state(state)
            except Exception:
                pass

    def _run(self):
        try:
            sock = self._connect(self.ip)
        except Exception:
            self._emit(FALLBACK)
            return
        with self._lock:
            if self._stop.is_set():
                sock.close()
                return
            self._sock = sock
        try:
            sock.settimeout(0.5)
            self._exchange(sock)
        except (OSError, ConnectionError, PhoneCamError, ValueError):
            if self.state in (None, SHOWN):
                self._emit(FALLBACK)   # 返事の前/表示中に切れた (Wi-Fi を許可していない iPhone、ポップアップの時間切れなど)
        finally:
            with self._lock:
                owned = self._sock is sock
                self._sock = None
            if owned:
                try:
                    sock.close()
                except Exception:
                    pass

    def _read_status(self, sock, until):
        """期限までに届いた最初の状態 JSON を返す (ほかの種別は無視)。無ければ None。"""
        while True:
            try:
                msg_type, length = struct.unpack(">BI", read_exact(sock, 5, until))
                if length > MAX_PAYLOAD:
                    raise ConnectionError("too large")   # 認証前の相手の巨大なメッセージは読まずに切る
                payload = read_exact(sock, length, until) if length else b""
            except ConnectionAbortedError:
                return None
            if msg_type == MSG_STATUS:
                try:
                    status = json.loads(payload.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(status, dict):
                    return status

    def _exchange(self, sock):
        # 1. 認証前のステータスで、対応しているか確かめる (対応していない古い iPhone には何も送らない)
        status = self._read_status(sock, _Until(self._stop, time.monotonic() + CAPABILITY_WAIT_SEC))
        if not supports_popup(status):
            self._emit(FALLBACK)
            return
        # 2. 要求を送り、受け付けの返事を待つ。そのあとは「拒否」が届くか、閉じられるまで待つ
        sock.sendall(build_request(self.request_id, self.name))
        deadline = time.monotonic() + REPLY_WAIT_SEC
        end = time.monotonic() + SESSION_MAX_SEC
        while not self._stop.is_set() and time.monotonic() < end:
            status = self._read_status(sock, _Until(self._stop, deadline if self.state is None else end))
            if status is None:
                if self.state is None:
                    self._emit(FALLBACK)   # 返事が来なかった
                return
            state = parse_reply(status, self.request_id)
            if state is None:
                continue
            self._emit(state)
            if state != SHOWN:
                return
