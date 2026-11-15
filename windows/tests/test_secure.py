"""v2 の暗号の核 (core/secure.py): テストベクタ・通信路 (改ざん/再送/順序)・長期鍵・伏せ字。ネットワークは使わない。"""
import json
import os
import struct
import sys

import pytest

from phonecam.core import secure
from phonecam.core.protocol import MSG_CONTROL, MSG_SECURE, MSG_STATUS, encode_msg, payload_limit
from phonecam.core.secure import Channel, HandshakeError, SecureError, Wire

sys.path.insert(0, os.path.dirname(__file__))
import make_vectors  # noqa: E402

VECTOR_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "test-vectors", "handshake-v2.json")


def test_vector_file_matches_python():
    """docs/test-vectors/handshake-v2.json は、固定の鍵と乱数からいまの実装が作る値と一致する (Swift も同じ値と照合する)。"""
    with open(VECTOR_FILE, encoding="utf-8") as f:
        assert json.load(f) == make_vectors.build()


def test_vector_values_are_computed_independently():
    """ベクタの値を、実装の関数を通さずに RFC どおりの部品で計算し直して確かめる (実装と同じ誤りを写さないため)。"""
    import hashlib
    import hmac
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    v = make_vectors.build()
    m1, m2, m3 = (v["pair"][k].encode() for k in ("m1", "m2", "m3"))
    th = hashlib.sha256(m1 + m2 + m3).digest()
    assert th.hex() == v["pair"]["th"]
    ss = bytes.fromhex(v["pair"]["ss"])
    sas = HKDF(hashes.SHA256(), 4, th, b"phonecam pair sas").derive(ss)
    assert "%06d" % (struct.unpack(">I", sas)[0] % 1_000_000) == v["pair"]["code"]
    key = HKDF(hashes.SHA256(), 32, th, b"phonecam pair key").derive(ss)
    assert key.hex() == v["pair"]["key"]
    assert hmac.new(key, b"pc" + th, hashlib.sha256).hexdigest() == v["pair"]["confirm_pc"]
    c1, c2 = v["connect"]["c1"].encode(), v["connect"]["c2"].encode()
    th2 = hashlib.sha256(c1 + c2).digest()
    block = HKDF(hashes.SHA256(), 192, th2, b"phonecam v2 session").derive(bytes.fromhex(v["connect"]["ikm"]))
    assert block[:32].hex() == v["connect"]["keys"]["k_pc2ph"]
    assert block[32:64].hex() == v["connect"]["keys"]["k_ph2pc"]
    assert block[64:80].hex() == v["connect"]["keys"]["audio_tok"]
    # 先頭の暗号文: nonce = 固定 4 バイト + カウンタ (ビッグエンディアン 8 バイト)、AAD = 外側のヘッダ
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    inner = bytes([10]) + b'{"ping":1}'
    header = struct.pack(">BI", 20, len(inner) + 16)
    nonce = block[80:84] + struct.pack(">Q", 0)
    assert (header + ChaCha20Poly1305(block[:32]).encrypt(nonce, inner, header)).hex() == v["connect"]["frame_pc2ph_0"]


def test_sas_code_is_six_digits_and_depends_on_transcript():
    codes = {secure.sas_code(b"\x01" * 32, secure.sha256(bytes([i]))) for i in range(50)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 40


def test_fingerprint_shape():
    fp = secure.fingerprint(b"\x01" * 32)
    assert len(fp) == 26 and fp.isalnum() and fp == fp.upper()
    assert secure.fp_equal(fp, fp.lower())


def test_low_order_public_key_is_rejected():
    with pytest.raises(HandshakeError):
        secure.x25519(os.urandom(32), b"\x00" * 32)


def make_channels():
    k1, k2 = os.urandom(32), os.urandom(32)
    pc = Channel(k1, b"AAAA", k2, b"BBBB")
    ph = Channel(k2, b"BBBB", k1, b"AAAA")
    return pc, ph


def test_channel_round_trip_and_counters():
    pc, ph = make_channels()
    for i in range(5):
        frame = pc.seal(MSG_CONTROL, b"hello %d" % i)
        assert frame[0] == MSG_SECURE and b"hello" not in frame
        assert ph.open(frame[5:]) == (MSG_CONTROL, b"hello %d" % i)
    assert pc.send_count == 5 and ph.recv_count == 5


def test_channel_rejects_tampering_replay_and_reordering():
    pc, ph = make_channels()
    first, second = pc.seal(MSG_STATUS, b"one"), pc.seal(MSG_STATUS, b"two")
    flipped = bytearray(first)
    flipped[10] ^= 1
    with pytest.raises(SecureError):
        ph.open(bytes(flipped)[5:])
    assert ph.recv_count == 0
    with pytest.raises(SecureError):        # 順序の入れ替え: 2 つ目を先に
        ph.open(second[5:])
    assert ph.open(first[5:]) == (MSG_STATUS, b"one")
    with pytest.raises(SecureError):        # 再送
        ph.open(first[5:])
    assert ph.open(second[5:]) == (MSG_STATUS, b"two")


def test_channel_directions_do_not_mix():
    pc, ph = make_channels()
    frame = ph.seal(MSG_STATUS, b"x")
    with pytest.raises(SecureError):
        ph.open(frame[5:])                  # 自分が送ったものを受け取れない (方向別の鍵)


class FakeSock:
    def __init__(self, data):
        self.data = bytearray(data)

    def recv_into(self, view, n):
        chunk = self.data[:n]
        del self.data[:n]
        view[:len(chunk)] = chunk
        return len(chunk)


def test_wire_secure_rejects_plain_and_plain_rejects_secure():
    pc, ph = make_channels()
    secure_wire = Wire(ph)
    with pytest.raises(SecureError):
        secure_wire.read(FakeSock(encode_msg(MSG_STATUS, b"{}")))
    plain_wire = Wire(None)
    with pytest.raises(SecureError):
        plain_wire.read(FakeSock(pc.seal(MSG_STATUS, b"{}")))


def test_wire_reads_encrypted_and_plain_messages():
    pc, ph = make_channels()
    assert Wire(ph).read(FakeSock(pc.seal(MSG_STATUS, b'{"a":1}'))) == (MSG_STATUS, b'{"a":1}')
    assert Wire(None).read(FakeSock(encode_msg(MSG_STATUS, b'{"a":1}'))) == (MSG_STATUS, b'{"a":1}')


def test_wire_pushback_comes_first():
    wire = Wire(None, legacy=True)
    wire.push_back(MSG_STATUS, b"{}")
    assert wire.read(FakeSock(b"")) == (MSG_STATUS, b"{}")


def test_wire_cap_applies_to_decrypted_size():
    pc, ph = make_channels()
    with pytest.raises(secure.LimitError):
        Wire(ph).read(FakeSock(pc.seal(6, b"x" * 1000)), cap=100)         # 外側が大きすぎる: 復号せずに切る
    pc, ph = make_channels()
    with pytest.raises(secure.LimitError):
        Wire(ph).read(FakeSock(pc.seal(6, b"x" * 100)), cap=99)           # 復号したあとの大きさも見る


def test_secure_payload_limit():
    assert payload_limit(MSG_SECURE, True) > payload_limit(1, True)
    assert payload_limit(MSG_SECURE, False) == 64 * 1024


def test_identity_is_created_once_and_reloaded():
    settings = {}
    first = secure.load_identity(settings)
    assert settings["identity"]["pub"] == first.pub.hex()
    again = secure.load_identity(dict(settings))
    assert again.priv == first.priv and again.device_id == first.device_id
    assert secure.sanitize_identity(settings["identity"]) == settings["identity"]
    assert secure.sanitize_identity({"prot": "bad"}) == {}
    assert secure.sanitize_identity(None) == {}


def test_identity_secret_is_not_stored_in_plain_on_windows(monkeypatch):
    """Windows では DPAPI の結果だけを保存する (秘密鍵そのものを設定に書かない)。DPAPI は差し替えて確かめる。"""
    monkeypatch.setattr(secure.sys, "platform", "win32")
    calls = []

    def fake_dpapi(data, protect):
        calls.append(protect)
        return bytes(b ^ 0x5A for b in data)     # 可逆だが元と違う

    monkeypatch.setattr(secure, "_dpapi", fake_dpapi)
    settings = {}
    ident = secure.load_identity(settings)
    assert settings["identity"]["prot"] == "dpapi"
    assert ident.priv.hex() != settings["identity"]["key"] and ident.priv.hex() not in json.dumps(settings)
    secure._identity_cache.clear()
    assert secure.load_identity(dict(settings)).priv == ident.priv and calls == [True, False]


def test_log_name_masks_all_but_last_four():
    assert secure.log_name("192.168.1.23") == "…1.23"
    assert secure.log_name("00008110-000A1B2C3D4E5F") == "…4E5F"
    assert secure.log_name("") == "-"
    assert "192.168" not in secure.log_name("192.168.1.23")
