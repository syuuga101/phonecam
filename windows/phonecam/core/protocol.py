# -*- coding: utf-8 -*-
"""iPhone との通信プロトコル: ポート・メッセージ種別・フレーミング・pong/timing/audio の解析
"""

import json
import socket
import struct
from phonecam.core.deps import np
from phonecam.i18n import t


PHONE_PORT = 5555


AUDIO_PORT = 5556              # 音声専用チャンネル (v1.4): 映像の後ろで待たされない


MAIN_RCVBUF = 512 * 1024       # 映像の受信バッファ。大きいと詰まった映像がここに溜まって遅延になる


AUDIO_RCVBUF = 64 * 1024


AUDIO_RETRY_SEC = 2.0


USBMUX_ADDR = ("127.0.0.1", 27015)


RECONNECT_SEC = 2.0


AUTH_REFUSED_RETRY_SEC = 10.0   # Wi-Fi 接続を断られたときの再試行間隔 (締め出しを避けるため長めに)


MSG_VIDEO = 1        # H.264 アクセスユニット


MSG_VIDEO_HEVC = 2   # HEVC アクセスユニット (Annex B)


MSG_STATUS = 3


MSG_PONG = 4         # iPhone → PC: ping への応答 (JSON)


MSG_TIMING = 5       # iPhone → PC: 次の映像パケットの時刻情報 (16 byte)


MSG_AUDIO = 6        # iPhone → PC: マイク音声 (int16 PCM)


MSG_CONTROL = 10


class PhoneCamError(Exception):
    """利用者に見せる日本語メッセージ付きのエラー"""


# ---------------------------------------------------------------------------
# フレーミング
# ---------------------------------------------------------------------------

def encode_msg(msg_type, payload):
    return struct.pack(">BI", msg_type, len(payload)) + payload


def read_exact(sock, n, stop_event=None):
    """ちょうど n バイト読む。タイムアウト時は stop_event を確認して継続する。"""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        try:
            r = sock.recv_into(view[got:], n - got)
        except socket.timeout:
            if stop_event is not None and stop_event.is_set():
                raise ConnectionAbortedError("stopped")
            continue
        if r == 0:
            raise ConnectionError(t("core.protocol.closed"))
        got += r
    return bytes(buf)


def read_msg(sock, stop_event=None):
    header = read_exact(sock, 5, stop_event)
    msg_type, length = struct.unpack(">BI", header)
    if length > 64 * 1024 * 1024:
        raise ConnectionError(t("core.protocol.bad_length"))
    payload = read_exact(sock, length, stop_event) if length else b""
    return msg_type, payload


# ---------------------------------------------------------------------------
# v1.3 パケット解析・遅延計測・温度履歴 (GUI/ネットワークに依存しない純粋なロジック)
# ---------------------------------------------------------------------------

def parse_pong(payload):
    """type 4 (JSON) → (PCが送った値, iPhoneの時刻[秒])。不正なら None。"""
    try:
        d = json.loads(payload.decode("utf-8"))
        sent = float(d["pong"])
        phone_t = float(d["t"])
    except Exception:
        return None
    if sent != sent or phone_t != phone_t:  # NaN
        return None
    return sent, phone_t


def parse_timing(payload):
    """type 5 (16 byte: float64 BE captureTime, float64 BE sendTime) → (capture, send)。不正なら None。"""
    if len(payload) < 16:
        return None
    capture, send = struct.unpack(">dd", payload[:16])
    if capture != capture or send != send:
        return None
    return capture, send


AUDIO_HEADER_SIZE = 13  # float64 + uint32 + uint8


def parse_audio(payload):
    """type 6 → (captureTime, sampleRate, samples)。samples は float32 の mono (-1〜1)。不正なら None。"""
    if np is None or len(payload) < AUDIO_HEADER_SIZE:
        return None
    capture, rate, channels = struct.unpack(">dIB", payload[:AUDIO_HEADER_SIZE])
    if rate < 8000 or rate > 192000:
        return None
    body = payload[AUDIO_HEADER_SIZE:]
    body = body[: len(body) // 2 * 2]
    if not body:
        return None
    pcm = np.frombuffer(body, dtype="<i2").astype(np.float32) * (1.0 / 32768.0)
    if channels > 1:
        n = len(pcm) // channels * channels
        pcm = pcm[:n].reshape(-1, channels).mean(axis=1)
    return capture, int(rate), pcm
