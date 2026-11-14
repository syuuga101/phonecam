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
