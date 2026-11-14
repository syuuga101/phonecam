# -*- coding: utf-8 -*-
"""接続ページ (Tk の ui/pages/connection_video.build_connection + ui/pages/preview.py の移植)

状態 (点 + 文言 + iPhone 名/つなぎ方の1行) → プレビュー → 接続/切断・開始/一時停止 → 数値の行
→ 接続方法 → Wi-Fi → iPhone のカメラの動作。Controller と qt.widgets だけを使う。
"""

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
        QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import time

from phonecam.core.profiling import PROFILE
from phonecam.core.deps import np
from phonecam.core.softcam_shmem import IDLE_STOP_LABELS as _IDLE_STOP_LABELS, RUN_MODE_LABELS
from phonecam.i18n import t
from phonecam.qt import icons
from phonecam.qt.wireless import WirelessOptions
from phonecam.qt.widgets import (
    Card,
    ErrorLabel,
    Hint,
    Page,
    Row,
    StatusValue,
    bound_combo,
    border_color,
    button,
    card_color,
    ok_color,
    ref_palette,
    secondary_color,
)

RATIO_MIN = 1.0 / 3.0
RATIO_MAX = 1.0
RATIO_DEFAULT = 2.0 / 3.0
TICK_MS = 66          # 既定。実際の間隔は設定 preview_fps (15/30/60) から決める
MIN_W = 240           # プレビュー幅の下限 (論理px)
MAX_H_FRAC = 0.45     # ウィンドウの高さに対する上限
GRIP = 18             # つまみの当たり判定 (論理px)


def clamp_ratio(v):
    try:
        v = float(v)
    except Exception:
        v = RATIO_DEFAULT
    return max(RATIO_MIN, min(RATIO_MAX, v))


def frame_to_image(img, fmt, max_w, max_h):
    """numpy フレーム (H, W, 3|4|1) を max_w x max_h に収まる程度まで間引いて QImage にする (コピー済み)。"""
    if np is None:
        return None
    h, w = img.shape[:2]
    if h < 2 or w < 2:
        return None
    step = max(1, min(w // max(1, int(max_w)), h // max(1, int(max_h))))
    small = img[::step, ::step]
    if small.ndim == 2:
        small = np.stack([small] * 3, axis=2)
    elif small.shape[2] == 1:
        small = np.repeat(small, 3, axis=2)
    elif small.shape[2] > 3:
        small = small[:, :, :3]
    small = np.ascontiguousarray(small)
    sh, sw = small.shape[:2]
    qfmt = QImage.Format.Format_BGR888 if fmt == "bgr24" else QImage.Format.Format_RGB888
    # small のメモリをそのまま参照する QImage (tobytes と copy の2回のコピーを省く)。
    # 呼び出し側は、これを scaled() / copy() で別の画像にするまで _keep を手放さないこと (qi._keep が持つ)。
    qi = QImage(small.data, sw, sh, small.strides[0], qfmt)
    qi._keep = small
    return qi


class PreviewView(QWidget):
    """ライブプレビュー。内容列の幅 x ratio (既定 2/3) の 16:9 を左上に描き、右下のつまみで拡縮する。
    ratio は設定 preview_width_ratio に保存 (つまみを離したとき)。"""

    rect_width_changed = Signal(int)   # 描く枠の幅 (論理px)。直下のボタンの行をこの幅に合わせる

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.ratio = clamp_ratio(ctrl.get("preview_width_ratio", RATIO_DEFAULT))
        self._pix = None
        self._seq = -1
        self._drag = False
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(int(MIN_W * 9 / 16))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._apply_fps(ctrl.get("preview_fps", 30))
        self._timer.timeout.connect(self._tick)
        ctrl.settings_changed.connect(self._setting)

    # 別ページを開いている間は止める (表示中にだけ 15〜60 回/秒で起きる)
    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # -- 大きさ --------------------------------------------------------------
    def _rect_size(self):
        """(幅, 高さ) 論理px: 内容列の幅 x ratio。高さはウィンドウの45%以内、幅は下限 240px。"""
        cw = max(160, self.width())
        mn = min(MIN_W, cw)
        w = max(mn, min(cw, int(cw * self.ratio)))
        h = int(w * 9 / 16)
        win = self.window()
        wh = win.height() if win is not None else 0
        if wh > 50:
            hmax = int(wh * MAX_H_FRAC)
            if h > hmax:
                h = hmax
                w = int(h * 16 / 9)
        if w < mn:
            w = mn
            h = int(w * 9 / 16)
        return w, h

    def _relayout(self):
        w, h = self._rect_size()
        if h != self.height():
            self.setFixedHeight(h)
        self._seq = -1   # 次の tick で今の大きさに合わせて描き直す
        self.rect_width_changed.emit(w)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _apply_fps(self, fps):
        try:
            fps = int(fps)
        except Exception:
            fps = 30
        if fps not in (15, 30, 60):
            fps = 30
        self._timer.setInterval(max(1, int(round(1000.0 / fps))))   # 15->67ms 30->33ms 60->17ms (UI 用 50ms tick とは別)

    def _setting(self, key, value):
        if key == "preview_fps":
            self._apply_fps(value)
            return
        if key == "preview_width_ratio" and not self._drag:
            self.ratio = clamp_ratio(value)
            self._relayout()

    # -- 描画 ----------------------------------------------------------------
    def _grip_rect(self):
        w, h = self._rect_size()
        return QRect(w - GRIP, h - GRIP, GRIP, GRIP)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self.palette()
        w, h = self._rect_size()
        r = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(border_color(pal), 1))
        p.setBrush(card_color(pal))
        p.drawRoundedRect(r, 5, 5)
        if self._pix is not None:
            lw = self._pix.width() / self._pix.devicePixelRatio()
            lh = self._pix.height() / self._pix.devicePixelRatio()
            p.drawPixmap(int((w - lw) / 2), int((h - lh) / 2), self._pix)
        else:
            p.setPen(secondary_color(pal))
            p.drawText(QRect(0, 0, w, h), int(Qt.AlignmentFlag.AlignCenter), t("connection.preview.empty"))
        # つまみ: 斜線3本
        g = 14
        col = QColor(secondary_color(pal))
        p.setPen(QPen(col, 1.2))
        for k in (1, 2, 3):
            d = k * g / 3.0
            p.drawLine(QPointF(w - 3, h - d - 1), QPointF(w - d - 1, h - 3))
        p.end()

    # -- 映像 ----------------------------------------------------------------
    def _tick(self):
        if not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return   # 最小化中は見えないので変換しない
        w0, h0 = self._rect_size()
        if h0 != self.height():
            self._relayout()
        item = self.ctrl.preview_frame()
        if item is None:
            if self._pix is not None:
                self._pix = None
                self.update()
            self._seq = -1
            return
        img, fmt, seq = item
        if seq == self._seq:
            return
        self._seq = seq
        w, h = self._rect_size()
        dpr = self.devicePixelRatioF()
        pw, ph = max(2, int((w - 2) * dpr)), max(2, int((h - 2) * dpr))
        if PROFILE is not None:
            started = time.perf_counter()
        qi = frame_to_image(img, fmt, pw, ph)
        if qi is None:
            return
        if qi.width() <= pw and qi.height() <= ph:
            qi = qi.copy()   # 拡大はしない。参照している numpy から切り離す
        else:
            qi = qi.scaled(QSize(pw, ph), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(qi)
        pm.setDevicePixelRatio(dpr)
        self._pix = pm
        if PROFILE is not None:
            PROFILE.add(self.ctrl.link.vcam.slot, "preview", (time.perf_counter() - started) * 1000)
        self.update()

    # -- ドラッグで拡縮 -----------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._grip_rect().contains(e.position().toPoint()):
            self._drag = True
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag:
            self.ratio = clamp_ratio(e.position().x() / float(max(1, self.width())))
            self._relayout()
            e.accept()
            return
        if self._grip_rect().contains(e.position().toPoint()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.ctrl.changed("preview_width_ratio", round(self.ratio, 4), None)
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class Dot(QWidget):
    """状態の点 (10px)。接続中 (配信中/待機中) は緑、それ以外は二次色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self.key = "off"

    def set_key(self, key):
        if key != self.key:
            self.key = key
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self.palette()
        col = ok_color(pal) if self.key in ("live", "idle") else secondary_color(pal)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        p.end()


class ConnectionPage(Page):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._build_header()
        self._build_camera_card()
        self._build_preview()
        self._build_numbers()
        self._build_wifi()
        self._build_run()
        self._build_this_camera()
        self.finish()
        ctrl.status_changed.connect(self.update_status)
        self.update_status(ctrl.status)

    # -- 上部: 状態のカード (1枚) -----------------------------------------------------
    def _build_header(self):
        card = Card()
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = Dot()
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.state_label = QLabel(t("connection.card.off"))
        f = self.state_label.font()
        f.setPointSizeF(14.0)
        f.setWeight(QFont.Weight.DemiBold)
        self.state_label.setFont(f)
        self.state_label.setWordWrap(False)
        top.addWidget(self.state_label, 0)
        self.check = QLabel()
        self.check.setFixedSize(18, 18)
        self.check.hide()
        top.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        left.addLayout(top)
        self.sub_label = Hint("", wrap=True)
        left.addWidget(self.sub_label)
        self.error_label = ErrorLabel("")
        left.addWidget(self.error_label)
        lay.addLayout(left, 1)
        card.layout().addWidget(box)
        self.add(card)

    # -- プレビューと、その直下の操作ボタン (つなぐ/切断 + 一時停止/開始) -------------------------
    def _build_preview(self):
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        self.preview = PreviewView(self.ctrl)
        col.addWidget(self.preview)
        self.btn_row = QWidget()
        self.btn_row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self.btn_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.start_btn = button(t("connection.button.start"), primary=True)
        self.start_btn.clicked.connect(self.ctrl.start_pause)
        self.connect_btn = button(t("connection.button.connect"), primary=True)
        self.connect_btn.setMinimumWidth(104)
        self.connect_btn.clicked.connect(lambda: self.ctrl.toggle_connect())
        row.addWidget(self.connect_btn)
        row.addWidget(self.start_btn)
        row.addStretch(1)
        col.addWidget(self.btn_row, 0, Qt.AlignmentFlag.AlignLeft)
        self.preview.rect_width_changed.connect(self._fit_btn_row)
        self._fit_btn_row(self.preview._rect_size()[0])
        self.add(box)

    def _fit_btn_row(self, w):
        self.btn_row.setFixedWidth(max(w, self.btn_row.minimumSizeHint().width()))

    def _card_text(self, s):
        """上のカードの (見出し, 補足, ✓ を出すか)。方式を選ばせない (auto) ときは 3 つの状態だけを出す。"""
        auto = self.ctrl.get("mode") == "auto"
        way = t("wireless.way.wifi" if s.get("transport") == "wifi" else "wireless.way.usb")
        if s.connected:
            name = (s.get("phone") or {}).get("deviceName") or (s.get("phone") or {}).get("name") \
                or t("connection.device.default")
            return t("connection.card.connected", name=self.wireless.display_name(s.get("peer"), name), way=way), \
                s.sub, True
        if not auto:
            return s.state_text, s.sub, False
        if not s.wanted:
            return t("connection.card.off"), t("connection.card.off_hint"), False
        if s.error_text and s.get("auth_state") not in ("failed", "refused"):
            return t("connection.card.none"), t("connection.card.none_hint"), False
        return t("connection.card.searching"), "", False

    # -- 数値の行 -------------------------------------------------------------------
    def _build_numbers(self):
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        self.num = {}
        for i, key in enumerate(("res", "latency", "thermal", "battery")):
            grid.setColumnStretch(i, 1)
            cap = Hint(t("connection.num.%s" % key), wrap=False)
            val = QLabel("-")
            f = val.font()
            f.setPointSizeF(11.0)
            val.setFont(f)
            grid.addWidget(cap, 0, i)
            grid.addWidget(val, 1, i)
            self.num[key] = val
        self.add(box)

    # -- Wi-Fi ----------------------------------------------------------------------
    def _build_camera_card(self):
        """上のカードの下の 1 枚: 「OBS での名前: PhoneCam 2 ✓ 準備済み」(未登録なら「準備する」) と、
        このカメラの iPhone (2 台以上のとき)。割り当てを変えるのは「変更…」だけ。"""
        group = getattr(self.ctrl, "camera_group", None)
        self.camera_group = group
        if group is None:
            return
        card = Card()
        obs = Row("camera", t("multi.obs_name"))
        self.obs_label = QLabel()
        f = self.obs_label.font()
        f.setWeight(QFont.Weight.DemiBold)
        self.obs_label.setFont(f)
        obs.addControl(self.obs_label)
        self.vcam_status = StatusValue(wrap=False)
        obs.addControl(self.vcam_status)
        self.vcam_button = obs.addControl(button(t("multi.vcam.prepare")))
        self.vcam_button.clicked.connect(lambda: self.ctrl.change_camera_registration("register"))
        card.layout().addWidget(obs)
        self.phone_row = Row("phone", t("multi.this_phone"))
        self.phone_label = QLabel()
        self.phone_row.addControl(self.phone_label)
        change = self.phone_row.addControl(button(t("multi.change")))
        change.clicked.connect(self._change_phone)
        card.layout().addWidget(self.phone_row)
        self.camera_card = card
        self.add(card)
        self.ctrl.camera_registration_changed.connect(self._refresh_camera_card)
        self.ctrl.settings_changed.connect(lambda key, value: self._refresh_camera_card()
                                           if key in ("camera_slot", "target_device", "usb_serial", "known_devices") else None)
        group.changed.connect(self._refresh_camera_card)
        self._refresh_camera_card()

    def _refresh_camera_card(self):
        group = self.camera_group
        multi = len(group.cameras) > 1
        # カメラが 1 台で、出力が「カメラ 1」のときは、これまでどおり何も出さない
        self.camera_card.setVisible(multi or group.slot_of(self.ctrl) != 1)
        self.phone_row.setVisible(multi)
        self.obs_label.setText(group.obs_name(self.ctrl))
        state = self.ctrl.camera_registration
        busy = self.ctrl.camera_registration_busy
        if state == "ok":
            self.vcam_status.set_status(t("multi.vcam.ready"), "ok")
        elif not self.ctrl.frozen:
            self.vcam_status.set_status(t("video.vcam.src_only"))
        elif busy:
            self.vcam_status.set_status(t("multi.vcam.busy"))
        else:
            self.vcam_status.set_status(t("multi.vcam.outdated" if state == "outdated" else "multi.vcam.missing"), "warning")
        tip = self._vcam_tooltip()
        for widget in (self.obs_label, self.vcam_status, self.vcam_button):
            widget.setToolTip(tip)
        self.vcam_button.setVisible(self.ctrl.frozen and state in ("missing", "outdated"))
        self.vcam_button.setEnabled(not busy)
        self.phone_label.setText(group.phone_name(self.ctrl))

    def _vcam_tooltip(self):
        """OBS に複数のカメラが出ないときに自分で確かめるための情報: カメラごとの登録状態と、レジストリ上の名前 (FriendlyName)。"""
        if not self.ctrl.frozen:
            return ""
        try:
            from phonecam.core.softcam_reg import CAMERA_SLOTS, camera_display_name, slot_report
            words = {"ok": t("multi.vcam.ready"), "missing": t("multi.vcam.missing"),
                     "outdated": t("multi.vcam.outdated"), "unavailable": t("multi.vcam.tip.unavailable")}
            none = t("multi.vcam.tip.none")
            lines = [t("multi.vcam.tip.title")]
            mine = self.camera_group.slot_of(self.ctrl)
            current = None
            for slot in CAMERA_SLOTS:
                report = slot_report(slot)
                if slot == mine:
                    current = report
                lines.append(t("multi.vcam.tip.camera", n=slot, obs=camera_display_name(slot), state=words.get(report["status"], report["status"]),
                               name=report["name"][0] or none))
            if current is not None:
                lines.append(t("multi.vcam.tip.clsid", clsid=current["clsid"]))
                lines.append(t("multi.vcam.tip.dll", dll64=current["dll"][0] or none, dll32=current["dll"][32] or none))
            lines.append(t("multi.vcam.tip.hint"))
            return "\n".join(lines)
        except Exception:
            return ""

    def _change_phone(self):
        from phonecam.qt.multicam import DevicePickDialog
        dialog = DevicePickDialog(self.window(), self.camera_group, self.ctrl)
        dialog.exec()
        dialog.deleteLater()
        self._refresh_camera_card()

    def _build_this_camera(self):
        """「このカメラ」の折りたたみ (一番下)。2 台目以降は「カメラを削除」、1 台だけのときは「カメラを追加」の入口。"""
        group = self.camera_group
        if group is None:
            return
        section = self.add_section(t("multi.manage"))
        label = section.add_row("camera", t("multi.this_camera"))
        self.manage_label = QLabel()
        label.addControl(self.manage_label)
        self.add_row_ = section.add_row("camera", t("multi.add"), t("multi.add_hint"))
        add = self.add_row_.addControl(button(t("multi.add_button")))
        add.clicked.connect(self._open_wizard)
        self.remove_row = section.add_row("camera", t("multi.remove.row"), t("multi.remove.hint"))
        remove = self.remove_row.addControl(button(t("multi.remove.button")))
        remove.clicked.connect(self._remove_camera)

        def refresh():
            multi = len(group.cameras) > 1
            self.manage_label.setText(group.label(self.ctrl))
            self.add_row_.setVisible(not multi and group.can_add)
            self.remove_row.setVisible(self.ctrl is not group.primary)

        group.changed.connect(refresh)
        refresh()

    def _open_wizard(self):
        fn = getattr(self.window(), "add_camera", None)
        if callable(fn):
            fn()

    def _remove_camera(self):
        from phonecam.qt.multicam import confirm_remove
        confirm_remove(self.window(), self.camera_group, self.ctrl)

    def _build_wifi(self):
        sec = self.add_section(t("connection.wifi.title"))
        self.wireless = WirelessOptions(self, sec, self.ctrl)
        self.wifi_warning = self.wireless.warning

    def on_activated(self):
        # つないでいる間は Wi-Fi の検索 (Bonjour / 走査) を始めない。一覧の「再検索」ボタンを押したときだけ動かす
        if not self.ctrl.status.get("connected"):
            self.wireless.start()

    # -- iPhone のカメラの動作 -----------------------------------------------------------
    def _build_run(self):
        sec = self.add_section(t("connection.run.title"))
        row = sec.add_row("play", t("connection.run.mode"), t("connection.run.mode_hint"))
        row.addControl(bound_combo(self.ctrl, "run_mode", RUN_MODE_LABELS, setter=self.ctrl.set_run_mode, width=200))
        row = sec.add_row("clock", t("connection.run.idle"), t("connection.run.idle_hint"))
        IDLE_LABELS = [(v, t("labels.sec.%d" % v)) for v, _ in _IDLE_STOP_LABELS]
        self.idle_combo = bound_combo(self.ctrl, "idle_stop_sec", IDLE_LABELS, width=110)
        row.addControl(self.idle_combo)
        self.camera_row = sec.add_row("camera", t("connection.run.camera"))
        self.camera_value = QLabel("-")
        self.camera_row.addControl(self.camera_value)

    # -- status の反映 --------------------------------------------------------------------
    @staticmethod
    def _set_primary(b, on):
        if b.isDefault() != bool(on):
            b.setDefault(bool(on))
            b.setAutoDefault(bool(on))
            b.style().unpolish(b)
            b.style().polish(b)
            b.update()

    def update_status(self, s):
        if not s:
            return
        self.wireless.status(s)
        headline, sub, checked = self._card_text(s)
        self.dot.set_key(s.key)
        self.state_label.setText(headline)
        self.check.setVisible(checked)
        if checked:
            self.check.setPixmap(icons.pixmap("check", 18, ok_color(ref_palette(self)), self.devicePixelRatioF()))
        self.sub_label.setText(sub)
        self.sub_label.setVisible(bool(sub))
        # 自動のときの「見つかりません」は上の見出しが言うので、赤字は認証の失敗などだけ出す
        hide_error = self.ctrl.get("mode") == "auto" and not s.connected and s.get("auth_state") not in ("failed", "refused")
        self.error_label.setText("" if hide_error else s.error_text)
        self.start_btn.setText(t("connection.button.pause") if s.start_is_pause else t("connection.button.start"))
        # 主ボタン (accent) は「つなぐ」「開始」。切断・一時停止は通常色
        self._set_primary(self.start_btn, not s.start_is_pause)
        self._set_primary(self.connect_btn, not s.wanted)
        self.start_btn.setEnabled(bool(s.start_enabled))
        self.start_btn.setVisible(bool(s.start_enabled))   # つないでいる間だけ (主ボタンは「つなぐ / 切断」の1つ)
        self.connect_btn.setText(t("connection.button.disconnect") if s.wanted else t("connection.button.connect"))
        self.num["res"].setText(s.res_fps)
        self.num["latency"].setText(s.latency_text)
        self.num["thermal"].setText(s.thermal_text)
        self.num["battery"].setText(s.battery_text)
        self.idle_combo.setEnabled(bool(s.idle_enabled))
        self.camera_value.setText(s.camera_text)
        self.camera_row.setHint(s.run_note)


def build(ctrl):
    return ConnectionPage(ctrl)
