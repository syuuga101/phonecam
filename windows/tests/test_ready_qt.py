"""USB で見えている未接続 → 接続ページ (状態カード・緑の接続ボタン・端末の行)。PySide6 が無い環境では skip。"""
import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

from phonecam.controller import Controller  # noqa: E402
from phonecam.core.ready import CABLE, READY, UsbSeen  # noqa: E402
from phonecam.core.wireless import Device  # noqa: E402
from phonecam.i18n import t  # noqa: E402
from phonecam.qt.main_window import NAV_ITEMS, MainWindow  # noqa: E402
from phonecam.qt.widgets import ActionButton, GO_GREEN, go_color  # noqa: E402
from phonecam.qt.wireless import UsbLine  # noqa: E402

SERIAL = "00008110-001A2B3C4D5E6F01"
KNOWN = Device("5f1c0c1e-9a53-4a0e-8b8e-0b6f2f1d3a11", "iPhone 13", ("192.168.1.81",), "", "iPhone 13", "", "11" * 32)


@pytest.fixture(scope="module")
def app():
    qt = QApplication.instance() or QApplication([])
    yield qt
    qt.processEvents()


def connection_page(app, seen, found):
    ctrl = Controller(persist=False, demo=True)
    ctrl.settings["known_devices"] = [KNOWN.stored()]
    ctrl.demo_discovered = list(found)
    ctrl.demo_usb_seen = dict(seen)
    ctrl.poll()
    win = MainWindow(ctrl, tick=False)
    win.resize(1280, 800)
    win.show()
    win.select(next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "connection"))
    app.processEvents()
    return ctrl, win, win.current_page()


def test_usb_ready_makes_green_connect_button_and_card(app):
    ctrl, win, page = connection_page(app, {SERIAL: UsbSeen(READY, KNOWN.id)}, [])
    try:
        assert isinstance(page.connect_btn, ActionButton)
        assert (page.connect_btn.look, page.connect_btn.text()) == ("go", t("connection.button.connect_usb"))
        assert page.connect_btn.isEnabled()
        assert GO_GREEN["light"]["fill"].lower() in page.connect_btn.styleSheet().lower() or \
            GO_GREEN["dark"]["fill"].lower() in page.connect_btn.styleSheet().lower()
        assert page.state_label.text() == t("connection.card.ready")
        assert page.dot.key == "ready" and go_color(page.palette()).isValid()
        assert "iPhone 13" in page.sub_label.text()
        assert [line.label.text() for line in page.findChildren(UsbLine)] == [t("wireless.usb.ready")]
    finally:
        win.close()


def test_cable_only_stays_neutral_and_asks_to_open_the_app(app):
    ctrl, win, page = connection_page(app, {SERIAL: UsbSeen(CABLE, "")}, [])
    try:
        assert (page.connect_btn.look, page.connect_btn.text()) == ("plain", t("connection.button.connect"))
        assert page.state_label.text() == t("connection.card.off")
        assert [line.label.text() for line in page.findChildren(UsbLine)] == [t("wireless.usb.cable")]
    finally:
        win.close()


def test_nothing_to_connect_to_is_the_old_neutral_state(app):
    ctrl, win, page = connection_page(app, {}, [])
    try:
        assert (page.connect_btn.look, page.connect_btn.text()) == ("plain", t("connection.button.connect"))
        assert page.state_label.text() == t("connection.card.off")
        assert not page.findChildren(UsbLine)
    finally:
        win.close()


def test_wifi_found_is_green_connect_wifi(app):
    found = [Device(KNOWN.id, "iPhone 13", ("192.168.1.81",), "", "iPhone 13")]
    ctrl, win, page = connection_page(app, {}, found)
    try:
        assert (page.connect_btn.look, page.connect_btn.text()) == ("go", t("connection.button.connect_wifi"))
    finally:
        win.close()


def test_trying_disables_the_button_and_shows_spinner(app):
    ctrl, win, page = connection_page(app, {SERIAL: UsbSeen(READY, KNOWN.id)}, [])
    try:
        ctrl.wanted = True
        ctrl.poll()
        app.processEvents()
        assert not page.connect_btn.isEnabled()
        assert page.connect_btn.text() == t("connection.button.connecting")
        assert page.state_label.text() == t("connection.button.connecting")
        assert not page.spinner.isHidden()
    finally:
        win.close()
