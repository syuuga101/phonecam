import json
import socket
import struct
import threading
import time

import pytest

from phonecam.core import pairreq
from phonecam.core.protocol import MSG_CONTROL, MSG_STATUS, PhoneCamError, encode_msg, read_msg


def decode(packet):
    msg_type, length = struct.unpack(">BI", packet[:5])
    assert length == len(packet) - 5
    return msg_type, json.loads(packet[5:].decode("utf-8"))


def status(**values):
    return encode_msg(MSG_STATUS, json.dumps(values).encode("utf-8"))


# -- 要求メッセージ -------------------------------------------------------------------

def test_request_message_has_only_name_and_one_time_id():
    msg_type, body = decode(pairreq.build_request("abcd1234abcd1234", "My-PC"))
    assert msg_type == MSG_CONTROL
    assert body == {"pairRequest": {"v": 1, "id": "abcd1234abcd1234", "name": "My-PC"}}
    assert "code" not in json.dumps(body)   # コードはネットワークに送らない


def test_cancel_message():
    assert decode(pairreq.build_cancel("abcd1234"))[1] == {"pairCancel": {"id": "abcd1234"}}


def test_request_ids_are_unique_and_url_safe():
    ids = {pairreq.new_request_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) == 16 and all(c in "0123456789abcdef" for c in i) for i in ids)


@pytest.mark.parametrize("raw, expected", [
    ("Desk-PC", "Desk-PC"),
    ("  padded  ", "padded"),
    ("a\x00b\x1b[31mc\n", "ab[31mc"),                 # 制御文字を除く
    ("name‮gpj", "namegpj"),                      # 向きを変える文字 (書式文字) を除く
    ("x y z", "xyz"),
    ("あ" * 100, "あ" * pairreq.NAME_LIMIT),            # 長さを切り詰める
    ("", "PC"), ("\n\t", "PC"), (None, "PC"), (123, "PC"),
])
def test_sanitize_name(raw, expected):
    assert pairreq.sanitize_name(raw) == expected


def test_request_uses_sanitized_name():
    _, body = decode(pairreq.build_request("abcd1234", "evil\x00" + "x" * 500))
    assert len(body["pairRequest"]["name"]) <= pairreq.NAME_LIMIT
    assert "\x00" not in body["pairRequest"]["name"]


# -- iPhone からの返事の解析 ----------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (1, True), (2, True), (0, False), (True, False), ("1", False), (None, False),
])
def test_supports_popup(value, expected):
    assert pairreq.supports_popup({"pairPopup": value}) is expected
    assert not pairreq.supports_popup({"authRequired": True})   # 古い iPhone
    assert not pairreq.supports_popup(None)


def test_parse_reply_maps_states_and_ignores_other_requests():
    reply = lambda **r: {"pairReply": r}
    assert pairreq.parse_reply(reply(id="a", state="shown"), "a") == pairreq.SHOWN
    assert pairreq.parse_reply(reply(id="a", state="declined"), "a") == pairreq.DECLINED
    assert pairreq.parse_reply(reply(id="a", state="background"), "a") == pairreq.BACKGROUND
    for other in ("busy", "limited", "unavailable", "something-new", 5, None):
        assert pairreq.parse_reply(reply(id="a", state=other), "a") == pairreq.FALLBACK
    assert pairreq.parse_reply(reply(id="b", state="shown"), "a") is None   # 別の要求への返事
    assert pairreq.parse_reply({"authRequired": True}, "a") is None
    assert pairreq.parse_reply({"pairReply": "x"}, "a") is None
    assert pairreq.parse_reply([], "a") is None


# -- PC 側の状態の移り変わり (本物のソケット対 + 偽の iPhone) ----------------------------

class FakePhone:
    """socketpair の片方で iPhone のふりをする。受け取った制御メッセージを records に貯める。"""

    def __init__(self, script):
        self.pc, self.phone = socket.socketpair()
        self.records = []
        self.script = script
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _read_control(self, timeout=2.0):
        self.phone.settimeout(0.2)
        deadline = time.monotonic() + timeout

        class Until:
            def is_set(self_inner):
                return time.monotonic() >= deadline
        try:
            msg_type, payload = read_msg(self.phone, Until())
        except (ConnectionError, OSError):
            return None
        assert msg_type == MSG_CONTROL
        body = json.loads(payload.decode("utf-8"))
        self.records.append(body)
        return body

    def _run(self):
        try:
            self.script(self)
        except OSError:
            pass
        finally:
            time.sleep(0.05)

    def send(self, **values):
        self.phone.sendall(status(**values))


def run_requester(script, **kwargs):
    fake = FakePhone(script)
    states = []
    event = threading.Event()

    def on_state(state):
        states.append(state)
        event.set()

    requester = pairreq.PairRequester("192.168.1.7", "Desk-PC", on_state, connect=lambda ip: fake.pc,
                                      request_id="req0123456789abc", **kwargs).start()
    return fake, requester, states, event


def finish(fake, requester):
    requester.close()
    for sock in (fake.phone, fake.pc):
        try:
            sock.close()
        except OSError:
            pass


def test_shown_then_cancel_on_close():
    def script(phone):
        phone.send(authRequired=True, authOk=False, pairPopup=1)
        request = phone._read_control()
        phone.records.append(("seen", request["pairRequest"]["id"]))
        phone.send(pairReply={"id": request["pairRequest"]["id"], "state": "shown"})
        phone._read_control()   # PC の取り消し
    fake, requester, states, event = run_requester(script)
    assert event.wait(3)
    assert states == [pairreq.SHOWN]
    requester.close()
    fake.thread.join(3)
    assert fake.records[0] == {"pairRequest": {"v": 1, "id": "req0123456789abc", "name": "Desk-PC"}}
    assert fake.records[-1] == {"pairCancel": {"id": "req0123456789abc"}}
    finish(fake, requester)


def test_declined_after_shown_is_reported_and_stops():
    def script(phone):
        phone.send(authRequired=True, authOk=False, pairPopup=1)
        phone._read_control()
        phone.send(pairReply={"id": "req0123456789abc", "state": "shown"})
        time.sleep(0.2)
        phone.send(pairReply={"id": "req0123456789abc", "state": "declined"})
    fake, requester, states, event = run_requester(script)
    deadline = time.monotonic() + 3
    while pairreq.DECLINED not in states and time.monotonic() < deadline:
        time.sleep(0.02)
    assert states == [pairreq.SHOWN, pairreq.DECLINED]
    requester._thread.join(3)
    assert not requester._thread.is_alive()
    finish(fake, requester)


def test_background_reply():
    def script(phone):
        phone.send(authRequired=True, authOk=False, pairPopup=1)
        phone._read_control()
        phone.send(pairReply={"id": "req0123456789abc", "state": "background"})
    fake, requester, states, event = run_requester(script)
    assert event.wait(3)
    assert states == [pairreq.BACKGROUND]
    finish(fake, requester)


def test_reply_for_another_request_is_ignored_and_then_times_out(monkeypatch):
    monkeypatch.setattr(pairreq, "REPLY_WAIT_SEC", 0.5)

    def script(phone):
        phone.send(authRequired=True, authOk=False, pairPopup=1)
        phone._read_control()
        phone.send(pairReply={"id": "someone-else", "state": "shown"})
        time.sleep(1.5)
    fake, requester, states, event = run_requester(script)
    assert event.wait(3)
    assert states == [pairreq.FALLBACK]
    finish(fake, requester)


def test_old_iphone_gets_nothing_and_falls_back(monkeypatch):
    monkeypatch.setattr(pairreq, "CAPABILITY_WAIT_SEC", 0.5)

    def script(phone):
        phone.send(authRequired=True, authOk=False)   # pairPopup なし
        time.sleep(0.8)
        phone.records.append(phone._read_control(0.3))
    fake, requester, states, event = run_requester(script)
    assert event.wait(3)
    assert states == [pairreq.FALLBACK]
    fake.thread.join(3)
    assert fake.records == [None]   # 何も送っていない (古い iPhone に認証失敗を数えさせない)
    finish(fake, requester)


def test_silent_iphone_falls_back(monkeypatch):
    monkeypatch.setattr(pairreq, "CAPABILITY_WAIT_SEC", 0.3)
    fake, requester, states, event = run_requester(lambda phone: time.sleep(1.0))
    assert event.wait(3)
    assert states == [pairreq.FALLBACK]
    finish(fake, requester)


def test_connection_closed_by_phone_falls_back():
    def script(phone):
        phone.phone.close()   # Wi-Fi を許可していない iPhone はすぐ切る
    fake, requester, states, event = run_requester(script)
    assert event.wait(3)
    assert states == [pairreq.FALLBACK]
    finish(fake, requester)


def test_connect_failure_falls_back():
    def connect(ip):
        raise PhoneCamError("no route")
    states = []
    requester = pairreq.PairRequester("192.168.1.7", "PC", states.append, connect=connect).start()
    requester._thread.join(3)
    assert states == [pairreq.FALLBACK]


def test_oversized_phone_message_does_not_crash_or_hang():
    def script(phone):
        phone.phone.sendall(struct.pack(">BI", MSG_STATUS, 10 * 1024 * 1024))   # 巨大な長さだけ送り、本体は送らない
        time.sleep(5)
    fake, requester, states, event = run_requester(script)
    assert event.wait(1.5)   # 本体を待たずにすぐ切り上げる
    assert states == [pairreq.FALLBACK]
    finish(fake, requester)


def test_close_before_connection_is_silent():
    started = threading.Event()
    release = threading.Event()
    fake_pc, fake_phone = socket.socketpair()

    def connect(ip):
        started.set()
        release.wait(2)
        return fake_pc
    states = []
    requester = pairreq.PairRequester("192.168.1.7", "PC", states.append, connect=connect).start()
    assert started.wait(2)
    requester.close()
    release.set()
    requester._thread.join(3)
    assert states == []
    fake_phone.close()
