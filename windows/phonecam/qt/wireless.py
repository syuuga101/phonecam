"""Wi-Fi connection choices for the Qt connection page."""

from io import BytesIO
from queue import Empty, SimpleQueue
from threading import Thread
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from phonecam.core.wireless import PAIR_SECONDS, Pairing, discover, ipv4, merge_devices, read_device
from phonecam.i18n import t
from phonecam.qt.widgets import Combo, Hint, button


def local_addresses():
    flags = QNetworkInterface.InterfaceFlag
    return ipv4([entry.ip().toString()
                 for interface in QNetworkInterface.allInterfaces()
                 if interface.flags() & flags.IsUp and not interface.flags() & flags.IsLoopBack
                 for entry in interface.addressEntries()])


class PairDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("wireless.qr"))
        self.device = None
        self.inbox = SimpleQueue()
        layout = QVBoxLayout(self)
        self.note = Hint(t("wireless.firewall"))
        self.note.setMaximumWidth(430)
        layout.addWidget(self.note)
        qr_view = QLabel()
        qr_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_view.setAccessibleName(t("wireless.qr"))
        layout.addWidget(qr_view)
        self.clock = Hint()
        layout.addWidget(self.clock)
        close = button(t("wireless.close"))
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.listener = None
        self.deadline = time.monotonic() + PAIR_SECONDS
        self.elapsed = QTimer(self, interval=1000)
        self.elapsed.timeout.connect(self._tick)
        self.received = QTimer(self, interval=80)
        self.received.timeout.connect(self._receive)
        try:
            import qrcode
            self.listener = Pairing(local_addresses(), self.inbox.put)
            with BytesIO() as buffer:
                qrcode.make(self.listener.url, box_size=5).save(buffer, format="PNG")
                pixmap = QPixmap()
                if not pixmap.loadFromData(buffer.getvalue()):
                    raise ValueError("QR image could not be loaded")
            qr_view.setPixmap(pixmap)
        except (ImportError, OSError, ValueError):
            if self.listener:
                self.listener.close()
            self.note.setText(t("wireless.qr_error"))
            return
        self.clock.setText(t("wireless.expires", seconds=PAIR_SECONDS))
        self.elapsed.start()
        self.received.start()

    def _tick(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            if self.listener:
                self.listener.close()
            self.elapsed.stop()
            self.received.stop()
            self.layout().itemAt(1).widget().hide()
            self.clock.setText(t("wireless.expired"))
        else:
            self.clock.setText(t("wireless.expires", seconds=int(remaining + .999)))

    def _receive(self):
        try:
            self.device = self.inbox.get_nowait()
        except Empty:
            return
        self.accept()

    def done(self, result):
        self.elapsed.stop()
        self.received.stop()
        if self.listener:
            self.listener.close()
        super().done(result)


class WirelessOptions:
    def __init__(self, page, section, ctrl, warning):
        self.page, self.ctrl = page, ctrl
        self.discovered = []
        self.inbox = SimpleQueue()
        self.method = Combo()
        for key in ("lan", "tether", "hotspot", "qr"):
            self.method.addItem(t(f"wireless.{key}"), key)
        self.method_row = section.add_row("wifi", t("wireless.method"))
        self.method_row.addControl(self.method)
        self.actions = section.add_row("link", t("wireless.actions"))
        self.hotspot = self.actions.addControl(button(t("wireless.open_hotspot")))
        self.hotspot.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:network-mobilehotspot")))
        self.qr = self.actions.addControl(button(t("wireless.qr")))
        self.qr.clicked.connect(self._show_qr)
        self.scan = self.actions.addControl(button(t("wireless.scan")))
        self.scan.clicked.connect(self.refresh)
        self.devices = Combo()
        self.devices.setMaximumWidth(300)
        self.devices.activated.connect(self._select)
        section.add_row("camera", t("wireless.devices")).addControl(self.devices)
        self.warning = warning
        self.timer = QTimer(page, interval=100)
        self.timer.timeout.connect(self._result)
        self.hint_timer = QTimer(page, interval=12000, singleShot=True)
        self.hint_timer.timeout.connect(self._show_hint)
        self.method.currentIndexChanged.connect(self._mode_changed)
        self.method.activated.connect(lambda _index: self._show_qr() if self.method.currentData() == "qr" else None)
        self._mode_changed()
        self._populate()
        self.started = False

    def start(self):
        if not self.started and not self.ctrl.demo:
            self.started = True
            self.refresh()

    def _mode_changed(self):
        choice = self.method.currentData()
        self.hotspot.setVisible(choice == "hotspot")
        self.qr.setVisible(choice == "qr")
        self.actions.setHint(t(f"wireless.help.{choice}"))

    def status(self, status):
        pending = status.wanted and not status.connected and self.ctrl.get("mode") == "wifi"
        if not pending:
            self.hint_timer.stop()
            self.warning.hide()
        elif not self.hint_timer.isActive() and self.warning.isHidden():
            self.hint_timer.start()

    def _show_hint(self):
        status = self.ctrl.status
        self.warning.setVisible(status.wanted and not status.connected and self.ctrl.get("mode") == "wifi")

    def refresh(self):
        if self.timer.isActive():
            return
        self.scan.setEnabled(False)
        self.scan.setText(t("wireless.scanning"))
        addresses = local_addresses()
        inbox = self.inbox

        def work():
            try:
                inbox.put(discover(addresses))
            except Exception:
                inbox.put(None)

        Thread(target=work, daemon=True).start()
        self.timer.start()

    def _result(self):
        try:
            found = self.inbox.get_nowait()
        except Empty:
            return
        self.timer.stop()
        self.scan.setEnabled(True)
        self.scan.setText(t("wireless.scan"))
        if found is None:
            self.ctrl.toast.emit(t("wireless.scan_error"), "error")
        else:
            self.discovered = found
            self._populate()

    def _known(self):
        return [device for entry in self.ctrl.get("known_devices", []) if (device := read_device(entry))]

    def _populate(self):
        self.devices.clear()
        self.devices.addItem(t("wireless.choose"))
        for device in merge_devices(self._known(), self.discovered):
            for address in device.addresses:
                name = device.name or t("wireless.unknown_device")
                if device.code:
                    name = t("wireless.known", name=name)
                self.devices.addItem(f"{name} · {address}", (address, device.code))

    def _select(self, index):
        target = self.devices.itemData(index)
        if target:
            self._apply(*target)

    def _apply(self, address, code):
        if self.ctrl.wanted:
            self.ctrl.toggle_connect()
        previous_ip = self.ctrl.get("ip")
        self.ctrl.set_ip(address)
        if code or address != previous_ip:
            self.ctrl.set_pair_code(code)
        self.ctrl.set_mode("wifi")

    def _show_qr(self):
        dialog = PairDialog(self.page)
        dialog.exec()
        device = dialog.device
        dialog.deleteLater()
        if not device:
            return
        known = [device, *(item for item in self._known() if item.id != device.id)]
        self.ctrl.changed("known_devices", [item.stored() for item in known[:64]])
        self.ctrl.save_now()
        self._populate()
        self._apply(device.addresses[0], device.code)
        self.ctrl.toggle_connect()
