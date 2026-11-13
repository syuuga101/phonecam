# -*- coding: utf-8 -*-
"""接続ページ (Tk の ui/pages/connection_video.build_connection + ui/pages/preview.py の移植)

状態 (点 + 文言 + iPhone 名/つなぎ方) と 開始/一時停止・接続/切断 → プレビュー → 数値の行
→ 接続方法 → Wi-Fi → iPhone のカメラの動作。Controller と qt.widgets だけを使う。
"""

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from phonecam.core.deps import np
from phonecam.core.softcam_shmem import IDLE_STOP_LABELS, RUN_MODE_LABELS
from phonecam.i18n import t
from phonecam.qt.widgets import (
    ErrorLabel,
    Hint,
    Page,
    StatusValue,
    bound_combo,
    bound_line,
    border_color,
    button,
    card_color,
    ok_color,
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
    buf = small.tobytes()
    qi = QImage(buf, sw, sh, small.strides[0], qfmt)
    return qi.copy()   # buf から切り離す (buf はこの関数を出ると消える)


class PreviewView(QWidget):
    """ライブプレビュー。内容列の幅 x ratio (既定 2/3) の 16:9 を左上に描き、右下のつまみで拡縮する。
    ratio は設定 preview_width_ratio に保存 (つまみを離したとき)。"""

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
        self._timer.start()
        ctrl.settings_changed.connect(self._setting)

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
        qi = frame_to_image(img, fmt, pw, ph)
        if qi is None:
            return
        qi = qi.scaled(QSize(pw, ph), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(qi)
        pm.setDevicePixelRatio(dpr)
        self._pix = pm
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
        self.preview = self.add(PreviewView(ctrl))
        self.connection_status = StatusValue()
        self.column.addWidget(self.connection_status, 0, Qt.AlignmentFlag.AlignLeft)
        self._build_numbers()
        self._build_method()
        self._build_wifi()
        self._build_run()
        self.finish()
        ctrl.status_changed.connect(self.update_status)
        self.update_status(ctrl.status)

    # -- 上部: 状態 + ボタン -----------------------------------------------------
    def _build_header(self):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = Dot()
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.state_label = QLabel(t("connection.state.off"))
        f = self.state_label.font()
        f.setPointSizeF(14.0)
        f.setWeight(QFont.Weight.DemiBold)
        self.state_label.setFont(f)
        top.addWidget(self.state_label)
        self.device_label = Hint("", wrap=False)
        top.addWidget(self.device_label)
        top.addStretch(1)
        left.addLayout(top)
        self.sub_label = Hint("", wrap=True)
        self.sub_label.setVisible(False)
        left.addWidget(self.sub_label)
        self.error_label = ErrorLabel("")
        left.addWidget(self.error_label)
        lay.addLayout(left, 1)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.start_btn = button(t("connection.button.start"), primary=True)
        self.start_btn.clicked.connect(self.ctrl.start_pause)
        self.connect_btn = button(t("connection.button.connect"))
        self.connect_btn.clicked.connect(lambda: self.ctrl.toggle_connect())
        btns.addWidget(self.start_btn)
        btns.addWidget(self.connect_btn)
        lay.addLayout(btns, 0)
        self.add(box)

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

    # -- 接続方法 -------------------------------------------------------------------
    def _build_method(self):
        sec = self.add_section(t("connection.method.title"))
        row = sec.add_row("link", t("connection.method.label"))
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for value, text in (("usb", "USB"), ("wifi", "Wi-Fi")):
            rb = QRadioButton(text)
            self.mode_group.addButton(rb)
            self.mode_buttons[value] = rb
            row.addControl(rb)
            rb.clicked.connect(lambda _checked=False, v=value: self.ctrl.set_mode(v))
        self._show_mode(self.ctrl.get("mode"))
        self.ctrl.settings_changed.connect(lambda k, v: self._show_mode(v) if k == "mode" else None)

    def _show_mode(self, value):
        rb = self.mode_buttons.get(value)
        if rb is not None and not rb.isChecked():
            rb.setChecked(True)

    # -- Wi-Fi ----------------------------------------------------------------------
    def _build_wifi(self):
        sec = self.add_section(t("connection.wifi.title"))
        row = sec.add_row("wifi", t("connection.wifi.ip"))
        row.addControl(bound_line(self.ctrl, "ip", width=180))

        row = sec.add_row("link", t("connection.wifi.code"), t("connection.wifi.code_hint"))
        self.code_edit = QLineEdit(self.ctrl.get("pair_code", "") or "")
        self.code_edit.setMaxLength(6)
        self.code_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_edit.setMinimumWidth(120)
        f = self.code_edit.font()
        f.setPointSizeF(14.0)
        f.setWeight(QFont.Weight.DemiBold)
        self.code_edit.setFont(f)
        self.code_edit.textEdited.connect(self._code_edited)
        row.addControl(self.code_edit)
        self.ctrl.settings_changed.connect(self._code_setting)

    def _code_edited(self, text):
        code = self.ctrl.set_pair_code(text)   # 数字だけ6桁にそろえる
        if code != text:
            self.code_edit.setText(code)

    def _code_setting(self, key, value):
        if key == "pair_code" and self.code_edit.text() != str(value):
            self.code_edit.setText(str(value))

    # -- iPhone のカメラの動作 -----------------------------------------------------------
    def _build_run(self):
        sec = self.add_section(t("connection.run.title"))
        row = sec.add_row("play", t("connection.run.mode"), t("connection.run.mode_hint"))
        row.addControl(bound_combo(self.ctrl, "run_mode", RUN_MODE_LABELS, setter=self.ctrl.set_run_mode, width=200))
        row = sec.add_row("clock", t("connection.run.idle"))
        self.idle_combo = bound_combo(self.ctrl, "idle_stop_sec", IDLE_STOP_LABELS, width=100)
        row.addControl(self.idle_combo)
        self.camera_row = sec.add_row("camera", t("connection.run.camera"))
        self.camera_value = QLabel("-")
        self.camera_row.addControl(self.camera_value)

    # -- status の反映 --------------------------------------------------------------------
    def update_status(self, s):
        if not s:
            return
        level = "error" if s.error_text else "ok" if s.connected else "warning" if s.wanted else None
        self.connection_status.set_status(t("compact.connected") if s.connected else s.state_text, level)
        self.dot.set_key(s.key)
        self.state_label.setText(s.state_text)
        self.device_label.setText(s.device)
        self.sub_label.setText(s.sub)
        self.sub_label.setVisible(bool(s.sub))
        self.error_label.setText(s.error_text)
        self.start_btn.setText(t("connection.button.pause") if s.start_is_pause else t("connection.button.start"))
        self.start_btn.setEnabled(bool(s.start_enabled))
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
