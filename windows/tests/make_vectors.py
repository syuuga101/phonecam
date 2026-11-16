"""docs/test-vectors/handshake-v2.json を作る (固定の鍵と乱数)。Python (pytest) と Swift (ios/Tests/HandshakeChecks.swift) の
両方が、この値と一致することを確かめる。作り直すとき: python windows/tests/make_vectors.py (windows/ から)。"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from phonecam.core import secure  # noqa: E402
from phonecam.core.protocol import MSG_CONTROL, MSG_STATUS  # noqa: E402

PATH = os.path.join(HERE, "..", "..", "docs", "test-vectors", "handshake-v2.json")


def seed(label):
    return hashlib.sha256(b"phonecam test vector " + label.encode()).digest()


def keys_dict(keys):
    return {name: getattr(keys, name).hex() for name in secure._OFF}


def build():
    s_pc, s_ph = seed("s_pc"), seed("s_ph")
    e_pc, e_ph = seed("e_pc"), seed("e_ph")
    n_pc, n_ph = seed("n_pc")[:16], seed("n_ph")[:16]
    pc_id, ph_id = "11111111-2222-4333-8444-555555555555", "9bd344c9-765e-4cb6-ae81-4c77f064131a"
    pc_name = "PC-A"
    pc = secure.Identity(s_pc, pc_id)
    ph_pub = secure.x25519_public(s_ph)
    e_pc_pub, e_ph_pub = secure.x25519_public(e_pc), secure.x25519_public(e_ph)
    out = {
        "version": 2,
        "inputs": {
            "s_pc_priv": s_pc.hex(), "s_ph_priv": s_ph.hex(), "e_pc_priv": e_pc.hex(), "e_ph_priv": e_ph.hex(),
            "n_pc": n_pc.hex(), "n_ph": n_ph.hex(), "pc_name": pc_name, "pc_id": pc_id, "ph_id": ph_id,
            "qr_token": "tok-vector-0123456789abcdefghijkl",
        },
        "public": {
            "s_pc_pub": pc.pub.hex(), "s_ph_pub": ph_pub.hex(), "e_pc_pub": e_pc_pub.hex(), "e_ph_pub": e_ph_pub.hex(),
            "s_pc_fp": secure.fingerprint(pc.pub), "s_ph_fp": secure.fingerprint(ph_pub),
        },
    }

    # コード方式ペアリング
    init = secure.PairInitiator(pc, pc_name, eph_priv=e_pc, nonce=n_pc)
    m1 = init.start()
    m2 = secure.hs_encode({"e": e_ph_pub.hex(), "n": n_ph.hex()})
    m3 = init.on_reply(m2)
    ss = secure.x25519(e_pc, e_ph_pub)
    channel_pc = secure.pair_channel(init.key, True)
    channel_ph = secure.pair_channel(init.key, False)
    ping1, ping2 = b'{"ping":1}', b'{"ping":2}'
    out["pair"] = {
        "m1": m1.decode(), "m2": m2.decode(), "m3": m3.decode(),
        "commit": secure.sha256(e_pc_pub, n_pc).hex(),
        "ss": ss.hex(), "th": init.th.hex(), "code": init.code, "key": init.key.hex(),
        "confirm_pc": secure.confirm_mac(init.key, b"pc", init.th).hex(),
        "frame_pc2ph_0": channel_pc.seal(MSG_CONTROL, ping1).hex(),
        "frame_ph2pc_0": channel_ph.seal(MSG_STATUS, b'{"fps":30}').hex(),
    }

    # 通常接続
    ci = secure.ConnectInitiator(pc, eph_priv=e_pc, nonce=n_pc)
    c1 = ci.start()
    c2 = secure.hs_encode({"e": e_ph_pub.hex(), "n": n_ph.hex(), "tf": secure.fingerprint(ph_pub)})
    keys = ci.on_reply(c2, lambda fp: ph_pub)
    ikm = secure.x25519(e_pc, e_ph_pub) + secure.x25519(s_pc, e_ph_pub) + secure.x25519(e_pc, ph_pub)
    pc_chan = secure.session_channel(keys, True)
    ph_chan = secure.session_channel(keys, False)
    audio_ph = secure.session_channel(keys, False, audio=True)
    audio_pc = secure.session_channel(keys, True, audio=True)
    out["connect"] = {
        "c1": c1.decode(), "c2": c2.decode(), "ikm": ikm.hex(), "th": ci.th.hex(), "keys": keys_dict(keys),
        "confirm_pc": secure.confirm_mac(keys.k_confirm, b"pc", ci.th).hex(),
        "confirm_ph": secure.confirm_mac(keys.k_confirm, b"ph", ci.th).hex(),
        "frame_pc2ph_0": pc_chan.seal(MSG_CONTROL, ping1).hex(),
        "frame_pc2ph_1": pc_chan.seal(MSG_CONTROL, ping2).hex(),
        "frame_ph2pc_0": ph_chan.seal(MSG_STATUS, b'{"fps":30}').hex(),
        "audio_frame_pc2ph_0": audio_pc.seal(MSG_CONTROL, ("{\"hs\":{\"audio_tok\":\"%s\"}}" % keys.audio_tok.hex()).encode()).hex(),
        "audio_frame_ph2pc_0": audio_ph.seal(6, bytes(range(32))).hex(),
    }

    # QR 方式
    qr = secure.QrResponder(pc, out["inputs"]["qr_token"], eph_priv=e_pc, nonce=n_pc)
    q1 = secure.hs_encode({"v": 2, "mode": "qr", "e": e_ph_pub.hex(), "n": n_ph.hex()})
    q2 = qr.on_q1(q1)
    qr_pc, qr_ph = secure.session_channel(qr.keys, True), secure.session_channel(qr.keys, False)
    out["qr"] = {
        "q1": q1.decode(), "q2": q2.decode(), "th": qr.th.hex(), "keys": keys_dict(qr.keys),
        "confirm_pc": secure.confirm_mac(qr.keys.k_confirm, b"pc", qr.th).hex(),
        "frame_ph2pc_0": qr_ph.seal(MSG_CONTROL, b'{"hs":{"tok":"x"}}').hex(),
        "frame_pc2ph_0": qr_pc.seal(MSG_CONTROL, ping1).hex(),
    }
    return out


def main():
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("wrote", os.path.abspath(PATH))


if __name__ == "__main__":
    main()
