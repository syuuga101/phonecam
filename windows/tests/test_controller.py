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


def _language_controller():
    ctrl = receiver.Controller(persist=False, demo=True)
    ctrl.settings["language"] = "ja"
    from phonecam import i18n
    i18n.set_language("ja")
    return ctrl


def test_language_choice_switches_at_once_and_saves_only_when_kept():
    from phonecam import i18n
    ctrl = _language_controller()
    changed, trials, saved = Mock(), Mock(), Mock()
    ctrl.language_changed.connect(changed)
    ctrl.language_trial.connect(trials)
    ctrl.settings_changed.connect(saved)
    ctrl.set_language("en")
    assert i18n.get_language() == "en"                       # すぐ切り替わる
    assert ctrl.settings["language"] == "ja"                 # 保存はまだ
    assert ctrl.get("language") == "en"                      # 画面の選択肢は選んだ言語を見せる
    changed.assert_called_with("en")
    trials.assert_called_once_with("en", "ja")
    assert ctrl.language_trial_active
    ctrl.keep_language()
    assert ctrl.settings["language"] == "en" and i18n.get_language() == "en"
    saved.assert_called_with("language", "en")
    assert not ctrl.language_trial_active


def test_language_revert_restores_language_and_setting():
    from phonecam import i18n
    ctrl = _language_controller()
    ctrl.set_language("en")
    ctrl.revert_language()
    assert i18n.get_language() == "ja"
    assert ctrl.settings["language"] == "ja" and ctrl.get("language") == "ja"
    assert not ctrl.language_trial_active
    ctrl.revert_language()      # 何度呼んでも壊れない
    ctrl.keep_language()
    assert ctrl.settings["language"] == "ja"


def test_language_choice_that_changes_nothing_is_saved_without_asking():
    ctrl = _language_controller()
    trials = Mock()
    ctrl.language_trial.connect(trials)
    ctrl.set_language("system")         # 日本語の環境なら「システムに合わせる」でも見た目は同じ
    from phonecam import i18n
    if i18n.system_language() == "ja":
        trials.assert_not_called()
        assert ctrl.settings["language"] == "system"


def test_picking_the_old_language_again_cancels_the_trial():
    from phonecam import i18n
    ctrl = _language_controller()
    ctrl.set_language("en")
    ctrl.set_language("ja")
    assert not ctrl.language_trial_active and i18n.get_language() == "ja"
    assert ctrl.settings["language"] == "ja"


def test_language_change_rebuilds_status_text_in_the_new_language():
    from phonecam import i18n
    ctrl = _language_controller()
    ctrl.poll()
    ja = ctrl.status.state_text
    ctrl.set_language("en")
    assert ctrl.status.state_text == i18n.t("connection.state.off")
    assert ctrl.status.state_text != ja
    ctrl.revert_language()
    assert ctrl.status.state_text == ja


def test_callbacks_connected_while_building_a_page_can_be_dropped_together():
    from phonecam.core.callbacks import CallbackList
    signal = CallbackList()
    outside, inside, other = Mock(), Mock(), Mock()
    signal.connect(outside)
    scope, other_scope = object(), object()
    CallbackList.scope = scope
    try:
        signal.connect(inside)
    finally:
        CallbackList.scope = None
    CallbackList.scope = other_scope
    try:
        signal.connect(other)
    finally:
        CallbackList.scope = None
    CallbackList.drop_scope(None)       # 目印なしでは何も外さない
    signal.emit(1)
    assert outside.call_count == inside.call_count == other.call_count == 1
    CallbackList.drop_scope(scope)
    signal.emit(2)
    assert outside.call_count == 2 and other.call_count == 2 and inside.call_count == 1
