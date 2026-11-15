"""Wi-Fi でつないでいる間の USB への乗り換え: つないでから切る / 失敗したら待つ / 抜き差しでリセット。"""
import threading
from unittest.mock import Mock

import pytest

from phonecam.core import link as transport
from phonecam.core.protocol import PhoneCamError
from phonecam.core.settings import DEFAULT_SETTINGS
from phonecam.core.state import SharedState
from phonecam.core.usbmux import UsbError

PHONE = "0F6C6A4E-7B7A-4C58-9F55-1B6D1F4E5A11"
OTHER = "1A2B3C4D-0000-4000-8000-123456789ABC"
SERIAL = "00008110-000A1B2C3D4E5F"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    calls = []
    devices = {"serials": [SERIAL]}
    probe = Mock(return_value=PHONE)
    usb_sock = Mock(name="usb_sock")

    def connect(**kw):
        calls.append("usb_connect")
        kw["info"].update(device_id=7, usb_serial=SERIAL)
        return usb_sock

    monkeypatch.setattr(transport, "usbmux_devices", lambda: list(devices["serials"]))
    monkeypatch.setattr(transport, "usbmux_probe", probe)
    connect_mock = Mock(side_effect=connect)
    monkeypatch.setattr(transport, "usbmux_connect", connect_mock)
    link = transport.Link({**DEFAULT_SETTINGS}, SharedState(), lambda: ("auto", ""))
    link._clock = clock
    link.state.update(phone_status={"deviceId": PHONE}, connected=True, transport="wifi")
    wifi = Mock(name="wifi_sock")
    wifi.shutdown.side_effect = lambda *_: calls.append("wifi_shutdown")
    return type("Env", (), dict(link=link, clock=clock, calls=calls, devices=devices, probe=probe,
                                connect=connect_mock, wifi=wifi, usb_sock=usb_sock))


def attempt(env):
    return env.link._try_usb_switch(env.wifi, threading.Event())


def test_failed_probe_keeps_wifi_and_backoff_grows(env):
    env.probe.side_effect = UsbError("closed", "app")
    waits = []
    for expected in (30, 60, 300, 300):
        assert attempt(env) is False
        count, until = env.link._usb_fail[SERIAL]
        waits.append(until - env.clock.now)
        assert waits[-1] == expected
        before = env.probe.call_count
        env.clock.now += expected - 1          # 待ち時間のあいだは試さない
        assert attempt(env) is False
        assert env.probe.call_count == before
        env.clock.now += 1
    env.wifi.shutdown.assert_not_called()
    env.connect.assert_not_called()
    assert env.link._handover is None
    assert env.link.state.usb_note == transport.tr("core.link.usb_app")


def test_switch_connects_usb_before_closing_wifi(env):
    assert attempt(env) is True
    assert env.calls == ["usb_connect", "wifi_shutdown"]
    serial, sock, info = env.link._handover
    assert (serial, sock, info["device_id"]) == (SERIAL, env.usb_sock, 7)
    # 次の接続先は、先につないでおいた USB
    assert env.link._resolve() == ("usb", "", None)
    assert env.link._usb_serial == SERIAL


def test_connect_failure_after_probe_keeps_wifi(env):
    env.connect.side_effect = PhoneCamError("failed")
    assert attempt(env) is False
    env.wifi.shutdown.assert_not_called()
    assert env.link._usb_fail[SERIAL][0] == 1


def test_replug_resets_backoff(env):
    env.probe.side_effect = UsbError("failed", "trust")
    assert attempt(env) is False
    assert attempt(env) is False and env.probe.call_count == 1
    env.devices["serials"] = []
    assert attempt(env) is False
    assert env.link._usb_fail == {}
    env.devices["serials"] = [SERIAL]
    env.probe.side_effect = None
    assert attempt(env) is True          # 待ち時間はリセットされ、すぐ試す
    env.probe.assert_called()


@pytest.mark.parametrize("usb_id, wifi_id", [("", PHONE), (OTHER, PHONE)])
def test_does_not_switch_unless_same_device(env, usb_id, wifi_id):
    env.probe.return_value = usb_id
    env.link.state.update(phone_status={"deviceId": wifi_id})
    assert attempt(env) is False
    env.connect.assert_not_called()
    env.wifi.shutdown.assert_not_called()


def test_waits_for_wifi_identity_without_backoff(env):
    env.link.state.update(phone_status={})
    assert attempt(env) is False
    env.connect.assert_not_called()
    assert env.link._usb_fail == {}
    assert env.link.state.usb_note == transport.tr("core.link.usb_preparing")


def test_usb_session_failure_after_switch_counts_as_failure(env):
    env.link._mode, env.link._usb_serial = "usb", SERIAL
    env.link._from_wifi, env.link._usb_confirmed = True, False
    env.link._note_usb_failure(SERIAL, "trust")
    assert env.link._usb_fail[SERIAL][0] == 1
