from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phonecam import controller as receiver


def test_phone_edits_update_settings_without_echo(monkeypatch):
    ctrl = receiver.Controller(persist=False, demo=True)
    send = Mock()
    monkeypatch.setattr(ctrl.link, "send_control", send)
    values = {"lens": "front", "zoom": 2.345, "torch": True, "micMuted": True}
    assert ctrl._apply_phone_edit({"phoneEdit": {"seq": 1, **values}}) is None
    assert ctrl._apply_phone_edit({"phoneEdit": {"seq": 2, **values}})
    assert (ctrl.get("lens"), ctrl.get("zoom"), ctrl.get("torch"), ctrl.get("mic_muted")) == (
        "front", 2.35, True, True,
    )
    assert ctrl._apply_phone_edit({"phoneEdit": {"seq": 2, "zoom": 3}}) is None
    assert ctrl._apply_phone_edit({"phoneEdit": {"seq": 3, "zoom": True, "torch": 0}}) is None
    assert ctrl.get("zoom") == 2.35 and ctrl.get("torch") is True
    ctrl._flush(float("inf"))
    send.assert_not_called()


def test_status_throttles_jitter_but_shows_connection_immediately():
    ctrl = receiver.Controller(persist=False, demo=True)
    notified = Mock()
    ctrl.status_changed.connect(notified)
    ctrl.state.update(connected=True, recv_fps=60, decode_ms=1.5)
    ctrl.poll(now=100)
    assert notified.call_count == 1 and ctrl.status.connected
    ctrl.state.update(decode_ms=2.0)
    assert ctrl.poll(now=100.1).decode_ms == 2.0
    assert notified.call_count == 1 and ctrl.status.decode_ms == 1.5
    ctrl.poll(now=100.6)
    assert notified.call_count == 2 and ctrl.status.decode_ms == 2.0
    ctrl.state.update(connected=False)
    ctrl.poll(now=100.7)
    assert notified.call_count == 3 and not ctrl.status.connected


def test_update_timeout_reenables_check_button(monkeypatch):
    ctrl = receiver.Controller(persist=False, demo=True)
    ctrl.demo = False  # Allow starting a check. No background work is launched.
    mock_update = SimpleNamespace(CHECK_TIMEOUT_SEC=5, parse_version=lambda version: (1, 2, 3),
                                  fetch_latest=Mock())
    monkeypatch.setattr(receiver, "phonecam_update", mock_update)
    monkeypatch.setattr(receiver, "APP_REPO", "example/phonecam")
    clock = Mock(return_value=100.0)
    monkeypatch.setattr(receiver.time, "monotonic", clock)
    background = Mock()
    monkeypatch.setattr(ctrl, "run_background", background)
    notified = Mock()
    ctrl.update_status_changed.connect(notified)
    ctrl.check_updates()
    ctrl.check_updates()
    background.assert_called_once()
    assert ctrl.update_check_busy
    assert notified.call_args.args[1] is True

    ctrl.demo = True  # Only the watchdog runs, without hardware or periodic update checks.
    clock.return_value = 119.0
    ctrl.tick()
    assert ctrl.update_check_busy
    clock.return_value = 121.0
    ctrl.tick()
    assert not ctrl.update_check_busy
    assert notified.call_args.args[1] is False
    assert ctrl._update_status[0] == "update.status.error"
    mock_update.fetch_latest.assert_not_called()


def test_stale_phone_edit_does_not_lower_the_last_seen_sequence():
    ctrl = receiver.Controller(persist=False, demo=True)
    ctrl._apply_phone_edit({"phoneEdit": {"seq": 1, "zoom": 1.0}})
    ctrl._apply_phone_edit({"phoneEdit": {"seq": 3, "zoom": 3.0}})
    ctrl._apply_phone_edit({"phoneEdit": {"seq": 2, "zoom": 2.0}})
    ctrl._apply_phone_edit({"phoneEdit": {"seq": 3, "zoom": 2.5}})
    assert ctrl.get("zoom") == 3.0
