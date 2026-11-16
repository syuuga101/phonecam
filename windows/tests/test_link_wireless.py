import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import MagicMock, Mock

import pytest

from phonecam.core import link as transport, wireless
from phonecam.core.settings import DEFAULT_SETTINGS, sanitize_settings


def make_link(mode, ip="", **settings):
    return transport.Link({**DEFAULT_SETTINGS, **settings}, Mock(), lambda: (mode, ip))


def test_auto_prefers_usb_without_probing_wifi(monkeypatch, known_iphone):
    monkeypatch.setattr(transport, "usbmux_available", lambda: "usb-serial")
    discover = Mock()
    monkeypatch.setattr(transport, "discover", discover)
    link = make_link("auto", known_devices=[known_iphone])
    assert link._resolve() == ("usb", "", None)
    discover.assert_not_called()


def test_auto_finds_registered_device_at_new_address(monkeypatch, known_iphone):
    monkeypatch.setattr(transport, "usbmux_available", lambda: None)
    monkeypatch.setattr(transport, "local_ipv4", lambda: ("192.168.1.100",))
    found = wireless.read_device({**known_iphone, "addresses": ["192.168.1.8"]})
    discover = Mock(return_value=[found])
    monkeypatch.setattr(transport, "discover", discover)
    link = make_link("auto", known_devices=[known_iphone])
    mode, ip, device = link._resolve()
    assert (mode, ip) == ("wifi", "192.168.1.8") and device.key == known_iphone["key"]
    assert link._resolve()[:2] == ("wifi", "192.168.1.8")
    discover.assert_called_once_with(("192.168.1.100",))


def test_v1_registration_needs_repairing_and_never_connects(monkeypatch, legacy_iphone):
    """v1 で登録した端末 (鍵なし) は、PIN があっても Wi-Fi につながない。再ペアリングを促す。"""
    monkeypatch.setattr(transport, "usbmux_available", lambda: None)
    discover = Mock()
    monkeypatch.setattr(transport, "discover", discover)
    link = make_link("auto", known_devices=[legacy_iphone])
    with pytest.raises(transport.PhoneCamError) as e:
        link._resolve()
    assert "再ペアリング" in str(e.value)
    discover.assert_not_called()


def test_wifi_only_does_not_check_usb(monkeypatch):
    usb = Mock()
    monkeypatch.setattr(transport, "usbmux_available", usb)
    assert make_link("wifi", "192.168.1.4")._resolve() == ("wifi", "192.168.1.4", None)
    usb.assert_not_called()


def test_control_sender_coalesces_newest_values(monkeypatch):
    link = make_link("usb")
    link._sock = Mock()
    link._tx_thread = None
    sent = []
    first_started = threading.Event()
    release_first = threading.Event()
    sent_second = threading.Event()
    condition = link._tx_cond
    monkeypatch.setattr(condition, "wait", lambda timeout: False)

    def send(control):
        sent.append(control)
        if len(sent) == 1:
            first_started.set()
            release_first.wait(5)
        else:
            sent_second.set()

    monkeypatch.setattr(link, "_send_control_now", send)
    assert link.send_control({"bitrate": 10})
    try:
        assert first_started.wait(5)
        assert link.send_control({"bitrate": 15, "torch": True})
        assert link.send_control({"bitrate": 20})
    finally:
        release_first.set()
    assert sent_second.wait(5)
    assert sent == [{"bitrate": 10}, {"bitrate": 20, "torch": True}]


def test_send_failure_shuts_down_socket():
    link = make_link("usb")
    link._sock = Mock()
    link._wire = transport.Wire(None)
    link._sock.sendall.side_effect = OSError("disconnected")
    assert not link._send_control_now({"bitrate": 20})
    link._sock.shutdown.assert_called_once_with(transport.socket.SHUT_RDWR)


def test_link_reconnects_after_socket_drop_and_releases_route(monkeypatch):
    link = make_link("usb")
    link.routes = Mock()
    monkeypatch.setattr(link, "_resolve", lambda: ("usb", "", None))
    monkeypatch.setattr(transport, "RECONNECT_SEC", 0)
    link._usb_serial = "serial"
    sockets = [Mock(), Mock()]
    monkeypatch.setattr(link, "_connect", Mock(side_effect=sockets))
    stopped = threading.Event()

    def session(sock, stop):
        if sock is sockets[0]:
            raise OSError("dropped")
        stop.set()
        stopped.set()

    monkeypatch.setattr(link, "_session", session)
    try:
        assert link.start()
        assert stopped.wait(5)
    finally:
        link.stop()
    for sock in sockets:
        assert sock.close.called
    assert link.routes.claim.call_count == link.routes.release.call_count == 2
    link.routes.release.assert_called_with(link, ("usb", "serial"))
    assert link._sock is None and link._thread is None


def test_lan_ipv4_validation():
    assert wireless.ipv4([
        "127.0.0.1", "8.8.8.8", "224.0.0.1", "0.0.0.0", "fd00::1",
        "192.168.1.8", "192.168.1.8", "10.0.0.2", 12,
    ]) == ("192.168.1.8", "10.0.0.2")
    assert wireless.route_of("172.20.10.1") == "tether"
    assert wireless.route_of("192.168.137.22") == "hotspot"
    assert wireless.route_of("192.168.1.8") == "lan"
    assert wireless.on_tether(["172.20.10.14"])
    assert not wireless.on_tether(["172.20.10.16"])


def test_known_devices_drop_invalid_records(known_iphone):
    data = {**known_iphone, "name": " iPhone ", "alias": " Desk ",
            "addresses": ["1.1.1.1", *known_iphone["addresses"]]}
    config = sanitize_settings({**DEFAULT_SETTINGS, "known_devices": [
        None, {**data, "key": "zz"}, {**data, "id": "not-a-uuid"}, {**data, "key": 123},
        {**data, "key": "", "addresses": ["127.0.0.1"]}, {k: v for k, v in data.items() if k != "key"}, data,
    ]})
    expected = {**known_iphone, "alias": "Desk", "fp": wireless.read_device(known_iphone).fp}
    assert config["known_devices"] == [expected]


def test_v1_registration_is_kept_as_repair_without_sending_the_pin(legacy_iphone):
    config = sanitize_settings({**DEFAULT_SETTINGS, "known_devices": [legacy_iphone]})
    device = wireless.read_device(config["known_devices"][0])
    assert device.needs_repair and device.registered and not device.paired
    assert config["known_devices"][0]["repair"] is True
