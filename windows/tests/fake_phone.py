"""偽の iPhone (v2 の iPhone 側を Python で書いたもの)。本物のソケットで PC 側 (secure.py / handshake.py / link.py) と話す。

iPhone の実装 (ios/PhoneCam/Secure.swift) は別物で、同じ計算になることは docs/test-vectors/handshake-v2.json で確かめる。
ここは「PC が何を送り・何を送らないか」「改ざんや再送をどう扱うか」を、本物の TCP で試すための相手役。
"""
import hmac
import json
import os
import socket
import struct
import threading
import time

from phonecam.core import secure
from phonecam.core.handshake import FrameReader
from phonecam.core.protocol import MSG_CONTROL, MSG_SECURE, MSG_STATUS, MSG_VIDEO, encode_msg
from phonecam.core.secure import (
    Identity, Wire, confirm_mac, derive_session, fingerprint, hs_encode, hs_parse, pair_channel, pair_key, sas_code,
    session_channel, sha256, x25519, x25519_public,
)


class FakePhone:
    """1 本の待ち受け。1 接続ずつ処理する (同時に複数は受けない)。"""

    def __init__(self, device_id="9bd344c9-765e-4cb6-ae81-4c77f064131a", name="Fake iPhone", loopback=False, legacy=False):
        self.identity = Identity(os.urandom(32), device_id)
        self.name = name
        self.loopback = loopback            # True: usbmux 経由 (USB) のつもり。本文は平文
        self.legacy = legacy                # True: v1 の iPhone のふり (hs を知らない)
        self.pinned = {}                    # PC のフィンガープリント -> 公開鍵
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.raw = bytearray()              # PC から届いた生のバイト (暗号化の確認用)
        self.received = []                  # 認証後に届いたメッセージ (種別, 本文)
        self.before_auth = []               # 認証前に届いたメッセージ (種別, 本文)
        self.code = None                    # 直近のペアリングで作った照合コード
        self.pair_peer = None
        self.keys = None
        self.wire = None
        self.sent = threading.Lock()
        self.after_handshake = []           # 認証後に最初に送るもの [(種別, 本文)]
        self.first_frames = []              # 認証前に (v1 のふりで) 最初に送るもの
        self.error = None
        self.session_done = threading.Event()
        self.authenticated = threading.Event()
        self.tamper_next = False            # True: 次に送る暗号文の 1 バイトを変える
        self.sock = None
        threading.Thread(target=self._accept, daemon=True).start()

    # -- 後始末 --------------------------------------------------------------
    def close(self):
        self.stop.set()
        try:
            self.listener.close()
        except OSError:
            pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _accept(self):
        self.listener.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self._handle(conn)
            except Exception as e:      # テストの相手役: 失敗は記録して次へ
                self.error = e
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    # -- 1 接続 ----------------------------------------------------------------
    def _recv_frame(self, reader, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.stop.is_set():
            got = reader.poll()
            if got is not None:
                msg_type, payload = got
                self.raw.extend(struct.pack(">BI", msg_type, len(payload)) + payload)
                return msg_type, payload
        raise TimeoutError("no frame")

    def _send(self, conn, msg_type, payload):
        conn.sendall(encode_msg(msg_type, payload))

    def _handle(self, conn):
        self.sock = conn
        conn.settimeout(0.2)
        reader = FrameReader(conn, limit=16 * 1024 * 1024)
        for frame in self.first_frames:
            conn.sendall(frame)            # v1 のふり / 認証前の映像など
        if self.legacy:
            return self._legacy(conn, reader)
        msg_type, m1 = self._recv_frame(reader)
        hs = hs_parse(m1) if msg_type == MSG_CONTROL else None
        if hs is None:
            self.before_auth.append((msg_type, m1))
            return
        mode = hs.get("mode")
        if mode == "pair":
            self._pair(conn, reader, m1, hs)
        elif mode == "connect":
            self._connect(conn, reader, m1, hs)

    def _legacy(self, conn, reader):
        """v1 の iPhone: hs を知らない。Wi-Fi なら authRequired の状態を、USB なら普通の状態を先に送る。"""
        status = {"authRequired": True, "authOk": False, "pairPopup": 1} if not self.loopback else {"fps": 30, "deviceId": self.identity.device_id}
        self._send(conn, MSG_STATUS, json.dumps(status).encode())
        try:
            while not self.stop.is_set():
                self.received.append(self._recv_frame(reader, 2.0))
        except (TimeoutError, ConnectionError, OSError):
            pass

    def _pair(self, conn, reader, m1, hs):
        e_pc_commit = bytes.fromhex(hs["commit"])
        e_priv = os.urandom(32)
        e_pub, n = x25519_public(e_priv), os.urandom(16)
        m2 = hs_encode({"e": e_pub.hex(), "n": n.hex()})
        self._send(conn, MSG_CONTROL, m2)
        msg_type, m3 = self._recv_frame(reader)
        h3 = hs_parse(m3)
        e_pc, n_pc = bytes.fromhex(h3["e"]), bytes.fromhex(h3["n"])
        assert hmac.compare_digest(sha256(e_pc, n_pc), e_pc_commit), "commit mismatch"
        th = sha256(m1, m2, m3)
        ss = x25519(e_priv, e_pc)
        self.code = sas_code(ss, th)
        k = pair_key(ss, th)
        self._send(conn, MSG_CONTROL, hs_encode({"ui": "shown"}))
        msg_type, m4 = self._recv_frame(reader, 10.0)
        h4 = hs_parse(m4)
        if h4 is None or "confirm" not in h4:
            return                              # abort (mismatch / cancel)
        if not hmac.compare_digest(bytes.fromhex(h4["confirm"]), confirm_mac(k, b"pc", th)):
            self._send(conn, MSG_CONTROL, hs_encode({"err": "auth"}))
            return
        channel = pair_channel(k, False)
        msg_type, m5 = self._recv_frame(reader)
        assert msg_type == MSG_SECURE
        _t, body = channel.open(m5)
        peer = secure.parse_peer(hs_parse(body))
        self.pinned[fingerprint(peer["s"])] = peer["s"]
        self.pair_peer = peer
        reply = hs_encode({"s": self.identity.pub.hex(), "name": self.name, "id": self.identity.device_id,
                           "model": "iPhone 13", "addresses": ["192.168.1.7"]})
        conn.sendall(channel.seal(MSG_CONTROL, reply))
        time.sleep(0.05)

    def _connect(self, conn, reader, c1, hs):
        sf = hs.get("sf")
        s_pc = self.pinned.get(sf)
        if s_pc is None:
            self._send(conn, MSG_CONTROL, hs_encode({"err": "unpaired"}))
            return
        e_pc = bytes.fromhex(hs["e"])
        e_priv = os.urandom(32)
        e_pub, n = x25519_public(e_priv), os.urandom(16)
        c2 = hs_encode({"e": e_pub.hex(), "n": n.hex(), "tf": self.identity.fp})
        self._send(conn, MSG_CONTROL, c2)
        th = sha256(c1, c2)
        ikm = x25519(e_priv, e_pc) + x25519(e_priv, s_pc) + x25519(self.identity.priv, e_pc)
        keys = derive_session(ikm, th)
        msg_type, c3 = self._recv_frame(reader)
        h3 = hs_parse(c3)
        if not hmac.compare_digest(bytes.fromhex(h3["confirm"]), confirm_mac(keys.k_confirm, b"pc", th)):
            self._send(conn, MSG_CONTROL, hs_encode({"err": "auth"}))
            return
        self._send(conn, MSG_CONTROL, hs_encode({"confirm": confirm_mac(keys.k_confirm, b"ph", th).hex()}))
        self.keys = keys
        channel = None if self.loopback else session_channel(keys, False)
        self.wire = Wire(channel)
        self.authenticated.set()
        for msg_type, payload in self.after_handshake:
            self.push(msg_type, payload)
        self._session(conn, reader)

    def push(self, msg_type, payload):
        """認証後の送信 (テストから呼ぶ)。tamper_next なら暗号文を 1 バイト変える。"""
        with self.sent:
            data = self.wire.pack(msg_type, payload)
            if self.tamper_next and self.wire.secure:
                self.tamper_next = False
                data = data[:-1] + bytes([data[-1] ^ 1])
            self.sock.sendall(data)

    def push_raw(self, data):
        with self.sent:
            self.sock.sendall(data)

    def _session(self, conn, reader):
        while not self.stop.is_set():
            try:
                msg_type, payload = self._recv_frame(reader, 0.5)
            except TimeoutError:
                continue
            except (ConnectionError, OSError):
                break
            if self.wire.secure:
                if msg_type != MSG_SECURE:
                    self.error = AssertionError("plain message on secure link")
                    break
                try:
                    msg_type, payload = self.wire.channel.open(payload)
                except secure.SecureError as e:
                    self.error = e
                    break
            self.received.append((msg_type, payload))
        self.session_done.set()

    # -- 音声チャンネル ------------------------------------------------------------
    def serve_audio(self):
        """音声用の待ち受け。最初のメッセージ (audio_tok) を検証して audio_ok に結果を入れる。"""
        audio = FakeAudio(self)
        return audio


class FakeAudio:
    def __init__(self, phone):
        self.phone = phone
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        self.ok = threading.Event()
        self.bad = threading.Event()
        self.conn = None
        self.wire = None
        threading.Thread(target=self._run, daemon=True).start()

    def close(self):
        try:
            self.listener.close()
        except OSError:
            pass

    def _run(self):
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        conn.settimeout(3.0)
        self.conn = conn
        keys = self.phone.keys
        channel = None if self.phone.loopback else session_channel(keys, False, audio=True)
        wire = Wire(channel)
        self.wire = wire
        try:
            msg_type, payload = wire.read(conn)
            hs = hs_parse(payload)
            if hs and hs.get("audio_tok") == keys.audio_tok.hex():
                self.ok.set()
            else:
                self.bad.set()
        except Exception:
            self.bad.set()


def qr_phone(address, port, token, pc_fp, name="Fake iPhone", device_id="9bd344c9-765e-4cb6-ae81-4c77f064131a",
             identity=None, tamper_fp=False, timeout=5.0):
    """QR を読んだ iPhone のふり (iPhone が始める側)。結果の辞書 (PC の自己紹介 / 失敗) を返す。"""
    identity = identity or Identity(os.urandom(32), device_id)
    sock = socket.create_connection((address, port), timeout=timeout)
    try:
        sock.settimeout(0.25)
        reader = FrameReader(sock)
        e_priv = os.urandom(32)
        e_pub, n = x25519_public(e_priv), os.urandom(16)
        q1 = hs_encode({"v": 2, "mode": "qr", "e": e_pub.hex(), "n": n.hex()})
        sock.sendall(encode_msg(MSG_CONTROL, q1))

        def frame():
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                got = reader.poll()
                if got is not None:
                    return got
            raise TimeoutError("no frame")

        t, q2 = frame()
        h2 = hs_parse(q2)
        s_pc = bytes.fromhex(h2["s"])
        if not secure.fp_equal(fingerprint(s_pc), pc_fp):
            return {"error": "fingerprint"}
        e_pc = bytes.fromhex(h2["e"])
        th = sha256(q1, q2)
        keys = derive_session(x25519(e_priv, e_pc) + x25519(e_priv, s_pc), th, secure.INFO_QR)
        t, q3 = frame()
        if not hmac.compare_digest(bytes.fromhex(hs_parse(q3)["confirm"]), confirm_mac(keys.k_confirm, b"pc", th)):
            return {"error": "confirm"}
        channel = session_channel(keys, False)
        body = hs_encode({"s": identity.pub.hex(), "tok": token, "name": name, "id": identity.device_id,
                          "model": "iPhone 13", "addresses": ["192.168.1.7"]})
        sock.sendall(channel.seal(MSG_CONTROL, body))
        try:
            t, ct = frame()
        except (TimeoutError, ConnectionError, OSError):
            return {"error": "closed"}
        _t, reply = channel.open(ct)
        return {"ok": True, "reply": hs_parse(reply), "identity": identity}
    finally:
        sock.close()
