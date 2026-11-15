# -*- coding: utf-8 -*-
"""PC 側のハンドシェイクの実行 (ソケットを使う部分)。計算は core/secure.py。

- connect_handshake: 通常接続 (両者がピン留め済み)。Wi-Fi は暗号化、USB は認証だけして平文のまま。
- PairSession: コード方式ペアリング (PC が始める)。画面 (CodeDialog) からも、USB の初回接続からも使う。
どちらも「認証が終わるまで設定・制御・メディアを送らない・受けない」(監査 #2)。
"""

import socket
import struct
import threading
import time

from phonecam.core.protocol import (
    MAX_PREAUTH_PAYLOAD, MSG_CONTROL, MSG_SECURE, PayloadTooLarge, PhoneCamError, encode_msg, read_msg,
)
from phonecam.core.secure import (
    HS_SECONDS, ConnectInitiator, HandshakeError, PairInitiator, Wire, hs_encode, hs_parse, session_channel,
)

SESSION_MAX_SEC = 120.0       # コード入力を待つ上限 (iPhone のポップアップと同じ)

ERR_KINDS = {"unpaired": "unpaired", "locked": "refused", "busy": "busy", "limited": "limited", "declined": "declined",
             "unavailable": "refused", "version": "legacy", "auth": "mismatch", "bad": "bad"}


def _raise_if_error(hs):
    if isinstance(hs, dict) and "err" in hs:
        kind = ERR_KINDS.get(hs.get("err"), "bad") if isinstance(hs.get("err"), str) else "bad"
        raise HandshakeError(kind, "peer error: %s" % hs.get("err"))


def _send(sock, body):
    sock.sendall(encode_msg(MSG_CONTROL, body))


def connect_handshake(sock, identity, lookup, stop=None, usb=False, deadline=None):
    """通常接続のハンドシェイク -> (Wire, SessionKeys または None (旧版), iPhone の公開鍵 bytes または None)。
    旧版 (v1) の相手: USB なら平文の旧方式のまま続ける (Wire.legacy)、Wi-Fi なら HandshakeError("legacy")。"""
    deadline = deadline or time.monotonic() + HS_SECONDS
    init = ConnectInitiator(identity)
    _send(sock, init.start())
    msg_type, payload = read_msg(sock, stop, False, None, deadline)
    hs = hs_parse(payload) if msg_type == MSG_CONTROL else None
    if hs is None:
        # v1 の iPhone は hs を知らず、状態 JSON (認証待ち / 通常の状態) を先に送ってくる
        if not usb:
            raise HandshakeError("legacy", "v1 peer")
        wire = Wire(None, legacy=True)
        wire.push_back(msg_type, payload)
        return wire, None, None
    _raise_if_error(hs)
    keys = init.on_reply(payload, lookup)
    _send(sock, init.confirm_message())
    msg_type, payload = read_msg(sock, stop, False, None, deadline)
    hs = hs_parse(payload) if msg_type == MSG_CONTROL else None
    if hs is None:
        raise HandshakeError("bad", "bad message")
    _raise_if_error(hs)
    init.check_confirm(hs)
    channel = None if usb else session_channel(keys, True)
    return Wire(channel), keys, init.peer_pub


class FrameReader:
    """途中までしか届いていないメッセージを失わずに、短い待ちで少しずつ読む (ペアリングの待ち受け用)。"""

    def __init__(self, sock, limit=MAX_PREAUTH_PAYLOAD):
        self.sock, self.limit = sock, limit
        self.buf = bytearray()

    def poll(self):
        """届いた完全なメッセージを 1 つ返す -> (種別, 本体)。まだなら None (ソケットのタイムアウト 1 回ぶん待つ)。"""
        while True:
            if len(self.buf) >= 5:
                msg_type, length = struct.unpack(">BI", bytes(self.buf[:5]))
                if length > self.limit:
                    raise PayloadTooLarge("type=%d (認証前)" % msg_type, length, self.limit)
                if len(self.buf) >= 5 + length:
                    payload = bytes(self.buf[5:5 + length])
                    del self.buf[:5 + length]
                    return msg_type, payload
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError("closed")
            self.buf.extend(chunk)


class PairSession:
    """コード方式ペアリング 1 回ぶん (別スレッド)。on_state(state) は次の値で、バックグラウンドスレッドから呼ばれる:
    "waiting" (つないでいる) / "code" (iPhone がコードを作った。入力待ち) / "shown" (iPhone の画面に出ている) /
    "background" (iPhone のアプリが前面にない) / "declined" / "busy" / "limited" / "legacy" (旧版の iPhone) /
    "failed" / "mismatch" (入力が違った。コードは使い捨てなのでやり直し) / "paired" (result に相手の情報)。
    auto_confirm: コードの入力を待たずに進める (USB の初回。USB 接続そのものが「このコンピュータを信頼」で守られている)。"""

    def __init__(self, connect, identity, pc_name, on_state=None, auto_confirm=False):
        self._connect = connect
        self.identity = identity
        self.pc_name = pc_name
        self.on_state = on_state or (lambda state: None)
        self.auto_confirm = auto_confirm
        self.state = None
        self.result = None
        self.code = None
        self._typed = None
        self._submitted = threading.Event()
        self._stop = threading.Event()
        self._sock = None
        self._lock = threading.Lock()
        self._thread = None
        self.done = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self.run, name="phonecam-pair", daemon=True)
        self._thread.start()
        return self

    def submit(self, typed):
        """画面で入力された 6 桁。一致したら進める。一致しなければ iPhone へ「違った」と伝えて終わる (使い捨て)。"""
        self._typed = typed
        self._submitted.set()

    def close(self):
        """ダイアログを閉じた: 取りやめを iPhone に伝えて切る (失敗としては数えさせない)。"""
        self._stop.set()
        self._submitted.set()
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None and self.state in ("code", "shown", "background"):
            try:
                _send(sock, hs_encode({"abort": "cancel"}))
            except Exception:
                pass
        if sock is not None:
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

    def run(self):
        try:
            self._run()
        except HandshakeError as e:
            self._emit({"declined": "declined", "busy": "busy", "limited": "limited", "legacy": "legacy",
                        "refused": "failed"}.get(e.kind, "failed"))
        except (OSError, ConnectionError, PhoneCamError, ValueError):
            if self.state != "paired":
                self._emit("failed")
        finally:
            with self._lock:
                sock, self._sock = self._sock, None
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            self.done.set()

    def _next(self, reader, deadline):
        """期限か停止までに 1 メッセージ読む -> (種別, 本体, hs)。時間切れ・停止は None。"""
        while not self._stop.is_set() and time.monotonic() < deadline:
            got = reader.poll()
            if got is not None:
                msg_type, payload = got
                return msg_type, payload, (hs_parse(payload) if msg_type == MSG_CONTROL else None)
        return None

    def _run(self):
        self._emit("waiting")
        sock = self._connect()
        with self._lock:
            if self._stop.is_set():
                sock.close()
                return
            self._sock = sock
        sock.settimeout(0.25)
        reader = FrameReader(sock)
        init = PairInitiator(self.identity, self.pc_name)
        _send(sock, init.start())
        got = self._next(reader, time.monotonic() + HS_SECONDS)
        if got is None:
            if not self._stop.is_set():
                raise HandshakeError("bad", "no reply")
            return
        msg_type, payload, hs = got
        if hs is None:
            raise HandshakeError("legacy", "v1 peer")      # v1 の iPhone は状態 JSON を先に送ってくる
        _raise_if_error(hs)
        _send(sock, init.on_reply(payload))
        self.code = init.code
        self._emit("code")
        # iPhone の「表示した / 前面にない / 拒否」を受けながら、コードの入力を待つ
        end = time.monotonic() + SESSION_MAX_SEC
        while not self._stop.is_set() and time.monotonic() < end and not (self.auto_confirm or self._submitted.is_set()):
            got = self._next(reader, min(end, time.monotonic() + 0.25))
            if got is None or got[2] is None:
                continue
            _raise_if_error(got[2])
            if got[2].get("ui") in ("shown", "background"):
                self._emit(got[2]["ui"])
        if self._stop.is_set() or not (self.auto_confirm or self._submitted.is_set()):
            return
        if not self.auto_confirm and not init.code_matches(self._typed):
            _send(sock, hs_encode({"abort": "mismatch"}))
            self._emit("mismatch")
            return
        _send(sock, init.confirm_message())
        sock.sendall(init.identity_frame())
        end = time.monotonic() + HS_SECONDS
        while True:
            got = self._next(reader, end)
            if got is None:
                raise HandshakeError("bad", "no reply")
            msg_type, payload, hs = got
            if msg_type == MSG_SECURE:
                break
            _raise_if_error(hs)    # 確認が通らなかった (auth) など。ui の通知は読み飛ばす
        self.result = init.open_peer(payload)
        self._emit("paired")
