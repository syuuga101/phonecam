"""監査 #7 (受信のタイムアウト) と #8 (受信の上限) のテスト。偽のソケットを使い、PySide6 なしで動く。"""
import socket
import struct
import threading

import pytest

from phonecam.core import crashlog, protocol, usbmux
from phonecam.core.deps import np
from phonecam.core.protocol import (
    MAX_AUDIO_OUT_BYTES, MAX_AUDIO_PAYLOAD, MAX_PREAUTH_PAYLOAD, MAX_SMALL_PAYLOAD, MAX_USBMUX_LENGTH,
    MAX_VIDEO_PAYLOAD, MSG_AUDIO, MSG_STATUS, MSG_VIDEO, MSG_VIDEO_HEVC, LimitError, PayloadTooLarge,
    ReceiveTimeout, encode_msg, parse_audio, payload_limit, read_exact, read_msg)


class FakeClock:
    """time.monotonic の代わり。偽のソケットがタイムアウトするたびに進む。"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSock:
    """script: bytes (届く) か None (socket.timeout) の列。尽きたら無期限に timeout を返す (上限つき)。"""

    def __init__(self, script, clock=None, step=1.0):
        self.script, self.clock, self.step = list(script), clock, step
        self.timeouts = 0

    def recv_into(self, view, n):
        if self.script:
            item = self.script.pop(0)
        else:
            item = None
        if item is None:
            self.timeouts += 1
            assert self.timeouts < 1000, "期限がなく無限に待っている"
            if self.clock is not None:
                self.clock.now += self.step
            raise socket.timeout()
        if item == b"":
            return 0
        chunk = item[:n]
        view[:len(chunk)] = chunk
        if len(item) > n:
            self.script.insert(0, item[n:])
        return len(chunk)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(protocol.time, "monotonic", c)
    return c


# -- #7 タイムアウト ------------------------------------------------------------------

def test_silent_peer_raises_within_idle(clock):
    sock = FakeSock([], clock)
    with pytest.raises(ReceiveTimeout):
        read_msg(sock, None, True, protocol.RX_IDLE_SEC)
    assert 5 <= sock.timeouts <= 6          # 5 秒 (1 秒のタイムアウト 5 回) で諦める


def test_stalled_in_middle_of_frame_raises(clock):
    packet = encode_msg(MSG_VIDEO, b"x" * 100)
    sock = FakeSock([packet[:50]], clock)    # 途中で止まる
    with pytest.raises(ReceiveTimeout):
        read_msg(sock, None, True, 5.0)


def test_slow_but_progressing_is_not_cut(clock):
    packet = encode_msg(MSG_VIDEO, b"y" * 20)
    script = []
    for i in range(len(packet)):
        script += [None, None, None, packet[i:i + 1]]    # 3 秒ごとに 1 バイト (idle 5 秒より短い)
    sock = FakeSock(script, clock)
    assert read_msg(sock, None, True, 5.0) == (MSG_VIDEO, b"y" * 20)


def test_auth_deadline_cuts_even_when_bytes_trickle(clock):
    packet = encode_msg(MSG_STATUS, b"{}" * 10)
    script = []
    for i in range(len(packet)):
        script += [None, packet[i:i + 1]]
    sock = FakeSock(script, clock)
    with pytest.raises(ReceiveTimeout):
        read_msg(sock, None, False, protocol.AUTH_TIMEOUT_SEC, clock.now + 3.0)


def test_stop_event_still_stops():
    stop = threading.Event()
    stop.set()
    with pytest.raises(ConnectionAbortedError):
        read_exact(FakeSock([]), 5, stop, idle=5.0)


def test_no_idle_keeps_old_behavior_until_stop():
    stop = threading.Event()

    class Sock(FakeSock):
        def recv_into(self, view, n):
            self.timeouts += 1
            if self.timeouts == 20:
                stop.set()
            raise socket.timeout()

    with pytest.raises(ConnectionAbortedError):
        read_exact(Sock([]), 5, stop)


def test_closed_connection_is_error():
    with pytest.raises(ConnectionError):
        read_exact(FakeSock([b"ab", b""]), 5)


def test_timeout_is_a_connection_error_so_link_reconnects():
    assert issubclass(ReceiveTimeout, ConnectionError) and issubclass(LimitError, OSError)


# -- #8 上限 -------------------------------------------------------------------------

@pytest.mark.parametrize(("msg_type", "authed", "limit"), [
    (MSG_VIDEO, True, MAX_VIDEO_PAYLOAD), (MSG_VIDEO_HEVC, True, MAX_VIDEO_PAYLOAD),
    (MSG_AUDIO, True, MAX_AUDIO_PAYLOAD), (MSG_STATUS, True, MAX_SMALL_PAYLOAD),
    (4, True, MAX_SMALL_PAYLOAD), (5, True, MAX_SMALL_PAYLOAD), (99, True, MAX_SMALL_PAYLOAD),
    (MSG_VIDEO, False, MAX_PREAUTH_PAYLOAD), (MSG_AUDIO, False, MAX_PREAUTH_PAYLOAD),
])
def test_limit_table(msg_type, authed, limit):
    assert payload_limit(msg_type, authed) == limit


@pytest.mark.parametrize(("msg_type", "authed"), [
    (MSG_VIDEO, True), (MSG_AUDIO, True), (MSG_STATUS, True), (MSG_STATUS, False), (MSG_VIDEO, False), (99, True)])
def test_huge_declared_length_is_cut_without_reading_body(msg_type, authed):
    sock = FakeSock([struct.pack(">BI", msg_type, 0xFFFFFFFF)])
    with pytest.raises(PayloadTooLarge) as e:
        read_msg(sock, None, authed)
    assert sock.script == [] and "上限" in e.value.log_line()   # 本体は読みに行かない (タイムアウトも待たない)


def test_just_over_the_limit_is_cut_and_exactly_at_limit_passes():
    over = FakeSock([struct.pack(">BI", MSG_VIDEO, MAX_VIDEO_PAYLOAD + 1)])
    with pytest.raises(PayloadTooLarge):
        read_msg(over)
    ok = FakeSock([struct.pack(">BI", MSG_VIDEO, 70 * 1024) + b"z" * (70 * 1024)])
    assert len(read_msg(ok, None, True)[1]) == 70 * 1024
    pre = FakeSock([struct.pack(">BI", MSG_VIDEO, 70 * 1024)])
    with pytest.raises(PayloadTooLarge):                       # 同じ 70 KiB でも認証前は切る
        read_msg(pre, None, False)


def test_cap_for_audio_channel():
    sock = FakeSock([struct.pack(">BI", MSG_STATUS, MAX_AUDIO_PAYLOAD + 1)])
    with pytest.raises(PayloadTooLarge):
        read_msg(sock, None, True, cap=MAX_AUDIO_PAYLOAD)
    assert payload_limit(MSG_VIDEO) > MAX_AUDIO_PAYLOAD


def test_normal_frame_sequence_passes():
    frames = [encode_msg(MSG_VIDEO, bytes([i]) * (30000 + i)) for i in range(20)]
    frames.insert(5, encode_msg(MSG_STATUS, b'{"fps":30}'))
    data = b"".join(frames)
    sock = FakeSock([data[i:i + 4096] for i in range(0, len(data), 4096)])
    got = [read_msg(sock, None, True, 5.0) for _ in frames]
    assert [t for t, _ in got].count(MSG_VIDEO) == 20 and got[5] == (MSG_STATUS, b'{"fps":30}')


def audio_packet(frames, rate=48000, channels=1):
    return struct.pack(">dIB", 1.0, rate, channels) + b"\x01\x00" * (frames * channels)


@pytest.mark.skipif(np is None, reason="numpy なし")
class TestAudio:
    def test_normal_packet(self):
        capture, rate, pcm = parse_audio(audio_packet(1024), 48000)
        assert (rate, len(pcm)) == (48000, 1024)

    def test_stereo_is_downmixed(self):
        assert len(parse_audio(audio_packet(480, channels=2), 48000)[2]) == 480

    def test_too_long_payload_rejected_before_conversion(self):
        with pytest.raises(PayloadTooLarge):
            parse_audio(audio_packet(MAX_AUDIO_PAYLOAD // 2 + 10), 48000)

    def test_too_many_samples_for_rate_rejected(self):
        with pytest.raises(PayloadTooLarge):          # 8 kHz で 2 秒分
            parse_audio(audio_packet(16000, rate=8000), 48000)

    def test_expected_output_size_checked(self, monkeypatch):
        monkeypatch.setattr(protocol, "MAX_AUDIO_OUT_BYTES", 40000)
        with pytest.raises(PayloadTooLarge):          # 8 kHz → 48 kHz で 6 倍に増える
            parse_audio(audio_packet(4000, rate=8000), 48000)
        assert parse_audio(audio_packet(4000, rate=8000)) is not None   # 変換先が分からなければ見込みは見ない

    def test_channel_count_limited(self):
        with pytest.raises(PayloadTooLarge):
            parse_audio(audio_packet(10, channels=200), 48000)

    def test_invalid_header_still_returns_none(self):
        assert parse_audio(b"short") is None
        assert parse_audio(struct.pack(">dIB", 1.0, 100, 1) + b"\0\0") is None

    def test_estimate_guards_numpy_allocation(self):
        frames, out = protocol.audio_estimate(13 + 2 * 8000, 8000, 1, 48000)
        assert frames == 8000 and out == 8000 * 6 * 4 + 4 and out < MAX_AUDIO_OUT_BYTES


# -- usbmux ----------------------------------------------------------------------------

def mux_msg(body=b"<plist/>", length=None):
    return struct.pack("<IIII", 16 + len(body) if length is None else length, 1, 8, 1) + body


def test_usbmux_huge_length_is_cut_and_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(usbmux, "log_error", lambda tag, text=None: logged.append((tag, text)))
    sock = FakeSock([mux_msg(b"", length=MAX_USBMUX_LENGTH + 1)])
    with pytest.raises(PayloadTooLarge):
        usbmux.usbmux_recv(sock, 5.0)
    assert len(logged) == 1 and "usbmux" in logged[0][1]


def test_usbmux_too_short_length_is_cut(monkeypatch):
    monkeypatch.setattr(usbmux, "log_error", lambda *a, **k: None)
    with pytest.raises(PayloadTooLarge):
        usbmux.usbmux_recv(FakeSock([mux_msg(b"", length=3)]), 5.0)


def test_usbmux_silent_peer_times_out(clock):
    sock = FakeSock([], clock)
    with pytest.raises(ReceiveTimeout):
        usbmux.usbmux_recv(sock, 5.0)
    assert sock.timeouts <= 6


def test_usbmux_uses_socket_timeout_by_default(clock):
    sock = FakeSock([], clock)
    sock.gettimeout = lambda: 2.0
    with pytest.raises(ReceiveTimeout):
        usbmux.usbmux_recv(sock)
    assert sock.timeouts <= 3


def test_usbmux_normal_message():
    import plistlib
    body = plistlib.dumps({"Number": 0}, fmt=plistlib.FMT_XML)
    assert usbmux.usbmux_recv(FakeSock([mux_msg(body)]), 5.0) == {"Number": 0}


def test_usbmux_connect_reports_limit_as_usb_error(monkeypatch):
    monkeypatch.setattr(usbmux, "log_error", lambda *a, **k: None)
    fake = FakeSock([mux_msg(b"", length=MAX_USBMUX_LENGTH + 1)])
    fake.settimeout = lambda t: None
    fake.gettimeout = lambda: 5.0
    fake.sendall = lambda b: None
    fake.close = lambda: None
    monkeypatch.setattr(usbmux.socket, "create_connection", lambda *a, **k: fake)
    with pytest.raises(usbmux.UsbError):        # 呼び出し側は今までどおり UsbError (USB の失敗) として扱う
        usbmux.usbmux_connect()
