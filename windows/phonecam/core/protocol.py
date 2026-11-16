# -*- coding: utf-8 -*-
"""iPhone との通信プロトコル: ポート・メッセージ種別・フレーミング・pong/timing/audio の解析
"""

import json
import math
import socket
import struct
import time
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


MSG_SECURE = 20      # 双方向 (v2): AEAD で包んだメッセージ。中身は [種別 1 バイト][本文]。Wi-Fi の認証後はこれだけを使う


SECURE_OVERHEAD = 1 + 16   # 中の種別 1 バイト + 認証タグ 16 バイト


# ---------------------------------------------------------------------------
# 受信の上限と期限 (監査 #7・#8)
# ---------------------------------------------------------------------------

MAX_PREAUTH_PAYLOAD = 64 * 1024        # 認証が済むまで (Wi-Fi) は、どの種別でもこれより大きいメッセージを受けない
MAX_SMALL_PAYLOAD = 64 * 1024          # 状態・pong・timing・制御など (JSON や 16 byte)
MAX_VIDEO_PAYLOAD = 16 * 1024 * 1024   # 映像 1 フレーム (4K のキーフレームでも数 MiB に収まる)
MAX_AUDIO_PAYLOAD = 256 * 1024         # 音声 1 パケット (通常は数 KiB)
MAX_USBMUX_LENGTH = 1024 * 1024        # usbmux が宣言する 1 メッセージ (ListDevices でも数 KiB)
MAX_AUDIO_SECONDS = 1.0                # 音声 1 パケットが含む長さ (通常は 20〜100 ms)
MAX_AUDIO_CHANNELS = 8
MAX_AUDIO_OUT_BYTES = 4 * 1024 * 1024  # 変換・補間のあとの float32 の見込みサイズ

AUTH_TIMEOUT_SEC = 10.0                # 接続してから認証が済むまで
RX_IDLE_SEC = 5.0                      # 接続後、1 バイトも届かない時間の上限 (状態通知や pong が生存確認)
USBMUX_READ_SEC = 5.0                  # usbmux の 1 メッセージを読み切るまで


class LimitError(ConnectionError):
    """受信の上限・期限を超えたときの基底。切断してよい (再接続や USB/Wi-Fi の切り替えに進む)。"""

    def log_line(self):
        return str(self)


class PayloadTooLarge(LimitError):
    def __init__(self, what, size, limit):
        super().__init__(t("core.protocol.bad_length"))
        self.what, self.size, self.limit = what, size, limit

    def log_line(self):
        return "受信上限を超えたので切断: %s 長さ=%d 上限=%d" % (self.what, self.size, self.limit)


class ReceiveTimeout(LimitError):
    def __init__(self, what, seconds):
        super().__init__(t("core.protocol.timeout"))
        self.what, self.seconds = what, seconds

    def log_line(self):
        return "受信の期限が来たので切断: %s %.1f秒" % (self.what, self.seconds)


def payload_limit(msg_type, authed=True):
    """種別と認証段階ごとの、1 メッセージの長さの上限。"""
    if not authed:
        return MAX_PREAUTH_PAYLOAD
    if msg_type == MSG_SECURE:
        return MAX_VIDEO_PAYLOAD + SECURE_OVERHEAD
    if msg_type in (MSG_VIDEO, MSG_VIDEO_HEVC):
        return MAX_VIDEO_PAYLOAD
    if msg_type == MSG_AUDIO:
        return MAX_AUDIO_PAYLOAD
    return MAX_SMALL_PAYLOAD


class PhoneCamError(Exception):
    """利用者に見せる日本語メッセージ付きのエラー"""


# ---------------------------------------------------------------------------
# フレーミング
# ---------------------------------------------------------------------------

def encode_msg(msg_type, payload):
    return struct.pack(">BI", msg_type, len(payload)) + payload


def read_exact(sock, n, stop_event=None, idle=None, deadline=None, what="受信"):
    """ちょうど n バイト読む。ソケットのタイムアウトごとに stop_event を確認して継続する。
    idle (秒): 1 バイトも進まない時間がこれに達したら ReceiveTimeout。
    deadline (time.monotonic の時刻): これを過ぎたら ReceiveTimeout。どちらも None なら従来どおり待ち続ける。
    正常に届いているあいだは時計を見ない (idle は、タイムアウトのときだけ前回から進んだかを見て測る)。"""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    timed = idle is not None or deadline is not None
    if timed:
        mark_got, mark_t = 0, time.monotonic()
    while got < n:
        try:
            r = sock.recv_into(view[got:], n - got)
        except socket.timeout:
            if stop_event is not None and stop_event.is_set():
                raise ConnectionAbortedError("stopped")
            if timed:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise ReceiveTimeout(what, idle if idle is not None else 0.0)
                if got != mark_got:
                    mark_got, mark_t = got, now
                elif idle is not None and now - mark_t >= idle:
                    raise ReceiveTimeout(what, now - mark_t)
            continue
        if r == 0:
            raise ConnectionError(t("core.protocol.closed"))
        got += r
        if deadline is not None and time.monotonic() >= deadline and got < n:
            raise ReceiveTimeout(what, 0.0)
    return bytes(buf)


def read_msg(sock, stop_event=None, authed=True, idle=None, deadline=None, cap=None):
    """1 メッセージ読む。長さは種別・認証段階ごとの上限 (payload_limit、cap があればそれ以下) を超えたら、
    本体を読まずに PayloadTooLarge で切る。idle / deadline は read_exact と同じ。"""
    header = read_exact(sock, 5, stop_event, idle, deadline, "ヘッダ")
    msg_type, length = struct.unpack(">BI", header)
    limit = payload_limit(msg_type, authed)
    if cap is not None and cap < limit:
        limit = cap
    if length > limit:
        raise PayloadTooLarge("type=%d%s" % (msg_type, "" if authed else " (認証前)"), length, limit)
    payload = read_exact(sock, length, stop_event, idle, deadline, "本体") if length else b""
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
    if not (math.isfinite(sent) and math.isfinite(phone_t)):  # NaN / inf
        return None
    return sent, phone_t


def parse_timing(payload):
    """type 5 (16 byte: float64 BE captureTime, float64 BE sendTime) → (capture, send)。不正なら None。"""
    if len(payload) < 16:
        return None
    capture, send = struct.unpack(">dd", payload[:16])
    if not (math.isfinite(capture) and math.isfinite(send)):  # NaN / inf
        return None
    return capture, send


AUDIO_HEADER_SIZE = 13  # float64 + uint32 + uint8


def audio_estimate(payload_len, rate, channels, dst_rate=None):
    """音声パケットの (フレーム数, 変換後の float32 の見込みバイト数)。変換する前に上限と比べるために使う。"""
    frames = max(0, payload_len - AUDIO_HEADER_SIZE) // 2 // max(1, channels)
    out_frames = frames * (dst_rate / rate) if dst_rate else frames
    return frames, int(out_frames * 4) + 4


def parse_audio(payload, dst_rate=None):
    """type 6 → (captureTime, sampleRate, samples)。samples は float32 の mono (-1〜1)。不正なら None。
    サンプル数や変換後 (dst_rate があればそのレート) の見込みサイズが上限を超えるときは PayloadTooLarge。"""
    if np is None or len(payload) < AUDIO_HEADER_SIZE:
        return None
    capture, rate, channels = struct.unpack(">dIB", payload[:AUDIO_HEADER_SIZE])
    if rate < 8000 or rate > 192000 or channels < 1 or not math.isfinite(capture):
        return None
    if len(payload) > MAX_AUDIO_PAYLOAD:
        raise PayloadTooLarge("音声パケット", len(payload), MAX_AUDIO_PAYLOAD)
    if channels > MAX_AUDIO_CHANNELS:
        raise PayloadTooLarge("音声チャンネル数", channels, MAX_AUDIO_CHANNELS)
    frames, out_bytes = audio_estimate(len(payload), rate, channels, dst_rate)
    if frames > rate * MAX_AUDIO_SECONDS:
        raise PayloadTooLarge("音声サンプル数", frames, int(rate * MAX_AUDIO_SECONDS))
    if out_bytes > MAX_AUDIO_OUT_BYTES:
        raise PayloadTooLarge("音声の変換後の見込みサイズ", out_bytes, MAX_AUDIO_OUT_BYTES)
    body = payload[AUDIO_HEADER_SIZE:]
    body = body[: len(body) // 2 * 2]
    if not body:
        return None
    pcm = np.frombuffer(body, dtype="<i2").astype(np.float32) * (1.0 / 32768.0)
    if channels > 1:
        n = len(pcm) // channels * channels
        pcm = pcm[:n].reshape(-1, channels).mean(axis=1)
    return capture, int(rate), pcm
