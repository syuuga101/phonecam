"""Wi-Fi connection choices for the Qt connection page."""

from dataclasses import replace
from io import BytesIO
from queue import Empty, SimpleQueue
from threading import Thread
import socket
import time

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget,
)

from phonecam.core.handshake import PairSession
from phonecam.core.usbmux import wifi_connect
from phonecam.core.wireless import (
    PAIR_SECONDS, Device, Pairing, discover, hotspot_on, ipv4, merge_devices, on_tether, read_device, route_of,
)
from phonecam.core.ready import READY, connect_ready, usb_pick, usb_state_of
from phonecam.core.settings import normalize_pair_code, valid_pair_code
from phonecam.i18n import t
from phonecam.labels import clip_text
from phonecam.qt import icons
from phonecam.qt.widgets import (
    ActionButton, EmptyState, FitBox, FixedHint, GoHint, GoIcon, IconLabel, Hint, Row, refit, StatusValue, _ExpanderHeader, _THEME_EVENTS, bound_line, button, ref_palette,
    secondary_color, separator_color, text_color,
)
from phonecam.qt.plain import PlainLabel


def local_addresses():
    flags = QNetworkInterface.InterfaceFlag
    return ipv4([entry.ip().toString()
                 for interface in QNetworkInterface.allInterfaces()
                 if interface.flags() & flags.IsUp and not interface.flags() & flags.IsLoopBack
                 for entry in interface.addressEntries()])


class PairDialog(QDialog):
    """QR で iPhone を追加する。identity はこの PC の長期鍵 (QR にはそのフィンガープリントが入る)。"""

    def __init__(self, parent, identity):
        super().__init__(parent)
        self.setWindowTitle(t("wireless.add"))
        self.device = None
        self.inbox = SimpleQueue()
        layout = QVBoxLayout(self)
        self.note = Hint(t("wireless.firewall"))
        self.note.setMaximumWidth(430)
        layout.addWidget(self.note)
        qr_view = PlainLabel()
        qr_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_view.setAccessibleName(t("wireless.add"))
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
            self.listener = Pairing(local_addresses(), self.inbox.put, identity)
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


class Spinner(QWidget):
    """小さな回転スピナー (16px)。start() の間だけタイマーを回す。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.angle = 0
        self.timer = QTimer(self, interval=60)
        self.timer.timeout.connect(self._step)
        self.hide()

    def start(self):
        self.show()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def _step(self):
        self.angle = (self.angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(secondary_color(ref_palette(self)), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(2, 2, 12, 12), -self.angle * 16, 270 * 16)
        painter.end()


class IconButton(QPushButton):
    """アイコンだけのボタン (32px)。テーマが変わったらアイコンを塗り直す。"""

    def __init__(self, name, tip, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedSize(32, 32)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._render()

    def _render(self):
        self.setIcon(icons.icon(self._name, text_color(self)))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in _THEME_EVENTS and hasattr(self, "_name"):
            self._render()

    def showEvent(self, event):
        super().showEvent(event)
        self._render()


class Separator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), separator_color(ref_palette(self)))
        painter.end()


class NameLabel(PlainLabel):
    doubled = Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubled.emit()


class Disclosure(QWidget):
    """小さな折りたたみ (見出し + 中身)。Expander より軽く、カードの中に置く。"""

    toggled = Signal(bool)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = _ExpanderHeader()
        self.header.setCheckable(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setAccessibleName(title)
        self.header.setMinimumHeight(44)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(12)
        self.arrow = IconLabel("chevron_down")
        self.arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label = PlainLabel(title)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.arrow)
        row.addWidget(label, 1)
        outer.addWidget(self.header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 8)
        self.body_layout.setSpacing(0)
        self.body.hide()
        outer.addWidget(self.body)
        self.header.clicked.connect(lambda: self.set_open(not self.body.isVisible()))

    def add(self, widget):
        self.body_layout.addWidget(widget)
        refit(self)
        return widget

    def is_open(self):
        return not self.body.isHidden()

    def set_open(self, value):
        value = bool(value)
        if value == self.is_open():
            return
        self.body.setVisible(value)
        self.header.setChecked(value)
        self.arrow.set_icon("chevron_up" if value else "chevron_down")
        refit(self)
        self.toggled.emit(value)


def default_session(address, identity, on_state):
    """画面から使うペアリング (コード方式)。Wi-Fi の address の iPhone に、PC のほうから始める。"""
    return PairSession(lambda: wifi_connect(address), identity, socket.gethostname()[:40], on_state).start()


class CodeDialog(QDialog):
    """まだペアリングしていない iPhone につなぐときの、6桁の照合コードの入力。
    PC が iPhone に接続を始めると、iPhone の画面にその接続だけの 6 桁が出る。それを入力して合えば、
    お互いの鍵を保存する (次からは入力不要)。address が無いとき (デモ) は、入力欄だけを出す。
    コードは使い捨て: 違っていたら作り直す (もう一度「つなぐ」から)。"""

    def __init__(self, parent, name, address=None, identity=None, session=default_session):
        super().__init__(parent)
        self.setWindowTitle(t("wireless.code_title"))
        self.declined = False
        self.session = None
        self.peer = None             # ペアリングできたときの相手の情報 (secure.parse_peer の形)
        self.states = SimpleQueue()
        self._submitted = False
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        head = PlainLabel(t("wireless.code_for", name=clip_text(name)))
        font = head.font()
        font.setWeight(QFont.Weight.DemiBold)
        head.setFont(font)
        layout.addWidget(head)
        self.note = Hint(t("wireless.code_note"))
        self.note.setMinimumWidth(320)
        layout.addWidget(self.note)
        self.edit = QLineEdit()
        self.edit.setMaxLength(6)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setPlaceholderText("000000")
        big = self.edit.font()
        big.setPointSizeF(18.0)
        big.setWeight(QFont.Weight.DemiBold)
        self.edit.setFont(big)
        self.edit.textEdited.connect(self._edited)
        self.edit.returnPressed.connect(self._accept)
        layout.addWidget(self.edit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(t("wireless.cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = button(t("wireless.connect"), primary=True)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        layout.addLayout(buttons)
        self.code = ""
        self.poll = QTimer(self, interval=100)
        self.poll.timeout.connect(self._poll)
        if address:
            self.session = session(address, identity, self.states.put)
            self.poll.start()

    def _poll(self):
        while True:
            try:
                state = self.states.get_nowait()
            except Empty:
                return
            if state == "paired":
                self.peer = self.session.result
                self.accept()
                return
            if state == "declined":
                self.declined = True
                self.reject()
                return
            if state == "waiting":
                self.note.setText(t("wireless.code_note_wait"))
            elif state == "shown":
                self.note.setText(t("wireless.code_note_popup"))
            elif state == "background":
                self.note.setText(t("wireless.code_note_background"))
            elif state == "code":
                self.note.setText(t("wireless.code_note"))
            elif state == "mismatch":
                self._finish(t("wireless.code_wrong"))
            elif state in ("busy", "limited"):
                self._finish(t("wireless.code_busy"))
            elif state == "legacy":
                self._finish(t("wireless.code_legacy"))
            else:
                self._finish(t("wireless.code_failed"))

    def _finish(self, text):
        """このコードではもう進めない (使い捨て)。理由を出して、入力を止める。"""
        self.note.setText(text)
        self.edit.setEnabled(False)
        self.ok.setEnabled(False)
        self._submitted = True

    def done(self, result):
        self.poll.stop()
        session, self.session = self.session, None
        if session is not None:
            session.close()   # iPhone のポップアップも閉じてもらう
        super().done(result)

    def _edited(self, text):
        code = normalize_pair_code(text)
        if code != text:
            self.edit.setText(code)
        self.ok.setEnabled(valid_pair_code(code) and not self._submitted)

    def _accept(self):
        code = normalize_pair_code(self.edit.text())
        if not valid_pair_code(code) or self._submitted:
            return
        self.code = code
        if self.session is None:
            self.accept()      # iPhone につながない (デモ)
            return
        self._submitted = True
        self.edit.setEnabled(False)
        self.ok.setEnabled(False)
        self.note.setText(t("wireless.code_checking"))
        self.session.submit(code)


class Entry:
    """一覧の 1 行ぶんの情報。"""

    def __init__(self, device, address, route, found):
        self.device, self.address, self.route, self.found = device, address, route, found

    @property
    def name(self):
        return clip_text(self.device.title) or t("wireless.unknown_device")

    @property
    def registered(self):
        return self.device.registered

    @property
    def needs_repair(self):
        return self.device.needs_repair

    def detail(self, state="", usb=""):
        """2 行目の文。usb (READY / CABLE) のときは、先頭に出す「USB 接続済み」などの後ろに続く部分だけ (機種名と IP は省く)。"""
        if usb and not state and not self.needs_repair:
            parts = [t("wireless.route." + self.route)] if self.found else []
            if self.registered:
                parts.append(t("wireless.route.registered"))
            return " · ".join(parts)
        parts = [self.device.model, self.address]
        if self.needs_repair:
            parts.append(t("wireless.route.repair"))
        elif state == "connected":
            # つながっている行に「今は見つかりません」は矛盾するので出さない (検索に出ない USB / 検索前でも)
            parts.append(t("wireless.route.registered") if self.registered else "")
        elif self.found:
            parts.append(t("wireless.route." + self.route))
            if self.registered:
                parts.append(t("wireless.route.registered"))
        else:
            parts.append(t("wireless.route.registered"))
            parts.append(t("wireless.not_found"))
        return " · ".join(p for p in parts if p)

    def key(self):
        d = self.device
        return (d.id, d.title, d.model, d.key, d.repair, self.address, self.route, self.found)


class UsbLine(QWidget):
    """端末の行の 2 行目: [USB アイコン] USB 接続済み (緑) · 同じWi-Fi · 登録済み。
    ケーブルだけ見えている (アプリが応答していない) ときは、緑にせず「USB（PhoneCam を開いてください）」を通常の色で出す。"""

    def __init__(self, usb, rest, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        ready = usb == READY
        lay.addWidget((GoIcon if ready else IconLabel)("usb", 14), 0, Qt.AlignmentFlag.AlignVCenter)
        if ready:
            self.label = GoHint(t("wireless.usb.ready"))
        else:
            self.label = Hint(t("wireless.usb.cable"), wrap=False)
        lay.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        if rest:
            lay.addWidget(FixedHint("· " + rest), 1, Qt.AlignmentFlag.AlignVCenter)
        else:
            lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)


class DeviceRow(FitBox):
    """見つかった iPhone 1 台: アイコン | 名前 + 詳細 | 状態 / つなぐ・切断 / …"""

    def __init__(self, entry, state, owner, parent=None):
        super().__init__(60, parent)
        self.setMinimumHeight(60)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        lay.addWidget(IconLabel("phone", 20), 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = NameLabel(entry.name)
        font = name.font()
        font.setWeight(QFont.Weight.Bold)
        name.setFont(font)
        texts.addWidget(name)
        usb = "" if state else owner.usb_of(entry)
        if usb and not entry.needs_repair:
            texts.addWidget(UsbLine(usb, entry.detail(state, usb)))
        else:
            usb = ""
            texts.addWidget(FixedHint(entry.detail(state)))
        lay.addLayout(texts, 1)
        if entry.registered:
            name.setToolTip(t("wireless.rename_tip"))
            name.doubled.connect(lambda: owner.rename(entry))
        if state == "connected":
            status = StatusValue(wrap=False)
            status.set_status(t("wireless.connected"), "ok")
            lay.addWidget(status, 0, Qt.AlignmentFlag.AlignVCenter)
        elif state == "connecting":
            lay.addWidget(Hint(t("wireless.connecting"), wrap=False), 0, Qt.AlignmentFlag.AlignVCenter)
        # どの iPhone がどのカメラかは、接続ページの「変更…」だけで決める。ここは使用中のカメラを文字で出すだけ
        camera = owner._camera(entry)
        group = getattr(owner.ctrl, "camera_group", None)
        if group and len(group.cameras) > 1 and camera is not None:
            lay.addWidget(Hint(t("multi.in_use", camera=group.label(camera)), wrap=False), 0,
                          Qt.AlignmentFlag.AlignVCenter)
        # USB で見えていて今つなげるときだけ緑の塗り。切断は中立 (ホバーで赤っぽい文字)
        action = ActionButton(t("wireless.disconnect") if state else t("wireless.repair") if entry.needs_repair else t("wireless.connect"),
                              "stop" if state else "go" if usb == READY else "plain")
        action.setMinimumWidth(84)
        if state:
            action.clicked.connect(lambda: owner.disconnect(entry))
        else:
            action.clicked.connect(lambda: owner.connect_entry(entry))
        lay.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        if entry.registered:
            more = IconButton("more", t("wireless.more"))
            more.clicked.connect(lambda: owner.menu(entry, more))
            lay.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)


class WirelessOptions:
    """Wi-Fi 折りたたみの中身: 見つかった iPhone の一覧 / QR / 見つからないとき / IP 手入力。"""

    def __init__(self, page, section, ctrl):
        self.page, self.ctrl = page, ctrl
        self.discovered = list(getattr(ctrl, "demo_discovered", []) or [])
        self.inbox = SimpleQueue()
        self.addresses = local_addresses() if not ctrl.demo else []
        self.scanned = bool(self.discovered)
        self.started = False
        self.expander = None
        self._signature = None
        self._last_status = None
        self.timer = QTimer(page, interval=100)
        self.timer.timeout.connect(self._result)
        self.hint_timer = QTimer(page, interval=12000, singleShot=True)
        self.hint_timer.timeout.connect(self._show_hint)
        self.net_timer = QTimer(page, interval=3000)
        self.net_timer.timeout.connect(self._refresh_net)

        section.add(self._build_devices())
        section.add(self._build_qr())
        section.add(self._build_trouble())
        section.add(self._build_manual())
        section.add(self._build_method())
        group = getattr(ctrl, "camera_group", None)
        if group is not None:
            group.changed.connect(self._render)   # 「変更…」などで割り当てが変わったら、行の「カメラ N で使用中」を更新する
        self._render()

    # -- 部品 -------------------------------------------------------------------
    def _build_devices(self):
        box = FitBox()
        self.devices_box = box
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(16, 10, 12, 6)
        row.setSpacing(8)
        title = PlainLabel(t("wireless.found_title"))
        font = title.font()
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        row.addWidget(title)
        self.spinner = Spinner()
        row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.scanning_label = Hint(t("wireless.scanning"), wrap=False)
        self.scanning_label.hide()
        row.addWidget(self.scanning_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.scan = IconButton("refresh", t("wireless.rescan"))
        self.scan.clicked.connect(self.refresh)
        row.addWidget(self.scan)
        lay.addWidget(head)
        self.list = QVBoxLayout()
        self.list.setSpacing(0)
        lay.addLayout(self.list)
        return box

    def _build_qr(self):
        row = Row("qr", t("wireless.qr_title"), t("wireless.qr_hint"))
        self.qr = row.addControl(button(t("wireless.add"), primary=True))
        self.qr.setMinimumWidth(150)
        self.qr.clicked.connect(self._show_qr)
        row.setMinimumHeight(64)
        return row

    def _build_trouble(self):
        self.trouble = Disclosure(t("wireless.trouble"))
        self.trouble.toggled.connect(self._trouble_toggled)
        self.tether_block = self._block(t("wireless.tether_title"), t("wireless.tether_steps"))
        self.hotspot_block = self._block(t("wireless.hotspot_title"), t("wireless.hotspot_steps"))
        self.hotspot_button = button(t("wireless.open_hotspot"))
        self.hotspot_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("ms-settings:network-mobilehotspot")))
        self.hotspot_block[1].addWidget(self.hotspot_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.warning = Hint(t("wireless.timeout"))
        self.warning.hide()
        self.trouble.add(self.warning_box())
        self.trouble.add(self.tether_block[0])
        self.trouble.add(self.hotspot_block[0])
        return self.trouble

    def warning_box(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(44, 0, 16, 8)
        lay.addWidget(self.warning)
        return box

    def _block(self, title, steps):
        """(入れ物, 縦レイアウト, ✓ 表示, 手順文)。"""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(44, 6, 16, 10)
        lay.setSpacing(4)
        head = PlainLabel(title)
        font = head.font()
        font.setWeight(QFont.Weight.DemiBold)
        head.setFont(font)
        lay.addWidget(head)
        ok = StatusValue(wrap=False)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignLeft)
        text = Hint(steps)
        lay.addWidget(text)
        return box, lay, ok, text

    def _build_manual(self):
        self.manual = Disclosure(t("wireless.manual"))
        ip_row = Row("wifi", t("connection.wifi.ip"))
        ip_row.addControl(bound_line(self.ctrl, "ip", width=180))
        self.manual.add(ip_row)
        go_row = Row(None, t("wireless.manual_go"))
        go = go_row.addControl(button(t("wireless.connect")))
        go.clicked.connect(self._manual_connect)
        self.manual.add(go_row)
        return self.manual

    def _build_method(self):
        """つなぎ方の手動選択 (ふつうは自動)。"""
        self.method = Disclosure(t("wireless.method"))
        row = Row("link", t("wireless.method_label"), t("wireless.method_hint"))
        self.mode_group = QButtonGroup(self.page)
        self.mode_buttons = {}
        for value in ("auto", "usb", "wifi"):
            radio = QRadioButton(t("wireless.mode_" + value))
            self.mode_group.addButton(radio)
            self.mode_buttons[value] = radio
            row.addControl(radio)
            radio.clicked.connect(lambda _checked=False, v=value: self.ctrl.set_mode(v))
        self.method.add(row)
        self._show_mode(self.ctrl.get("mode"))
        self.ctrl.settings_changed.connect(lambda k, v: self._show_mode(v) if k == "mode" else None)
        return self.method

    def display_name(self, address, fallback):
        """つないでいる相手の表示名 (PC 側で付けた名前を優先)。分からなければ fallback。"""
        for entry in self.entries():
            if address and address in entry.device.addresses:
                return entry.name
        return fallback

    def _show_mode(self, value):
        radio = self.mode_buttons.get(value)
        if radio is not None and not radio.isChecked():
            radio.setChecked(True)

    def _manual_connect(self):
        """手で入れた IP につなぐ。その IP の iPhone をまだペアリングしていなければ、先に 6 桁の照合をする。"""
        if self.ctrl.wanted:
            self.ctrl.toggle_connect()
        address = (self.ctrl.get("ip") or "").strip()
        known = next((d for d in self._known() if d.paired and address in d.addresses), None)
        if known is None and address and not self.ctrl.demo:
            device = self._pair(address, address)
            if device is None:
                return
        self.ctrl.changed("target_device", "")
        self.ctrl.changed("usb_serial", "")
        self.ctrl.set_mode("wifi")
        self.ctrl.toggle_connect()

    # -- 外から ---------------------------------------------------------------------
    def bind_expander(self, expander):
        self.expander = expander
        self._signature = None
        self._render()

    def start(self):
        if self.started:
            return
        self.started = True
        if not self.ctrl.demo:
            self.refresh()

    def status(self, status):
        self._last_status = status
        pending = status.wanted and not status.connected and self.ctrl.get("mode") != "usb"
        if not pending:
            self.hint_timer.stop()
            self.warning.hide()
        elif not self.hint_timer.isActive() and self.warning.isHidden():
            self.hint_timer.start()
        self._render()

    def _show_hint(self):
        status = self.ctrl.status
        show = bool(status.get("wanted") and not status.get("connected") and self.ctrl.get("mode") != "usb")
        self.warning.setVisible(show)
        if show:
            self.trouble.set_open(True)

    # -- 検索 -----------------------------------------------------------------------
    def refresh(self):
        if self.timer.isActive():
            return
        self.addresses = local_addresses()
        addresses = self.addresses
        inbox = self.inbox

        def work():
            try:
                inbox.put(discover(addresses))
            except Exception:
                inbox.put(None)

        Thread(target=work, daemon=True).start()
        self.timer.start()
        self._render()

    def _result(self):
        try:
            found = self.inbox.get_nowait()
        except Empty:
            return
        self.timer.stop()
        self.scanned = True
        if found is None:
            self.ctrl.toast.emit(t("wireless.scan_error"), "error")
        else:
            self.discovered = found
        self._render()

    def _refresh_net(self):
        if not self.ctrl.demo:
            self.addresses = local_addresses()
        self._render_trouble()

    def _trouble_toggled(self, opened):
        if opened:
            self._refresh_net()
            self.net_timer.start()
        else:
            self.net_timer.stop()

    # -- 一覧の組み立て -------------------------------------------------------------
    def _known(self):
        return [device for entry in self.ctrl.get("known_devices", []) if (device := read_device(entry))]

    def entries(self):
        found_ips = {ip for device in self.discovered for ip in device.addresses}
        out = []
        for device in merge_devices(self._known(), self.discovered):
            live = [ip for ip in device.addresses if ip in found_ips]
            address = live[0] if live else (device.addresses[0] if device.addresses else "")
            out.append(Entry(device, address, route_of(address) if live else "registered", bool(live)))
        return sorted(out, key=lambda e: not e.found)

    def _camera(self, entry):
        group = getattr(self.ctrl, "camera_group", None)
        return group.for_device(entry.device.id, entry.device.addresses) if group else self.ctrl

    def usb_of(self, entry, seen=None):
        """この端末の USB の状態: READY (USB で見えていてアプリも応答) / CABLE (ケーブルだけ) / "" 。
        端末 ID が合うもの、次にそのカメラが覚えている USB の端末、最後に「登録も USB も 1 台だけ」で ID が読めない端末。"""
        seen = self.ctrl.usb_seen() if seen is None else seen
        if not seen:
            return ""
        known = self._known()
        camera = self._camera(entry)
        serial = ""
        if camera is not None and camera.get("target_device") in (entry.device.id, "", None):
            serial = camera.get("usb_serial") or ""
        sole = len(known) == 1 and known[0].id == entry.device.id
        return usb_state_of(seen, entry.device.id, serial, sole)

    def ready(self):
        """このカメラが今すぐつなげる相手 (core.ready.Ready)。USB で見えている端末が先、次に Wi-Fi で見つかった登録済みの端末。"""
        ctrl = self.ctrl
        group = getattr(ctrl, "camera_group", None)
        multi = bool(group and len(group.cameras) > 1)
        taken = {c.get("usb_serial") for c in group.cameras if c is not ctrl and c.get("usb_serial")} if group else set()
        target = ctrl.get("target_device") or ""
        pick = usb_pick(ctrl.usb_seen(), target, ctrl.get("usb_serial") or "", multi, taken)
        usb_state, usb_name = "", ""
        if pick:
            usb_state, _serial, ident = pick
            usb_name = next((clip_text(d.title) for d in self._known() if ident and d.id == ident), "") \
                or t("connection.device.default")
        wifi = next((e.name for e in self.entries()
                     if e.found and e.device.paired and (e.device.id == target if target else self._camera(e) in (None, ctrl))), "")
        return connect_ready(usb_state, usb_name, wifi, ctrl.get("mode"))

    def _state(self, entry):
        camera = self._camera(entry)
        if camera is None or not camera.wanted:
            return ""
        status = camera.status
        if status.get("transport") == "usb":
            if camera.get("target_device") != entry.device.id:
                return ""
        elif status.get("peer") not in entry.device.addresses and camera.get("target_device") != entry.device.id:
            return ""
        return "connected" if status.get("connected") and status.get("auth_state") != "checking" else "connecting"

    def _summary(self, entries):
        # 今選んでいるカメラ (このページの ctrl) 自身の接続の方式。上のカードと同じ status を見る
        status = self._last_status or self.ctrl.status
        if status and status.get("connected"):
            way = t("wireless.way." + ("wifi" if status.get("transport") == "wifi" else "usb"))
            for entry in entries:
                if self._camera(entry) is self.ctrl and self._state(entry) == "connected":
                    return t("wireless.summary.connected", name=entry.name, way=way)
            name = clip_text((status.get("phone") or {}).get("deviceName")) or t("connection.device.default")
            return t("wireless.summary.connected", name=name, way=way)
        count = sum(1 for entry in entries if entry.found)
        if count:
            return t("wireless.summary.found", n=count)
        if self.timer.isActive() or not self.scanned:
            return t("wireless.summary.searching")
        return t("wireless.summary.none")

    def _render(self):
        entries = self.entries()
        scanning = self.timer.isActive()
        states = [self._state(entry) for entry in entries]
        summary = self._summary(entries)
        group = getattr(self.ctrl, "camera_group", None)
        slots = tuple((camera.get("camera_slot"), camera.get("target_device"))
                      for camera in group.cameras) if group else ()
        seen = self.ctrl.usb_seen()
        usbs = tuple(self.usb_of(entry, seen) if not state else "" for entry, state in zip(entries, states))
        signature = (tuple(e.key() for e in entries), tuple(states), scanning, self.scanned, summary, slots, usbs)
        if signature == self._signature:
            return
        self._signature = signature
        if self.expander is not None:
            self.expander.set_summary(summary)
        self.scan.setEnabled(not scanning)
        self.scanning_label.setVisible(scanning)
        if scanning:
            self.spinner.start()
        else:
            self.spinner.stop()
        while self.list.count():
            item = self.list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)   # すぐレイアウトの計算から外す (deleteLater だけだと次のループまで残る)
                widget.deleteLater()
        if not entries:
            self.list.addWidget(Separator())
            self.list.addWidget(self._empty(scanning))
        for entry, state in zip(entries, states):
            self.list.addWidget(Separator())
            self.list.addWidget(DeviceRow(entry, state, self))
        self._render_trouble()
        refit(self.devices_box)   # 行の増減・空の状態への切り替えのたびに、折りたたみの高さを中身に合わせ直す
        notify = getattr(self.page, "refresh_connect", None)
        if callable(notify):
            notify()   # 見つかった端末が変わった: 接続ページの「接続」ボタンと状態カードを合わせ直す

    def _empty(self, scanning):
        """空の状態: 探し中はアイコン + 「探しています…」だけ。見つからなかったときは説明とボタンも出す。"""
        if scanning and not self.scanned:
            return EmptyState("phone", t("wireless.searching"))
        more = button(t("wireless.empty_more"))
        more.clicked.connect(lambda: self.trouble.set_open(True))
        return EmptyState("phone", t("wireless.empty_title"), t("wireless.empty_note"), more)

    def _render_trouble(self):
        for block, ok_text, on in ((self.tether_block, t("wireless.tether_ok"), on_tether(self.addresses)),
                                   (self.hotspot_block, t("wireless.hotspot_ok"), hotspot_on(self.addresses))):
            box, _lay, ok, steps = block
            ok.setVisible(on)
            if on:
                ok.set_status(ok_text, "ok")
            steps.setVisible(not on)
        self.hotspot_button.setVisible(not hotspot_on(self.addresses))
        refit(self.trouble)

    # -- 行の操作 -------------------------------------------------------------------
    def _pair(self, address, name):
        """コード方式のペアリング (iPhone に 6 桁が出る)。成功したら登録して Device を返す。やめた・失敗したら None。"""
        dialog = CodeDialog(self.page, name, None if self.ctrl.demo else address, self.ctrl.identity())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        peer = dialog.peer
        declined = dialog.declined
        dialog.deleteLater()
        if declined:
            self.ctrl.toast.emit(t("wireless.pair_declined"), "error")
        if not accepted or peer is None:
            return None
        device = Device(peer["id"], peer["name"], ipv4([address, *peer["addresses"]])[:32], "", peer["model"], "",
                        peer["s"].hex())
        return self._register(device)

    def _register(self, device):
        group = getattr(self.ctrl, "camera_group", None)
        root = group.primary if group else self.ctrl
        device = root.add_known_device(device)
        self._signature = None
        self._render()
        return device

    def connect_entry(self, entry):
        if entry.device.paired:
            self._start(entry.address, entry.device)
            return
        if not entry.address:
            self.ctrl.toast.emit(t("wireless.not_found"), "error")
            return
        device = self._pair(entry.address, entry.name)
        if device is not None:
            self._start(entry.address, device)

    def _start(self, address, device=None):
        group = getattr(self.ctrl, "camera_group", None)
        if group and device:
            camera = group.bind_device(self.ctrl, device, address)
            if camera is None:
                self.ctrl.toast.emit(t("multi.select_profile"), "error")
            else:
                camera.toggle_connect()
        else:
            self._apply(address)
            self.ctrl.toggle_connect()

    def disconnect(self, entry):
        camera = self._camera(entry)
        if camera and camera.wanted:
            camera.toggle_connect()

    def _apply(self, address):
        if self.ctrl.wanted:
            self.ctrl.toggle_connect()
        self.ctrl.set_ip(address)
        if self.ctrl.get("mode") == "usb":
            self.ctrl.set_mode("auto")   # USB だけにしていた人が Wi-Fi の iPhone を選んだとき。「Wi-Fiだけ」は選び直さない限り変えない

    def _save_known(self, devices):
        group = getattr(self.ctrl, "camera_group", None)
        root = group.primary if group else self.ctrl
        root.changed("known_devices", [item.stored() for item in devices[:64]])
        root.save_now()
        self._signature = None
        self._render()

    def menu(self, entry, anchor):
        menu = QMenu(self.page)
        menu.addAction(t("wireless.rename")).triggered.connect(lambda: self.rename(entry))
        menu.addAction(t("wireless.forget")).triggered.connect(lambda: self.forget(entry))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    def rename(self, entry):
        text, ok = QInputDialog.getText(self.page, t("wireless.rename"), t("wireless.rename_label"),
                                        QLineEdit.EchoMode.Normal, entry.device.title)
        if not ok:
            return
        alias = text.strip()[:128]
        if alias == entry.device.name:
            alias = ""   # 元の名前に戻した = PC 側の名前を使わない
        self._save_known([replace(item, alias=alias) if item.id == entry.device.id else item for item in self._known()])

    def forget(self, entry):
        box = QMessageBox(self.page)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(t("wireless.forget"))
        box.setText(t("wireless.forget_ask", name=entry.name))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        camera = self._camera(entry)
        if camera and camera.get("target_device") == entry.device.id:
            if camera.wanted:
                camera.toggle_connect()
            camera.changed("target_device", "")
            camera.set_ip("")
        self._save_known([item for item in self._known() if item.id != entry.device.id])

    def _show_qr(self):
        dialog = PairDialog(self.page, self.ctrl.identity())
        dialog.exec()
        device = dialog.device
        dialog.deleteLater()
        if not device:
            return
        device = self._register(device)
        self._start(device.addresses[0] if device.addresses else "", device)
