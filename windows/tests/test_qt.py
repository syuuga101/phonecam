from unittest.mock import Mock

import pytest
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

import phonecam_setup as setup
from phonecam.controller import Controller
from phonecam.core.cameras import CameraGroup
from phonecam.qt.multicam import AddTile
from phonecam.qt.pages import cameras
from phonecam.qt.setup_wizard import SetupWizardDialog


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


def test_camera_page_keeps_one_tile_per_camera_and_hides_add_at_limit(app):
    primary = Controller(persist=False, demo=True)
    group = CameraGroup(primary)
    group.add()
    page = cameras.build(primary)
    page.show()
    try:
        assert [tile.camera for tile in page.tiles] == group.cameras
        assert any(not tile.isHidden() for tile in page.findChildren(AddTile))
        group.add()
        group.add()
        assert len(page.tiles) == 4
        assert [tile.camera for tile in page.tiles] == group.cameras
        assert not any(tile.isVisible() for tile in page.findChildren(AddTile))
    finally:
        page.close()
        page.deleteLater()


def test_wizard_next_moves_welcome_to_check(app):
    controller = setup.SetupController(Mock(), engine=Mock())
    controller.checked = True
    controller.statuses = {component["id"]: {"state": setup.STATE_OK, "detail": ""}
                           for component in setup.COMPONENTS}
    controller.apply_defaults()
    wizard = SetupWizardDialog(controller)
    wizard.show()
    try:
        assert wizard.page == setup.PAGE_WELCOME
        assert wizard.next_btn.isEnabled()
        wizard.next_btn.click()
        assert wizard.page == setup.PAGE_CHECK
        assert wizard.panel_card is not None
    finally:
        wizard.close()
        wizard.deleteLater()


@pytest.mark.parametrize("language", ["ja", "en"])
def test_wizard_iphone_pages_follow_sidestore_flow_and_are_translated(app, monkeypatch, language):
    import re

    from PySide6.QtWidgets import QLabel, QPushButton

    import phonecam_iphone as iphone
    from phonecam import i18n
    from phonecam.qt.setup_common import Fold

    monkeypatch.setattr(i18n, "_LANG", language)
    monkeypatch.setattr(iphone, "query_usbmux", lambda *a, **k: {"service": True, "usb": True, "trusted": True})
    monkeypatch.setattr(iphone, "probe_phonecam", lambda *a, **k: False)
    monkeypatch.setattr(setup, "find_iloader", lambda: r"C:\Program Files\iloader\iloader.exe")
    controller = setup.SetupController(Mock(), engine=Mock())
    wizard = SetupWizardDialog(controller, start="iphone")
    wizard.show()
    raw_key = re.compile(r"^(setup|status|common)\.[a-z0-9_.]+$")
    try:
        assert [step["id"] for step in iphone.IPHONE_STEPS] == [
            "usb", "trust", "iloader", "devmode", "vpn", "sidestore", "phonecam", "test"]
        for index, step in enumerate(iphone.IPHONE_STEPS):
            wizard.show_page(setup.PAGE_IPHONE + index)
            wizard._pump_timer.stop()
            wizard._hop_timer.stop()
            for fold in wizard.findChildren(Fold):
                fold.set_open(True)
            texts = [w.text().strip() for w in wizard.findChildren(QLabel)]
            texts += [w.text().strip() for w in wizard.findChildren(QPushButton)]
            assert not [text for text in texts if raw_key.match(text)], step["id"]
            assert i18n.t(step["title"]) in texts
            if step["id"] == "iloader":
                assert len(wizard.findChildren(Fold)) == 2       # AltStore から乗り換える人 / ペアリングファイル
                assert wizard.iloader_btn is not None and wizard.iloader_btn.isEnabled()
            if step["id"] == "sidestore":
                assert len(wizard.findChildren(Fold)) == 1       # ペアリングファイル
            if step["id"] in setup.MANUAL_STEPS:
                assert wizard.confirm_cb is not None
        # 「済んだ」を押すと ✓ になり、「次へ」で次の段階へ進む
        wizard.show_page(setup.PAGE_IPHONE + 3)
        wizard._pump_timer.stop()
        wizard.confirm_cb.setChecked(True)
        assert iphone.step_status("devmode", {}, wizard._ctx())[0] == iphone.DONE
        wizard.next_btn.click()
        assert wizard.page == setup.PAGE_IPHONE + 4
        wizard._pump_timer.stop()
    finally:
        wizard.close()
        wizard.deleteLater()


# ---------------------------------------------------------------------------
# 翻訳キーがそのまま画面に出ていないこと / 言語の切り替え
# ---------------------------------------------------------------------------

from qt_smoke import untranslated_texts  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from phonecam import i18n  # noqa: E402
from phonecam.qt.language_dialog import LanguageConfirmDialog  # noqa: E402
from phonecam.qt.main_window import NAV_ITEMS, MainWindow  # noqa: E402
from phonecam.qt.widgets import Expander  # noqa: E402


def _settle(app):
    for _ in range(3):
        app.processEvents()


def _leftovers_on_every_page(app, win):
    """全ページ (折りたたみを全部開いた状態) の画面中のすべての文字に、翻訳キーが残っていないか。"""
    bad = []
    for row, (key, _icon) in enumerate(NAV_ITEMS):
        if win.nav.item(row).isHidden():
            continue
        win.select(row)
        _settle(app)
        page = win.current_page()
        for expander in page.findChildren(Expander):
            expander.set_expanded(True, animate=False, save=False)
        _settle(app)
        bad += [(key, *item) for item in untranslated_texts(page)]
    bad += [("window", *item) for item in untranslated_texts(win)]
    return bad


@pytest.mark.parametrize("language", ["ja", "en"])
def test_no_translation_key_is_shown_on_any_page(app, language):
    ctrl = Controller.demo_connected(language)
    win = MainWindow(ctrl, tick=False)
    win.resize(1280, 800)
    win.show()
    try:
        _settle(app)
        assert not win.page_errors
        win._add_camera()      # 「カメラ」ページ・カメラの切り替え・音声の受け取り先も出す
        _settle(app)
        assert _leftovers_on_every_page(app, win) == []
    finally:
        win.close()
        win.deleteLater()


def test_live_language_switch_rebuilds_everything_and_keeps_state(app):
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    win.resize(1280, 800)
    win.show()
    try:
        status_row = next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "status")
        win.select(status_row)
        _settle(app)
        iphone = next(e for e in win.current_page().findChildren(Expander) if e.key.endswith("status.iphone.title"))
        iphone.set_expanded(True, animate=False)
        win.current_page().verticalScrollBar().setValue(0)
        before = win.current_page()
        ctrl.language_trial.disconnect(win._language_trial)       # 確認のダイアログは別のテストで確かめる
        ctrl.set_language("en")
        win.retranslate()
        _settle(app)
        assert i18n.get_language() == "en"
        assert win.nav.item(status_row).text() == i18n.t("nav.status") == "Status"
        assert win.current_page() is not before                   # ページは作り直された
        assert win.nav.currentRow() == status_row                 # 見ているページは同じ
        iphone = next(e for e in win.current_page().findChildren(Expander) if e.key.endswith("status.iphone.title"))
        assert iphone.expanded                                    # 開いていた折りたたみは開いたまま
        assert iphone.title_label.text() == i18n.t("status.iphone.title")
        labels = {label.text() for label in win.current_page().findChildren(type(iphone.title_label))}
        assert i18n.t("status.iphone.factors") in labels
        assert untranslated_texts(win) == []
        ctrl.revert_language()
        win.retranslate()
        _settle(app)
        assert i18n.get_language() == "ja"
        assert win.nav.item(status_row).text() == i18n.t("nav.status")
        assert ctrl.settings["language"] == "ja"
    finally:
        win.close()
        win.deleteLater()


def test_language_dialog_counts_down_and_reverts_when_time_runs_out(app):
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    try:
        ctrl.language_trial.disconnect(win._language_trial)
        ctrl.set_language("en")
        dialog = LanguageConfirmDialog(win, "ja", seconds=3, autostart=False)
        assert "3" in dialog.countdown_label.text() and "Japanese" in dialog.countdown_label.text()
        dialog.tick()
        assert "2" in dialog.countdown_label.text()
        QTimer.singleShot(0, dialog.tick)
        QTimer.singleShot(0, dialog.tick)          # 0 秒になったら閉じる
        win.ask_language("ja", dialog)
        assert not ctrl.language_trial_active
        assert i18n.get_language() == "ja" and ctrl.settings["language"] == "ja"
    finally:
        win.close()
        win.deleteLater()


def test_language_dialog_keep_saves_and_revert_button_restores(app):
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    try:
        ctrl.language_trial.disconnect(win._language_trial)
        ctrl.set_language("en")
        dialog = LanguageConfirmDialog(win, "ja", autostart=False)
        QTimer.singleShot(0, dialog.keep_button.click)
        win.ask_language("ja", dialog)
        assert ctrl.settings["language"] == "en" and i18n.get_language() == "en"
        ctrl.set_language("ja")
        dialog = LanguageConfirmDialog(win, "en", autostart=False)
        QTimer.singleShot(0, dialog.revert_button.click)
        win.ask_language("en", dialog)
        assert ctrl.settings["language"] == "en" and i18n.get_language() == "en"
    finally:
        win.close()
        win.deleteLater()


@pytest.mark.parametrize("button, expected", [("keep_button", "en"), ("revert_button", "ja")])
def test_choosing_a_language_in_settings_asks_then_saves_or_reverts(app, button, expected):
    from phonecam.qt.widgets import Combo
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    win.resize(1280, 800)
    win.show()
    try:
        row = next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "settings")
        win.select(row)
        _settle(app)
        combo = next(c for c in win.current_page().findChildren(Combo) if c.findData("en") >= 0)
        shown = []

        def press():
            dialog = win._language_dialog
            shown.append(dialog is not None and i18n.get_language() == "en"
                         and dialog.title_label.text() == i18n.t("settings.language.confirm"))
            getattr(dialog, button).click()

        QTimer.singleShot(400, press)
        index = combo.findData("en")
        combo.setCurrentIndex(index)
        combo.activated.emit(index)       # 選んだ (setter = ctrl.set_language)
        for _ in range(6):
            app.processEvents()
        assert shown == [True]            # すぐ英語になり、英語のダイアログが出た
        assert ctrl.settings["language"] == expected
        assert i18n.get_language() == expected
        _settle(app)
        assert win.nav.item(row).text() == i18n.t("nav.settings")
        combo = next(c for c in win.current_page().findChildren(Combo) if c.findData("en") >= 0)
        assert combo.currentData() == expected    # 選択肢の表示も確定した言語に合う
    finally:
        win.close()
        win.deleteLater()


# ---------------------------------------------------------------------------
# 一発で起動する: 起動時に開くウィンドウはメインの 1 つだけ
# ---------------------------------------------------------------------------

def _other_windows(win):
    return [w for w in QApplication.topLevelWidgets() if w is not win and w.isVisible()]


def test_setup_opens_inside_the_main_window_not_as_another_window(app):
    from phonecam.qt.setup_wizard import SetupWizardDialog, SetupWizardPanel
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    win.resize(1100, 760)
    win.show()
    try:
        _settle(app)
        ctrl._setup_open = True
        ctrl.setup_requested.emit(False, None)       # 初回起動・更新後に controller が出す合図
        _settle(app)
        panel = win.setup_panel
        assert isinstance(panel, SetupWizardPanel)
        assert win.root_stack.currentWidget() is panel
        assert not win.findChildren(SetupWizardDialog)
        assert _other_windows(win) == []
        assert panel.page == 0 and panel.next_btn.isVisible()
        # 「あとで」: 普段の画面に戻り、設定ページの再表示もできる
        panel._skip()
        _settle(app)
        assert win.setup_panel is None
        assert win.root_stack.currentWidget() is win.main_area
        assert not ctrl._setup_open
        ctrl.setup_requested.emit(False, None)
        _settle(app)
        assert win.setup_panel is not None
        win.setup_panel._skip()
        _settle(app)
    finally:
        win.close()
        win.deleteLater()


def test_camera_registration_is_announced_by_a_bar_without_any_dialog(app, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox

    def boom(*args, **kwargs):
        raise AssertionError("a dialog was opened for the registration prompt")

    monkeypatch.setattr(QMessageBox, "question", boom)
    monkeypatch.setattr(QMessageBox, "exec", boom)
    monkeypatch.setattr(QDialog, "exec", boom)
    ctrl = Controller.demo_connected("ja")
    win = MainWindow(ctrl, tick=False)
    win.resize(1100, 760)
    win.show()
    try:
        _settle(app)
        registered = []
        ctrl.change_camera_registration = lambda action: registered.append(action)
        ctrl.camera_prompt.emit("missing")
        _settle(app)
        assert win.camera_bar.isVisible()
        assert _other_windows(win) == []
        assert win.camera_bar.label.text() == i18n.t("setup.first_run.bar_register")
        win.camera_bar._buttons[0].click()            # 「準備する」を押したときだけ登録 (UAC) に進む
        assert registered == ["register"]
        ctrl.camera_prompt.emit("outdated")
        _settle(app)
        assert win.camera_bar.label.text() == i18n.t("setup.first_run.bar_update")
        ctrl.camera_registration = "ok"
        ctrl.camera_registration_changed.emit()
        _settle(app)
        assert not win.camera_bar.isVisible()
        ctrl.camera_prompt.emit("missing")
        _settle(app)
        win.camera_bar._buttons[1].click()            # 「あとで」
        assert not win.camera_bar.isVisible()
    finally:
        win.close()
        win.deleteLater()


def test_second_launch_hands_over_to_the_running_window(app):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from phonecam.qt import single_instance
    name = "PhoneCamReceiver-test-%d" % os.getpid()
    first = single_instance.claim(name, relaunch_wait=0)
    assert first is not None
    raised = []
    first.activated.connect(lambda: raised.append(True))
    try:
        # 2 つめは本物と同じく別のプロセスで起動する (動いている側のイベントループは、こちらで回す)
        code = ("from PySide6.QtWidgets import QApplication\n"
                "app = QApplication([])\n"
                "from phonecam.qt import single_instance\n"
                "import sys\n"
                "sys.exit(0 if single_instance.claim(%r, relaunch_wait=0) is None else 1)\n" % name)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]), env=env)
        deadline = time.monotonic() + 30
        while (proc.poll() is None or not raised) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if proc.poll() is None:
            proc.kill()
        assert proc.returncode == 0    # 2 つめは何も開かずに終わる
        assert raised                  # 動いている方が前面に出す合図を受け取った
    finally:
        first.close()
    third = single_instance.claim(name, relaunch_wait=0)                  # 終わったあとは、また起動できる
    assert third is not None
    third.close()
