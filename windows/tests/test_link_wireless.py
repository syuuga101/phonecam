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
    found = wireless.read_device({**known_iphone, "addresses": ["192.168.1.8"], "code": "999999"})
    discover = Mock(return_value=[found])
    monkeypatch.setattr(transport, "discover", discover)
    link = make_link("auto", known_devices=[known_iphone])
    assert link._resolve() == ("wifi", "192.168.1.8", "012345")
    assert link._resolve() == ("wifi", "192.168.1.8", "012345")
    discover.assert_called_once_with(("192.168.1.100",))


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
        None, {**data, "code": 123456}, {**data, "id": "not-a-uuid"},
        {**data, "addresses": ["127.0.0.1"]}, data,
    ]})
    assert config["known_devices"] == [{**known_iphone, "alias": "Desk"}]


@pytest.fixture
def pairing(monkeypatch, known_iphone):
    # Drive each connection with a fake socket so no network service or phone is needed.
    monkeypatch.setattr(wireless.socket, "socket", MagicMock())
    monkeypatch.setattr(wireless, "Thread", Mock())
    clock = Mock(return_value=100)
    monkeypatch.setattr(wireless.time, "monotonic", clock)
    accepted = Mock()
    with closing(wireless.Pairing(["192.168.1.3"], accepted, seconds=10)) as server:
        payload = {**known_iphone, "alias": "untrusted", "token": server._token, "v": wireless.VERSION}
        yield server, payload, accepted, clock


def pairing_socket(payload):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.recv.side_effect = [json.dumps(payload).encode() + b"\n", b""]
    return conn


def handle(server, conn, accepted):
    with server._lock:
        server._active += 1
    server._handle(conn, "192.168.1.8", accepted)


def test_pairing_rejects_invalid_input_then_accepts_once(pairing):
    server, payload, accepted, _ = pairing
    for invalid in ([1], {**payload, "token": "incorrect"}, {**payload, "v": -1},
                    {**payload, "code": "invalid"}):
        conn = pairing_socket(invalid)
        handle(server, conn, accepted)
        conn.sendall.assert_not_called()
    accepted.assert_not_called()
    valid = pairing_socket(payload)
    handle(server, valid, accepted)
    valid.sendall.assert_called_once_with(b'{"ok":true}\n')
    device = accepted.call_args.args[0]
    assert device.addresses == ("192.168.1.8", "192.168.1.7") and not device.alias
    replay = pairing_socket(payload)
    handle(server, replay, accepted)
    replay.sendall.assert_not_called()
    assert accepted.call_count == 1


def test_pairing_rejects_expired_token(pairing):
    server, payload, accepted, clock = pairing
    clock.return_value = 110
    conn = pairing_socket(payload)
    handle(server, conn, accepted)
    accepted.assert_not_called()
    conn.sendall.assert_not_called()


def test_pairing_only_one_simultaneous_request_wins(pairing, monkeypatch):
    server, payload, accepted, _ = pairing
    read = server._read
    ready = threading.Barrier(2)

    def read_at_same_time(conn, peer):
        device = read(conn, peer)
        ready.wait(timeout=5)
        return device

    monkeypatch.setattr(server, "_read", read_at_same_time)
    sockets = [pairing_socket(payload), pairing_socket(payload)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for job in [pool.submit(handle, server, conn, accepted) for conn in sockets]:
            job.result(timeout=6)
    assert accepted.call_count == 1
    assert sum(conn.sendall.call_count for conn in sockets) == 1
    assert server._active == 0
