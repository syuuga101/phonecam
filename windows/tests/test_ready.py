"""「今すぐつなげる相手がいるか」: USB の見張り (usbmux をモック) と、状態 → 表示・ボタンの種類の決め方 (純粋な関数)。"""
import io
from unittest.mock import Mock

import pytest

from phonecam.core import ready as R
from phonecam.core import usbmux
from phonecam.core.protocol import PhoneCamError
from phonecam.core.ready import (
    CABLE, READY, Ready, TRY_SEC, UsbSeen, UsbWatcher, connect_ready, connect_view, is_trying, usb_pick, usb_state_of)
from phonecam.core.usbmux import UsbError

A, B = "00008110-000A1B2C3D4E5F", "00008120-00FFEEDDCCBBAA"
ID_A = "9bd344c9-765e-4cb6-ae81-4c77f064131a"
ID_B = "0d2e6a52-3c1b-4f0a-9d4e-7a1b2c3d4e5f"


# -- usbmux_app_state: アプリが応答しているか ---------------------------------------

class FakeSock:
    def __init__(self, line):
        self.line = line
        self.closed = False

    def settimeout(self, _t):
        pass

    def makefile(self, _mode):
        return io.BytesIO(self.line)

    def close(self):
        self.closed = True


def test_app_state_ready_reads_identity_port(monkeypatch):
    sock = FakeSock((ID_A + "\n").encode())
    connect = Mock(return_value=sock)
    monkeypatch.setattr(usbmux, "usbmux_connect", connect)
    assert usbmux.usbmux_app_state(A) == ("ready", ID_A)
    assert connect.call_args.kwargs["port"] == usbmux.IDENTITY_PORT   # 映像のポート (5555) には触れない
    assert sock.closed


def test_app_state_ready_even_when_identity_unreadable(monkeypatch):
    monkeypatch.setattr(usbmux, "usbmux_connect", Mock(return_value=FakeSock(b"")))
    assert usbmux.usbmux_app_state(A) == ("ready", "")   # 古いアプリ: つながるが ID は読めない


@pytest.mark.parametrize("error", [UsbError("閉じている", "app"), UsbError("信頼", "trust"), PhoneCamError("なし")])
def test_app_state_cable_when_app_does_not_answer(monkeypatch, error):
    monkeypatch.setattr(usbmux, "usbmux_connect", Mock(side_effect=error))
    assert usbmux.usbmux_app_state(A) == ("cable", "")


# -- UsbWatcher ------------------------------------------------------------------

class Clock:
    now = 100.0


def watcher(devices, probes):
    """devices: 一覧を返す関数用の list、probes: {シリアル: (状態, 生の ID)}"""
    probe = Mock(side_effect=lambda serial: probes[serial])
    return UsbWatcher(list_fn=lambda: list(devices), probe_fn=probe, clock=lambda: Clock.now), probe


def test_watcher_reports_ready_and_cable_per_serial():
    w, _ = watcher([A, B], {A: ("ready", ID_A), B: ("cable", "")})
    assert w.step(100.0) is True
    assert w.snapshot() == {A: UsbSeen(READY, ID_A), B: UsbSeen(CABLE, "")}


def test_watcher_probes_only_when_due():
    w, probe = watcher([A, B], {A: ("ready", ID_A), B: ("cable", "")})
    w.step(100.0)
    assert probe.call_count == 2
    w.step(101.0)   # 2 秒たっていない
    assert probe.call_count == 2
    w.step(102.5)   # アプリが応答していない B だけ、2 秒後に確かめ直す
    assert [c.args[0] for c in probe.call_args_list] == [A, B, B]
    w.step(106.5)   # 応答している A は 6 秒後
    assert [c.args[0] for c in probe.call_args_list][3:] == [A, B]


def test_watcher_notices_app_opening_and_unplug():
    devices, probes = [A], {A: ("cable", "")}
    w, _ = watcher(devices, probes)
    w.step(100.0)
    assert w.snapshot()[A].state == CABLE
    probes[A] = ("ready", ID_A)
    assert w.step(102.1) is True               # PhoneCam を開いた
    assert w.snapshot()[A] == UsbSeen(READY, ID_A)
    devices.clear()
    assert w.step(102.2) is True               # 抜いた
    assert w.snapshot() == {}


def test_watcher_is_silent_while_connecting_and_rechecks_right_after():
    w, probe = watcher([A], {A: ("ready", ID_A)})
    w.step(100.0)
    w.set_busy("camera 1", True)
    assert w.step(200.0) is False
    assert probe.call_count == 1               # つないでいる間は何も触れない
    w.set_busy("camera 1", False)
    w.step(200.5)
    assert probe.call_count == 2               # 切断した直後に見直す (6 秒待たない)


def test_watcher_survives_errors():
    def boom():
        raise OSError("usbmux なし")
    w = UsbWatcher(list_fn=boom, probe_fn=Mock(), clock=lambda: 1.0)
    assert w.step(1.0) is False and w.snapshot() == {}
    w = UsbWatcher(list_fn=lambda: [A], probe_fn=Mock(side_effect=RuntimeError("x")), clock=lambda: 1.0)
    w.step(1.0)
    assert w.snapshot() == {A: UsbSeen(CABLE, "")}


def test_watcher_thread_start_is_idempotent():
    w = UsbWatcher(list_fn=lambda: [], probe_fn=Mock())
    w.ensure_started()
    thread = w._thread
    w.ensure_started()
    assert w._thread is thread
    w.stop()


# -- usb_state_of: 登録済み端末 1 台の USB の状態 ----------------------------------------

def test_usb_state_matches_device_id_first():
    seen = {A: UsbSeen(READY, ID_A), B: UsbSeen(CABLE, "")}
    assert usb_state_of(seen, ID_A) == READY
    assert usb_state_of(seen, ID_B) == ""          # 別の端末はケーブルが見えていても自分とは限らない


def test_usb_state_uses_saved_serial_for_cable_only_device():
    seen = {A: UsbSeen(CABLE, "")}
    assert usb_state_of(seen, ID_A, serial=A) == CABLE
    assert usb_state_of(seen, ID_A, serial=B) == ""
    assert usb_state_of({A: UsbSeen(READY, ID_B)}, ID_A, serial=A) == ""   # そのシリアルは今は別の端末


def test_usb_state_sole_device_with_unreadable_identity():
    assert usb_state_of({A: UsbSeen(READY, "")}, ID_A, sole=True) == READY     # 古いアプリ
    assert usb_state_of({A: UsbSeen(CABLE, "")}, ID_A, sole=True) == CABLE
    assert usb_state_of({A: UsbSeen(CABLE, "")}, ID_A, sole=False) == ""
    assert usb_state_of({A: UsbSeen(READY, ID_B)}, ID_A, sole=True) == ""      # ID が違うと分かっているなら自分ではない
    assert usb_state_of({A: UsbSeen(CABLE, ""), B: UsbSeen(CABLE, "")}, ID_A, sole=True) == ""
    assert usb_state_of({}, ID_A, A, True) == ""


# -- usb_pick: カメラが USB で使う端末 (Link._eligible_usb と同じ決め方) --------------------------

def test_pick_single_camera_prefers_ready_phone():
    seen = {A: UsbSeen(CABLE, ""), B: UsbSeen(READY, ID_B)}
    assert usb_pick(seen) == (READY, B, ID_B)
    assert usb_pick(seen, serial=A) == (CABLE, A, "")   # 保存した端末があればそれ
    assert usb_pick({}) is None


def test_pick_multi_camera_follows_target_and_serial():
    seen = {A: UsbSeen(READY, ID_A), B: UsbSeen(READY, ID_B)}
    assert usb_pick(seen, target=ID_B, multi=True) == (READY, B, ID_B)
    assert usb_pick(seen, target="9999", multi=True) is None
    assert usb_pick(seen, serial=A, multi=True) == (READY, A, ID_A)
    assert usb_pick(seen, serial="gone", multi=True) is None   # 保存した USB の端末が見えない: 別の端末には振らない
    assert usb_pick(seen, multi=True, taken={A}) == (READY, B, ID_B)   # 別のカメラが使っている端末は除く
    assert usb_pick(seen, multi=True, taken={A, B}) is None


# -- connect_ready: USB ready / cable / Wi-Fi / なし ---------------------------------------

@pytest.mark.parametrize("usb, wifi, mode, kind", [
    (READY, "", "auto", "usb"),
    (READY, "iPhone 13", "auto", "usb"),          # 両方あれば USB が先
    ("", "iPhone 13", "auto", "wifi"),
    (CABLE, "iPhone 13", "auto", "wifi"),         # アプリが応答していなくても Wi-Fi でつなげるなら Wi-Fi
    (CABLE, "", "auto", "cable"),
    ("", "", "auto", ""),
    (READY, "iPhone 13", "wifi", "wifi"),         # 方式を決めているときはその方式だけ
    (READY, "", "wifi", ""),
    ("", "iPhone 13", "usb", ""),
    (CABLE, "iPhone 13", "usb", "cable"),
])
def test_connect_ready_kind(usb, wifi, mode, kind):
    assert connect_ready(usb, "iPhone 13", wifi, mode).kind == kind


def test_connect_ready_names_the_phone():
    assert connect_ready(READY, "iPhone 13", "リビング", "auto") == Ready("usb", "iPhone 13")
    assert connect_ready("", "", "リビング", "auto") == Ready("wifi", "リビング")
    assert connect_ready("", "", "", "auto") == Ready()


# -- 表示とボタンの種類 ---------------------------------------------------------------------

def test_view_usb_ready_is_green_connect_usb():
    view = connect_view(Ready("usb", "iPhone 13"), wanted=False, connected=False, trying=False)
    assert (view.look, view.text, view.enabled, view.card) == ("go", "connection.button.connect_usb", True, "ready")


def test_view_wifi_ready_is_green_connect_wifi():
    view = connect_view(Ready("wifi", "iPhone 13"), False, False, False)
    assert (view.look, view.text, view.card) == ("go", "connection.button.connect_wifi", "ready")


@pytest.mark.parametrize("ready", [Ready(), Ready("cable", "iPhone 13")])
def test_view_nothing_to_connect_to_stays_neutral(ready):
    view = connect_view(ready, False, False, False)
    assert (view.look, view.text, view.enabled, view.card) == ("plain", "connection.button.connect", True, "")


def test_view_connected_is_disconnect_even_if_usb_ready():
    for ready in (Ready(), Ready("usb", "iPhone 13")):
        view = connect_view(ready, wanted=True, connected=True, trying=False)
        assert (view.look, view.text, view.enabled, view.card) == ("stop", "connection.button.disconnect", True, "")


def test_view_trying_is_disabled_with_spinner():
    view = connect_view(Ready("usb", "iPhone 13"), wanted=True, connected=False, trying=True)
    assert (view.look, view.text, view.enabled, view.busy, view.card) == ("plain", "connection.button.connecting", False, True, "connecting")


def test_view_after_trying_window_the_user_can_stop():
    view = connect_view(Ready(), wanted=True, connected=False, trying=False)
    assert (view.look, view.enabled) == ("stop", True)


def test_is_trying_window_and_failures():
    assert is_trying(True, False, 0.0)
    assert is_trying(True, False, TRY_SEC - 0.1)
    assert not is_trying(True, False, TRY_SEC)
    assert not is_trying(True, True, 1.0)
    assert not is_trying(False, False, 1.0)
    assert not is_trying(True, False, 1.0, failed=True)
    assert not is_trying(True, False, None)


def test_every_view_text_is_translated():
    import json
    from pathlib import Path
    locales = Path(R.__file__).resolve().parents[2] / "locales"
    for lang in ("ja", "en"):
        keys = json.loads((locales / f"{lang}.json").read_text(encoding="utf-8"))
        for ready in (Ready(), Ready("usb", "x"), Ready("wifi", "x"), Ready("cable", "x")):
            for wanted, connected, trying in ((False, False, False), (True, False, True), (True, False, False), (True, True, False)):
                assert connect_view(ready, wanted, connected, trying).text in keys
