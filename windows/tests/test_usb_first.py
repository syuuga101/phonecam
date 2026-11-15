"""USB 優先の回帰テスト: usbmux をモックして、auto でも USB が先に試され、5557 (識別ポート) が無くても 1 台なら USB でつなぐ。"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phonecam.core import cameras as cameras_mod
from phonecam.core import link as transport
from phonecam.core.cameras import DeviceRoutes
from phonecam.core.protocol import PhoneCamError
from phonecam.core.settings import DEFAULT_SETTINGS
from phonecam.core.state import SharedState
from phonecam.core.usbmux import UsbError

SERIAL = "00008110-000A1B2C3D4E5F"
SERIAL2 = "00008120-00FFEEDDCCBBAA"
WIFI_IP = "192.168.1.7"


class FakeCamera:
    def __init__(self, link, settings):
        self.link = link
        self._settings = settings

    def get(self, key, default=None):
        return self._settings.get(key, default)


def make_link(monkeypatch, serials, mode="auto", cameras=1, **settings):
    """usbmux をモックした Link を DeviceRoutes つきで作る (本物のアプリと同じ配線)。"""
    state = {"serials": list(serials)}
    monkeypatch.setattr(transport, "usbmux_devices", lambda: list(state["serials"]))
    monkeypatch.setattr(cameras_mod, "usbmux_devices", lambda: list(state["serials"]))
    values = {**DEFAULT_SETTINGS, **settings}
    link = transport.Link(values, SharedState(), lambda: (mode, values.get("ip", "")))
    group = [FakeCamera(link, values)] + [FakeCamera(object(), {}) for _ in range(cameras - 1)]
    link.routes = DeviceRoutes(group)
    return link, state


def wifi_settings():
    return dict(ip=WIFI_IP, pair_code="012345",
                known_devices=[{"id": "9bd344c9-765e-4cb6-ae81-4c77f064131a", "name": "iPhone",
                                "addresses": [WIFI_IP], "code": "012345"}])


def test_auto_prefers_usb_over_known_wifi(monkeypatch):
    link, _ = make_link(monkeypatch, [SERIAL], **wifi_settings())
    link._last_wifi = (WIFI_IP, "012345")          # 前回は Wi-Fi でつないでいた
    assert link._resolve() == ("usb", "", None)
    assert link._usb_serial == SERIAL


def test_not_connected_start_tries_usb_without_identity_port(monkeypatch):
    """未接続からの最初の接続: 5557 (識別) が読めなくても、1 台なら USB の本接続 (5555) を最初に試す。"""
    link, _ = make_link(monkeypatch, [SERIAL], **wifi_settings())
    monkeypatch.setattr(cameras_mod, "usbmux_connect", Mock(side_effect=PhoneCamError("5557 なし")))   # usb_identity 用
    probe = Mock(side_effect=UsbError("5557 なし", "app"))
    monkeypatch.setattr(transport, "usbmux_probe", probe)
    attempts = []

    def connect(**kw):
        attempts.append(kw)
        raise UsbError("止める", "app")           # 本接続はここで止めて _run の 1 周目だけ見る

    monkeypatch.setattr(transport, "usbmux_connect", connect)
    link._stop = threading.Event()
    stop = link._stop
    monkeypatch.setattr(transport, "RECONNECT_SEC", 0.01)
    t = threading.Thread(target=link._run, daemon=True)
    t.start()
    deadline = time.time() + 3
    while not attempts and time.time() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(2)
    assert attempts and attempts[0]["serial"] == SERIAL     # 最初の接続試行が USB
    probe.assert_not_called()                               # 識別ポートの probe を要求していない


def test_single_camera_switches_to_usb_when_identity_port_missing(monkeypatch):
    """Wi-Fi 中に USB が挿されたとき、5557 が無い (古い/開いていない iOS アプリ) 1 台構成でも USB へ乗り換える。"""
    link, _ = make_link(monkeypatch, [SERIAL])
    monkeypatch.setattr(cameras_mod, "usbmux_connect", Mock(side_effect=PhoneCamError("5557 なし")))
    monkeypatch.setattr(transport, "usbmux_probe", Mock(side_effect=UsbError("5557 なし", "app")))
    usb_sock = Mock()

    def connect(**kw):
        kw["info"].update(device_id=7, usb_serial=SERIAL)
        return usb_sock

    monkeypatch.setattr(transport, "usbmux_connect", connect)
    wifi = Mock()
    assert link._try_usb_switch(wifi, threading.Event()) is True
    assert link._handover[1] is usb_sock
    wifi.shutdown.assert_called()


def test_single_camera_keeps_wifi_when_usb_really_fails(monkeypatch):
    link, _ = make_link(monkeypatch, [SERIAL])
    monkeypatch.setattr(cameras_mod, "usbmux_connect", Mock(side_effect=PhoneCamError("x")))
    monkeypatch.setattr(transport, "usbmux_probe", Mock(side_effect=UsbError("x", "app")))
    monkeypatch.setattr(transport, "usbmux_connect", Mock(side_effect=UsbError("拒否", "app")))
    wifi = Mock()
    assert link._try_usb_switch(wifi, threading.Event()) is False
    wifi.shutdown.assert_not_called()
    assert link._usb_fail[SERIAL][0] == 1


def test_multi_camera_still_requires_identity_port(monkeypatch):
    link, _ = make_link(monkeypatch, [SERIAL], cameras=2)
    monkeypatch.setattr(cameras_mod, "usbmux_connect", Mock(side_effect=PhoneCamError("x")))
    monkeypatch.setattr(transport, "usbmux_probe", Mock(side_effect=UsbError("5557 なし", "app")))
    connect = Mock()
    monkeypatch.setattr(transport, "usbmux_connect", connect)
    wifi = Mock()
    assert link._try_usb_switch(wifi, threading.Event()) is False
    connect.assert_not_called()
    wifi.shutdown.assert_not_called()


def test_wifi_mode_and_no_usb_fall_back_to_wifi(monkeypatch):
    link, _ = make_link(monkeypatch, [], **wifi_settings())
    monkeypatch.setattr(transport.Link, "_probe", staticmethod(lambda ip: True))
    mode, ip, _code = link._resolve()
    assert (mode, ip) == ("wifi", WIFI_IP)
