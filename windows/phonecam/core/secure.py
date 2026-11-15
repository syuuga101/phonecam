# -*- coding: utf-8 -*-
"""Wi-Fi の暗号化と相互認証 (プロトコル v2)。ネットワークにも画面にも依存しない純粋な部分。

暗号部品は X25519 / HKDF-SHA256 / ChaCha20-Poly1305。仕様は docs/PROTOCOL.md の「セキュリティ (v2)」。
iPhone 側は ios/PhoneCam/Secure.swift が同じ計算をする (docs/test-vectors/handshake-v2.json で両方の一致を確かめる)。

ここにあるもの:
- 鍵・指紋・HKDF・照合コード・確認 (HMAC) の計算
- 長期鍵 (Identity): Windows では DPAPI で包んで設定に置く
- 通信路 (Channel): 方向別の 64bit カウンタを nonce に使う AEAD。種別と本文をまとめて包む
- Wire: 接続 1 本ぶんの送受信 (Wi-Fi は暗号化、USB は平文)
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import threading
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from phonecam.core.protocol import (
    LimitError, MSG_CONTROL, MSG_SECURE, PhoneCamError, SECURE_OVERHEAD, encode_msg, payload_limit, read_msg,
)

HS_VERSION = 2
HS_SECONDS = 10.0           # ハンドシェイクの期限 (接続してから)
CODE_DIGITS = 6
TAG_LEN = 16

INFO_PAIR_SAS = b"phonecam pair sas"
INFO_PAIR_KEY = b"phonecam pair key"
INFO_SESSION = b"phonecam v2 session"
INFO_QR = b"phonecam v2 qr"

# HKDF 出力の割り当て (通常接続・QR 共通。192 バイト)
KEY_BLOCK = 192
_OFF = {
    "k_pc2ph": (0, 32), "k_ph2pc": (32, 64), "audio_tok": (64, 80), "iv_pc2ph": (80, 84), "iv_ph2pc": (84, 88),
    "k_confirm": (88, 120), "ka_pc2ph": (120, 152), "ka_ph2pc": (152, 184), "iva_pc2ph": (184, 188),
    "iva_ph2pc": (188, 192),
}
# コード方式ペアリングの AEAD (鍵は 1 本。nonce の固定 4 バイトで方向を分ける)
PAIR_IV_PC2PH = b"c2p\x00"
PAIR_IV_PH2PC = b"p2c\x00"


class SecureError(LimitError):
    """暗号化された通信の検証に失敗した (改ざん・順番の乱れ・再送・平文の混入)。切断する。"""

    def log_line(self):
        return "暗号化された通信の検証に失敗したので切断: %s" % self


class HandshakeError(PhoneCamError):
    """ハンドシェイクが成立しなかった。kind: unpaired / mismatch / legacy / refused / bad / declined / busy"""

    def __init__(self, kind, message=None):
        super().__init__(message or kind)
        self.kind = kind


# ---------------------------------------------------------------------------
# 基本の計算
# ---------------------------------------------------------------------------

def sha256(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


def hkdf(ikm, salt, info, length):
    """HKDF-SHA256 (RFC 5869)。"""
    prk = hmac.new(salt if salt else b"\x00" * 32, ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def x25519_public(priv):
    return X25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def x25519_generate():
    priv = os.urandom(32)
    return priv, x25519_public(priv)


def x25519(priv, pub):
    """DH。相手の鍵が位数の小さい点で結果が全部 0 のときは拒否する。"""
    try:
        shared = X25519PrivateKey.from_private_bytes(priv).exchange(X25519PublicKey.from_public_bytes(pub))
    except ValueError as e:
        raise HandshakeError("bad", "bad key") from e
    if shared == b"\x00" * 32:
        raise HandshakeError("bad", "bad key")
    return shared


def fingerprint(pub):
    """静的公開鍵のフィンガープリント: SHA256 の先頭 16 バイトを base32 (パディングなし)。"""
    return base64.b32encode(hashlib.sha256(pub).digest()[:16]).decode("ascii").rstrip("=")


def fp_equal(a, b):
    return hmac.compare_digest(str(a).upper().encode("utf-8", "replace"), str(b).upper().encode("utf-8", "replace"))


def is_hex(value, nbytes):
    return isinstance(value, str) and len(value) == nbytes * 2 and all(c in "0123456789abcdefABCDEF" for c in value)


def sas_code(ss, th):
    """照合コード: HKDF(ss, salt=th, info="phonecam pair sas") の先頭 4 バイト (ビッグエンディアン) を mod 1,000,000。"""
    n = struct.unpack(">I", hkdf(ss, th, INFO_PAIR_SAS, 4))[0]
    return "%06d" % (n % 1_000_000)


def pair_key(ss, th):
    return hkdf(ss, th, INFO_PAIR_KEY, 32)


def confirm_mac(key, role, th):
    """role は b"pc" か b"ph"。"""
    return hmac.new(key, role + th, hashlib.sha256).digest()


@dataclass(frozen=True)
class SessionKeys:
    k_pc2ph: bytes
    k_ph2pc: bytes
    audio_tok: bytes
    iv_pc2ph: bytes
    iv_ph2pc: bytes
    k_confirm: bytes
    ka_pc2ph: bytes
    ka_ph2pc: bytes
    iva_pc2ph: bytes
    iva_ph2pc: bytes


def derive_session(ikm, th, info=INFO_SESSION):
    block = hkdf(ikm, th, info, KEY_BLOCK)
    return SessionKeys(**{name: block[a:b] for name, (a, b) in _OFF.items()})


# ---------------------------------------------------------------------------
# ハンドシェイクのメッセージ
# ---------------------------------------------------------------------------

def hs_encode(body):
    """平文のハンドシェイクメッセージ (種別 10 の JSON 本体)。順序と書式を固定する (iPhone と文字列が一致する)。"""
    return json.dumps({"hs": body}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hs_parse(payload):
    """control JSON から "hs" の辞書を取り出す。無い・不正なら None。"""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    hs = data.get("hs") if isinstance(data, dict) else None
    return hs if isinstance(hs, dict) else None


def hs_frame(body):
    return encode_msg(MSG_CONTROL, hs_encode(body))


def read_hs(sock, stop=None, deadline=None):
    """認証前の 1 メッセージを読む (上限 64 KiB・期限つき) -> (種別, 本体, hs 辞書 or None)。"""
    msg_type, payload = read_msg(sock, stop, False, None, deadline)
    return msg_type, payload, (hs_parse(payload) if msg_type == MSG_CONTROL else None)


# ---------------------------------------------------------------------------
# 通信路
# ---------------------------------------------------------------------------

class Channel:
    """認証済みの通信路。メッセージ 1 つ = [種別 20][長さ][AEAD(種別 1 バイト + 本文)]。
    nonce は「固定 4 バイト + 方向別 64bit カウンタ」。受信側は自分のカウンタでしか復号しないので、
    改ざん・順番の入れ替え・欠落・再送はすべて検証に失敗する。"""

    def __init__(self, send_key, send_iv, recv_key, recv_iv):
        self._send = ChaCha20Poly1305(send_key)
        self._recv = ChaCha20Poly1305(recv_key)
        self._send_iv, self._recv_iv = send_iv, recv_iv
        self.send_count = 0
        self.recv_count = 0

    @staticmethod
    def _nonce(iv, counter):
        if counter >= 1 << 64:
            raise SecureError("counter exhausted")
        return iv + struct.pack(">Q", counter)

    def seal(self, msg_type, payload):
        inner = bytes((msg_type,)) + payload
        header = struct.pack(">BI", MSG_SECURE, len(inner) + TAG_LEN)
        ct = self._send.encrypt(self._nonce(self._send_iv, self.send_count), inner, header)
        self.send_count += 1
        return header + ct

    def open(self, ct):
        """暗号文 (外側の本体) を復号して (種別, 本文)。"""
        if len(ct) < TAG_LEN + 1:
            raise SecureError("short")
        header = struct.pack(">BI", MSG_SECURE, len(ct))
        try:
            inner = self._recv.decrypt(self._nonce(self._recv_iv, self.recv_count), ct, header)
        except InvalidTag as e:
            raise SecureError("tag") from e
        self.recv_count += 1
        return inner[0], inner[1:]


def pair_channel(k, pc_side):
    """コード方式ペアリングの AEAD (鍵 k は両方向で共通)。"""
    if pc_side:
        return Channel(k, PAIR_IV_PC2PH, k, PAIR_IV_PH2PC)
    return Channel(k, PAIR_IV_PH2PC, k, PAIR_IV_PC2PH)


def session_channel(keys, pc_side, audio=False):
    if audio:
        a, b, ia, ib = keys.ka_pc2ph, keys.ka_ph2pc, keys.iva_pc2ph, keys.iva_ph2pc
    else:
        a, b, ia, ib = keys.k_pc2ph, keys.k_ph2pc, keys.iv_pc2ph, keys.iv_ph2pc
    return Channel(a, ia, b, ib) if pc_side else Channel(b, ib, a, ia)


class Wire:
    """接続 1 本ぶんの送受信。channel があれば (Wi-Fi) 暗号化、無ければ (USB・旧版との USB) 平文。
    平文の Wi-Fi は作れない: Wi-Fi では必ず channel を付けて作る。"""

    def __init__(self, channel=None, legacy=False):
        self.channel = channel
        self.legacy = legacy         # 旧版 (v1) の相手との USB 接続
        self._pushback = []

    @property
    def secure(self):
        return self.channel is not None

    def pack(self, msg_type, payload):
        """送るバイト列。呼び出し側は送信ロックを持ったまま pack と sendall を続けて行う (カウンタの順序を守るため)。"""
        if self.channel is None:
            return encode_msg(msg_type, payload)
        return self.channel.seal(msg_type, payload)

    def push_back(self, msg_type, payload):
        self._pushback.append((msg_type, payload))

    def read(self, sock, stop=None, idle=None, deadline=None, cap=None):
        """1 メッセージ読む -> (種別, 本文)。暗号化している間は、平文のメッセージが来たら切る。"""
        if self._pushback:
            return self._pushback.pop(0)
        if self.channel is None:
            msg_type, payload = read_msg(sock, stop, True, idle, deadline, cap)
            if msg_type == MSG_SECURE:
                raise SecureError("encrypted message on plain link")
            return msg_type, payload
        outer_cap = cap + SECURE_OVERHEAD if cap is not None else None
        msg_type, ct = read_msg(sock, stop, True, idle, deadline, outer_cap)
        if msg_type != MSG_SECURE:
            raise SecureError("plain message on secure link")
        inner_type, payload = self.channel.open(ct)
        if len(payload) > payload_limit(inner_type, True) or (cap is not None and len(payload) > cap):
            raise SecureError("too large")
        return inner_type, payload


# ---------------------------------------------------------------------------
# 長期鍵 (Identity)
# ---------------------------------------------------------------------------

_DPAPI_ENTROPY = b"phonecam identity v1"


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make(raw):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source, keep1 = make(data)
    entropy, keep2 = make(_DPAPI_ENTROPY)
    out = Blob()
    flags = 0x1   # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "PhoneCam", ctypes.byref(entropy), None, None, flags, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(out))
    del keep1, keep2
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_secret(data):
    """Windows ではユーザースコープの DPAPI で包む -> (方式, バイト列)。Windows 以外 (テスト) は包まない。"""
    if sys.platform == "win32":
        return "dpapi", _dpapi(data, True)
    return "plain", data


def unprotect_secret(method, blob):
    if method == "dpapi":
        return _dpapi(blob, False)
    if method == "plain" and sys.platform != "win32":
        return blob
    raise ValueError("unsupported protection")


class Identity:
    """この PC の長期鍵。priv/pub は X25519 の生の 32 バイト。device_id はインストールごとの UUID。"""

    def __init__(self, priv, device_id):
        self.priv = priv
        self.pub = x25519_public(priv)
        self.fp = fingerprint(self.pub)
        self.device_id = device_id


_identity_lock = threading.Lock()
_identity_cache = {}
_ephemeral = []      # DPAPI が使えなかったとき、この起動の間だけ使う鍵


def load_identity(settings):
    """設定の "identity" から長期鍵を読む。無い・読めないときは作って設定へ書く (保存は呼び出し側)。"""
    with _identity_lock:
        entry = settings.get("identity")
        if not (isinstance(entry, dict) and entry.get("key")) and _ephemeral:
            return _ephemeral[0]
        if isinstance(entry, dict):
            key = (entry.get("prot"), entry.get("key"), entry.get("id"))
            cached = _identity_cache.get(key)
            if cached is not None:
                return cached
            try:
                priv = unprotect_secret(entry["prot"], bytes.fromhex(entry["key"]))
                if len(priv) == 32 and is_hex(entry.get("pub"), 32):
                    ident = Identity(priv, str(uuid.UUID(entry["id"])))
                    if ident.pub.hex() == entry["pub"]:
                        _identity_cache[key] = ident
                        return ident
            except Exception:
                pass
        priv = os.urandom(32)
        ident = Identity(priv, str(uuid.uuid4()))
        try:
            method, blob = protect_secret(priv)
            settings["identity"] = {"prot": method, "key": blob.hex(), "pub": ident.pub.hex(), "id": ident.device_id}
            _identity_cache[(method, blob.hex(), ident.device_id)] = ident
        except Exception:
            settings["identity"] = {}   # DPAPI が使えない: この起動の間だけ使う (保存しない)
            _ephemeral.append(ident)
        return ident


def sanitize_identity(value):
    """設定に残してよい形か。"""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ("prot", "key", "pub", "id"):
        item = value.get(key)
        if not isinstance(item, str) or len(item) > 4096:
            return {}
        out[key] = item
    return out if out["prot"] in ("dpapi", "plain") else {}


def log_name(value):
    """接続ログ用の伏せ字: 末尾 4 文字だけ残す (USB シリアルや IP)。"""
    text = str(value or "")
    if not text:
        return "-"
    return "…" + text[-4:] if len(text) > 4 else "…" + text


# ---------------------------------------------------------------------------
# PC 側のハンドシェイク (クライアント)
# ---------------------------------------------------------------------------

def _rand16():
    return os.urandom(16)


def _need(hs, key, nbytes):
    value = hs.get(key) if isinstance(hs, dict) else None
    if not is_hex(value, nbytes):
        raise HandshakeError("bad", "bad message")
    return bytes.fromhex(value)


class PairInitiator:
    """コード方式ペアリング (PC が始める)。メッセージ番号は docs/PROTOCOL.md と同じ。
    rand/eph は固定値でテストするための差し込み口。"""

    def __init__(self, identity, name, eph_priv=None, nonce=None):
        self.identity = identity
        self.name = name
        self.e_priv = eph_priv or os.urandom(32)
        self.e_pub = x25519_public(self.e_priv)
        self.n = nonce or _rand16()
        self.m1 = self.m2 = self.m3 = None
        self.code = self.th = self.key = self.channel = None

    def start(self):
        commit = sha256(self.e_pub, self.n)
        self.m1 = hs_encode({"v": HS_VERSION, "mode": "pair", "commit": commit.hex(), "name": self.name})
        return self.m1

    def on_reply(self, m2):
        """iPhone の {"e","n"} を受けて、こちらの {"e","n"} (m3) を返す。同時に照合コードを計算する。"""
        hs = hs_parse(m2)
        e_ph = _need(hs, "e", 32)
        _need(hs, "n", 16)
        self.m2 = m2
        self.m3 = hs_encode({"e": self.e_pub.hex(), "n": self.n.hex()})
        self.th = sha256(self.m1, self.m2, self.m3)
        ss = x25519(self.e_priv, e_ph)
        self.code = sas_code(ss, self.th)
        self.key = pair_key(ss, self.th)
        self.channel = pair_channel(self.key, True)
        return self.m3

    def code_matches(self, typed):
        return isinstance(typed, str) and hmac.compare_digest(typed.encode("utf-8", "replace"), self.code.encode("ascii"))

    def confirm_message(self):
        return hs_encode({"confirm": confirm_mac(self.key, b"pc", self.th).hex()})

    def identity_frame(self):
        """AEAD で包んだ自分の静的公開鍵・名前・ID。"""
        body = hs_encode({"s": self.identity.pub.hex(), "name": self.name, "id": self.identity.device_id})
        return self.channel.seal(MSG_CONTROL, body)

    def open_peer(self, ct):
        """iPhone の AEAD (m6) を開く -> {"s": 公開鍵(bytes), "name", "id", "model", "addresses"}。"""
        msg_type, body = self.channel.open(ct)
        hs = hs_parse(body) if msg_type == MSG_CONTROL else None
        return parse_peer(hs)


def parse_peer(hs):
    """相手の自己紹介 ({"s","name","id",...}) を検証して取り出す。"""
    if not isinstance(hs, dict):
        raise HandshakeError("bad", "bad message")
    pub = _need(hs, "s", 32)
    ident = hs.get("id")
    try:
        ident = str(uuid.UUID(ident))
    except (ValueError, TypeError, AttributeError):
        raise HandshakeError("bad", "bad id")
    name = hs.get("name") if isinstance(hs.get("name"), str) else ""
    model = hs.get("model") if isinstance(hs.get("model"), str) else ""
    addresses = hs.get("addresses") if isinstance(hs.get("addresses"), list) else []
    return {"s": pub, "name": name[:128], "id": ident, "model": model[:128], "addresses": addresses[:32]}


class ConnectInitiator:
    """通常接続 (両者がピン留め済み)。lookup(fp) は保存済みの iPhone の公開鍵 (bytes) か None を返す。"""

    def __init__(self, identity, eph_priv=None, nonce=None):
        self.identity = identity
        self.e_priv = eph_priv or os.urandom(32)
        self.e_pub = x25519_public(self.e_priv)
        self.n = nonce or _rand16()
        self.c1 = self.c2 = None
        self.th = self.keys = None
        self.peer_pub = None

    def start(self):
        self.c1 = hs_encode({"v": HS_VERSION, "mode": "connect", "e": self.e_pub.hex(), "n": self.n.hex(),
                             "sf": self.identity.fp})
        return self.c1

    def on_reply(self, c2, lookup):
        hs = hs_parse(c2)
        e_ph = _need(hs, "e", 32)
        _need(hs, "n", 16)
        tf = hs.get("tf")
        if not isinstance(tf, str) or len(tf) > 64:
            raise HandshakeError("bad", "bad message")
        s_ph = lookup(tf)
        if s_ph is None:
            raise HandshakeError("unpaired", "peer not pinned")
        self.c2 = c2
        self.peer_pub = s_ph
        self.th = sha256(self.c1, self.c2)
        ikm = x25519(self.e_priv, e_ph) + x25519(self.identity.priv, e_ph) + x25519(self.e_priv, s_ph)
        self.keys = derive_session(ikm, self.th)
        return self.keys

    def confirm_message(self):
        return hs_encode({"confirm": confirm_mac(self.keys.k_confirm, b"pc", self.th).hex()})

    def check_confirm(self, hs):
        mac = _need(hs, "confirm", 32)
        if not hmac.compare_digest(mac, confirm_mac(self.keys.k_confirm, b"ph", self.th)):
            raise HandshakeError("mismatch", "confirm mismatch")


class QrResponder:
    """QR 方式 (PC が待ち受ける側)。iPhone が q1 を送ってくる。PC は自分の静的公開鍵 s を q2 で送る。"""

    def __init__(self, identity, token, eph_priv=None, nonce=None):
        self.identity = identity
        self.token = token
        self.e_priv = eph_priv or os.urandom(32)
        self.e_pub = x25519_public(self.e_priv)
        self.n = nonce or _rand16()
        self.q1 = self.q2 = self.th = self.keys = None

    def on_q1(self, q1):
        hs = hs_parse(q1)
        if not isinstance(hs, dict) or hs.get("mode") != "qr" or hs.get("v") != HS_VERSION:
            raise HandshakeError("bad", "bad message")
        e_ph = _need(hs, "e", 32)
        _need(hs, "n", 16)
        self.q1 = q1
        self.q2 = hs_encode({"e": self.e_pub.hex(), "n": self.n.hex(), "s": self.identity.pub.hex()})
        self.th = sha256(self.q1, self.q2)
        ikm = x25519(self.e_priv, e_ph) + x25519(self.identity.priv, e_ph)
        self.keys = derive_session(ikm, self.th, INFO_QR)
        return self.q2

    def confirm_message(self):
        return hs_encode({"confirm": confirm_mac(self.keys.k_confirm, b"pc", self.th).hex()})

    def channel(self):
        return session_channel(self.keys, True)

    def check_token(self, value):
        return isinstance(value, str) and hmac.compare_digest(value.encode("utf-8", "replace"), self.token.encode("ascii"))

    def reply_frame(self, channel, name):
        body = hs_encode({"ok": True, "name": name, "id": self.identity.device_id})
        return channel.seal(MSG_CONTROL, body)
