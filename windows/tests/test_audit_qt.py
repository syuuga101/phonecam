"""監査 #10 / #17 の Qt テスト。PySide6 が無い環境では skip。"""
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from phonecam.controller import Controller  # noqa: E402
from phonecam.i18n import t  # noqa: E402
from phonecam.qt.main_window import NAV_ITEMS, MainWindow  # noqa: E402
from phonecam.qt.plain import PlainLabel  # noqa: E402
from phonecam.qt.wireless import CodeDialog  # noqa: E402

EVIL = "<img src=x width=1000 height=1000>"


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


def test_html_device_name_does_not_grow_code_dialog_label(app):
    plain = CodeDialog(None, "iPhone")
    evil = CodeDialog(None, EVIL)
    for dialog in (plain, evil):
        dialog.show()
    app.processEvents()
    heads = [d.findChildren(QLabel)[0] for d in (plain, evil)]
    assert heads[1].textFormat() == Qt.TextFormat.PlainText
    assert heads[1].sizeHint().height() <= heads[0].sizeHint().height() + 4
    assert heads[1].height() < 100
    for dialog in (plain, evil):
        dialog.close()


def test_labels_never_auto_detect_html(app):
    label = PlainLabel(EVIL)
    assert label.textFormat() == Qt.TextFormat.PlainText
    assert label.sizeHint().height() < 100


def test_every_label_in_main_window_is_plain_text(app):
    ctrl = Controller(persist=False, demo=True)
    win = MainWindow(ctrl, tick=False)
    win.resize(1300, 800)
    win.show()
    app.processEvents()
    try:
        for row in range(len(NAV_ITEMS)):
            win.select(row)
            app.processEvents()
        bad = [lb.text() for lb in win.findChildren(QLabel) if lb.textFormat() != Qt.TextFormat.PlainText]
        assert bad == []
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(("enabled", "pc", "phone", "key"), [
    (False, False, True, "off"),
    (True, False, True, "phone"),
    (True, True, False, "muted"),
    (True, True, True, "both"),
    (True, False, False, "on"),
])
def test_dock_mic_label_follows_both_mutes(app, enabled, pc, phone, key):
    ctrl = Controller(persist=False, demo=True)
    win = MainWindow(ctrl, tick=False)
    try:
        ctrl.state.update(connected=True, phone_status={"micMuted": phone})
        ctrl.poll(now=100)
        ctrl.changed("mic_enabled", enabled)
        ctrl.changed("mic_mute", pc)
        win.dock.set_camera(ctrl)
        win.dock._refresh_info()
        assert win.dock.mic_label.text() == t("compact.sum.mic." + key)
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()
