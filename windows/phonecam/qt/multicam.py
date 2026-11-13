# -*- coding: utf-8 -*-
"""複数カメラの UI 部品。

考え方は 1 つ: 「カメラ」= OBS に出る仮想カメラ (PhoneCam カメラ N)。それぞれのカメラに iPhone を 1 台割り当てる。
割り当て (カメラ ↔ iPhone) は core/cameras.py の CameraGroup.assign() だけが書き換える。

  Segmented        ページ見出しの右の「カメラ 1 | カメラ 2」切り替え
  ElidedLabel      ナビの「カメラ 2 · iPhone 13」(長いときは … で省く)
  CameraTile       「カメラ」ページの 1 台ぶん (プレビュー + 状態の帯 + つなぐ/切断)
  AddTile          「＋ カメラを追加」(点線の枠)
  DevicePicker     iPhone を選ぶ一覧 (ウィザードのステップ 1 と、接続ページの「変更…」で共用)
  DevicePickDialog 「変更…」から開く選択ダイアログ
  AddCameraWizard  「カメラを追加」 3 ステップ
  confirm_remove   「カメラを削除」の確認
"""

from dataclasses import replace
from queue import Empty, SimpleQueue
from threading import Thread

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from phonecam.core.usbmux import usbmux_devices
from phonecam.core.wireless import discover, merge_devices
from phonecam.i18n import t
from phonecam.qt import icons
from phonecam.qt.widgets import (
    _THEME_EVENTS,
    Card,
    FixedHint,
    Hint,
    IconLabel,
    Page,
    Row,
    blend,
    border_color,
    button,
    card_color,
    error_color,
    is_dark,
    ok_color,
    ref_palette,
    secondary_color,
)

TILE_FPS_MAX = 30          # グリッドのプレビューは重くしないため最大 30fps
BAND_H = 36                # タイル下の帯の高さ (論理px)
TILE_RADIUS = 6


def _tile_height(width):
    return max(90, int(width * 9 / 16))


# ---------------------------------------------------------------------------
# 小さな部品
# ---------------------------------------------------------------------------

class ElidedLabel(QLabel):
    """1 行に収まらないときは末尾を … にする二次色のラベル (全文はツールチップ)。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        f = self.font()
        f.setPointSizeF(9.0)
        self.setFont(f)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self.update()

    def text(self):
        return self._full

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height() + 2)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full), self.fontMetrics().height() + 2)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(secondary_color(ref_palette(self)))
        p.setFont(self.font())
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided)
        p.end()


class Segmented(QWidget):
    """「カメラ 1 | カメラ 2」の小さな切り替え (Windows 11 のセグメント風: 選ばれた側だけ面が浮く)。"""

    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._current = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(t("multi.switch"))

    def set_items(self, labels, current=0):
        self._items = list(labels)
        self._current = max(0, min(len(self._items) - 1, int(current))) if self._items else 0
        self.updateGeometry()
        self.update()

    def set_current(self, index):
        if index != self._current and 0 <= index < len(self._items):
            self._current = index
            self.update()

    def current(self):
        return self._current

    def _seg_w(self):
        fm = self.fontMetrics()
        return max([fm.horizontalAdvance(text) for text in self._items] or [0]) + 28

    def sizeHint(self):
        return QSize(self._seg_w() * len(self._items) + 4, 30)

    def minimumSizeHint(self):
        return self.sizeHint()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        win, txt = pal.window().color(), pal.windowText().color()
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border_color(pal), 1))
        p.setBrush(blend(win, txt, 0.05))
        p.drawRoundedRect(outer, 5, 5)
        w = self._seg_w()
        for i, text in enumerate(self._items):
            seg = QRectF(2 + i * w, 2, w, self.height() - 4)
            selected = i == self._current
            if selected:
                p.setPen(QPen(border_color(pal), 1))
                p.setBrush(card_color(pal))
                p.drawRoundedRect(seg.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            font = QFont(self.font())
            font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
            p.setFont(font)
            p.setPen(txt if selected else secondary_color(pal))
            p.drawText(seg, int(Qt.AlignmentFlag.AlignCenter), text)
        if self.hasFocus():
            p.setPen(QPen(pal.highlight().color(), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(outer.adjusted(-0.5, -0.5, 0.5, 0.5), 5, 5)
        p.end()

    def _pick(self, index):
        if 0 <= index < len(self._items) and index != self._current:
            self._current = index
            self.update()
            self.activated.emit(index)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pick(int((e.position().x() - 2) // max(1, self._seg_w())))
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._pick(self._current - 1)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._pick(self._current + 1)
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------------------
# 「カメラ」ページ: プレビューのグリッド
# ---------------------------------------------------------------------------

def _dot_color(state, pal):
    if state == "live":
        return ok_color(pal)
    if state == "wait":
        return QColor("#f5b400") if is_dark(pal) else QColor("#d99a00")
    return QColor(255, 255, 255, 120)


class CameraTile(QWidget):
    """カメラ 1 台ぶんのタイル。16:9 のプレビュー + 左上のバッジ + 下の帯 (点 / iPhone 名 / USB・Wi-Fi · fps) + つなぐ/切断。
    タイル全体のクリックで、そのカメラを選んで接続ページへ移る。"""

    chosen = Signal(object)

    def __init__(self, group, camera, parent=None):
        super().__init__(parent)
        self.group, self.camera = group, camera
        self._pix = None
        self._seq = -1
        self._hover = False
        self._press = False
        self._state = ("off", "", "", False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(200)
        self.setFixedHeight(_tile_height(300))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn = QPushButton(self)
        self.btn.setMinimumHeight(24)
        self.btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn.setCursor(Qt.CursorShape.ArrowCursor)
        self.btn.clicked.connect(self._toggle)
        self.refresh()

    # -- 状態 ----------------------------------------------------------------
    def _toggle(self):
        self.camera.toggle_connect()
        self.refresh()

    def refresh(self):
        s = self.camera.status
        connected, wanted = bool(s.get("connected")), bool(s.get("wanted"))
        name = self.group.phone_name(self.camera)
        if connected:
            way = t("wireless.way.wifi" if s.get("transport") == "wifi" else "wireless.way.usb")
            sub = t("multi.tile.sub", way=way, fps=int(round(s.get("recv_fps") or 0)))
            dot = "live"
        elif wanted:
            sub, dot = t("multi.tile.waiting"), "wait"
        else:
            sub, dot = t("multi.tile.off"), "off"
        state = (dot, name, sub, wanted)
        if state == self._state and self.btn.text():
            return
        self._state = state
        self.btn.setText(t("multi.tile.disconnect") if wanted else t("multi.tile.connect"))
        self.btn.setToolTip(t("multi.tile.disconnect_tip" if wanted else "multi.tile.connect_tip",
                              camera=self.group.label(self.camera)))
        self.btn.setAccessibleName(self.btn.toolTip())
        self.setAccessibleName(f"{self.group.label(self.camera)}, {name}, {sub}")
        self._place()
        self.update()

    def update_preview(self):
        """見えているときだけ呼ぶ。最新フレームを今のタイルの大きさに縮めて持つ。"""
        from phonecam.qt.pages.connection import frame_to_image
        item = self.camera.preview_frame()
        if item is None:
            if self._pix is not None:
                self._pix = None
                self._seq = -1
                self.update()
            return
        img, fmt, seq = item
        if seq == self._seq:
            return
        self._seq = seq
        dpr = self.devicePixelRatioF()
        pw, ph = max(2, int(self.width() * dpr)), max(2, int(self.height() * dpr))
        qi = frame_to_image(img, fmt, pw, ph)
        if qi is None:
            return
        if qi.width() > pw or qi.height() > ph:
            qi = qi.scaled(QSize(pw, ph), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        else:
            qi = qi.copy()   # 参照している numpy から切り離す
        pm = QPixmap.fromImage(qi)
        pm.setDevicePixelRatio(dpr)
        self._pix = pm
        self.update()

    # -- 配置 ----------------------------------------------------------------
    def _place(self):
        self.btn.adjustSize()
        w = max(60, self.btn.sizeHint().width())
        self.btn.setFixedSize(w, 24)
        self.btn.move(self.width() - 10 - w, self.height() - (BAND_H + 24) // 2 - 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = _tile_height(self.width())
        if h != self.height():
            self.setFixedHeight(h)
        self._seq = -1
        self._place()

    # -- 描画 ----------------------------------------------------------------
    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pal = ref_palette(self)
        win, txt = pal.window().color(), pal.windowText().color()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, TILE_RADIUS, TILE_RADIUS)
        p.setClipPath(clip)
        dot, name, sub, _wanted = self._state
        if self._pix is not None:
            p.fillRect(r, QColor(16, 16, 16))
            lw = self._pix.width() / self._pix.devicePixelRatio()
            lh = self._pix.height() / self._pix.devicePixelRatio()
            p.drawPixmap(int((self.width() - lw) / 2), int((self.height() - lh) / 2), self._pix)
        else:
            p.fillRect(r, blend(win, txt, 0.14 if is_dark(pal) else 0.10))
            col = secondary_color(pal)
            top = (self.height() - BAND_H) / 2 - 22
            p.drawPixmap(int(self.width() / 2 - 14), int(top),
                         icons.pixmap("phone", 28, col, self.devicePixelRatioF()))
            p.setPen(col)
            p.setFont(self.font())
            p.drawText(QRectF(8, top + 34, self.width() - 16, 22), int(Qt.AlignmentFlag.AlignCenter),
                       t("multi.tile.empty"))
        # 下の帯
        band = QRectF(r.left(), r.bottom() - BAND_H + 0.5, r.width(), BAND_H)
        p.fillRect(band, QColor(0, 0, 0, 150))
        cy = band.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_dot_color(dot, pal))
        p.drawEllipse(QPointF(band.left() + 16, cy), 4.5, 4.5)
        avail = self.btn.x() - 10 - (band.left() + 30)
        name_font = QFont(self.font())
        name_font.setWeight(QFont.Weight.DemiBold)
        name_font.setPointSizeF(9.5)
        sub_font = QFont(self.font())
        sub_font.setPointSizeF(9.0)
        nfm, sfm = QFontMetrics(name_font), QFontMetrics(sub_font)
        sub_w = sfm.horizontalAdvance(sub)
        gap = 10
        x = band.left() + 30
        full = nfm.horizontalAdvance(name)
        if full + gap + sub_w <= avail:
            name_w, show_sub = full, True
        elif avail - gap - sub_w >= 70:
            name_w, show_sub = avail - gap - sub_w, True    # 名前を … で省いて、右の「USB · 60fps」を残す
        else:
            name_w, show_sub = max(0, avail), False
        p.setFont(name_font)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x, band.top(), name_w, band.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   nfm.elidedText(name, Qt.TextElideMode.ElideRight, int(name_w)))
        if show_sub:
            p.setFont(sub_font)
            p.setPen(QColor(255, 255, 255, 190))
            p.drawText(QRectF(x + name_w + gap, band.top(), sub_w + 2, band.height()),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), sub)
        # 左上のバッジ
        badge_font = QFont(self.font())
        badge_font.setPointSizeF(9.0)
        badge_font.setWeight(QFont.Weight.DemiBold)
        label = self.group.label(self.camera)
        bw = QFontMetrics(badge_font).horizontalAdvance(label) + 16
        badge = QRectF(r.left() + 8, r.top() + 8, bw, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(badge, 4, 4)
        p.setFont(badge_font)
        p.setPen(QColor(255, 255, 255))
        p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), label)
        p.setClipping(False)
        accent = self._hover or self.hasFocus()
        p.setPen(QPen(pal.highlight().color() if accent else border_color(pal), 1.5 if accent else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, TILE_RADIUS, TILE_RADIUS)
        p.end()

    # -- 操作 ----------------------------------------------------------------
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._press = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._press = e.button() == Qt.MouseButton.LeftButton
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._press = False
            self.chosen.emit(self.camera)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.chosen.emit(self.camera)
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()


class AddTile(QWidget):
    """「＋ カメラを追加」: 点線の枠の真ん中に + と文字。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(200)
        self.setFixedHeight(_tile_height(300))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAccessibleName(t("multi.add"))
        self.setToolTip(t("multi.add"))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = _tile_height(self.width())
        if h != self.height():
            self.setFixedHeight(h)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        win, txt = pal.window().color(), pal.windowText().color()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = self._hover or self.hasFocus()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(blend(win, txt, 0.06 if accent else 0.0))
        p.drawRoundedRect(r, TILE_RADIUS, TILE_RADIUS)
        pen = QPen(pal.highlight().color() if accent else blend(win, txt, 0.35), 1.2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, TILE_RADIUS, TILE_RADIUS)
        col = txt if accent else secondary_color(pal)
        cx, cy = self.width() / 2, self.height() / 2 - 14
        p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx - 11, cy), QPointF(cx + 11, cy))
        p.drawLine(QPointF(cx, cy - 11), QPointF(cx, cy + 11))
        p.setPen(col)
        p.drawText(QRectF(8, cy + 20, self.width() - 16, 24), int(Qt.AlignmentFlag.AlignCenter), t("multi.add"))
        p.end()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()


class CamerasPage(Page):
    """「カメラ」ページ (2 台以上のときだけナビの先頭に出る)。各カメラのプレビューを 1 画面に並べる。"""

    camera_chosen = Signal(object)
    add_requested = Signal()

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.group = ctrl.camera_group
        self.tiles = []
        self.host = QWidget()
        self.grid = _grid(self.host)
        self.add(self.host)
        self.add(Hint(t("multi.cameras.hint")))
        self.finish()
        self.preview_timer = QTimer(self)
        self.preview_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.preview_timer.timeout.connect(self._preview_tick)
        self.status_timer = QTimer(self, interval=500)
        self.status_timer.timeout.connect(self._refresh_tiles)
        self._apply_fps()
        ctrl.settings_changed.connect(lambda key, value: self._apply_fps() if key == "preview_fps" else None)
        self.group.changed.connect(self.rebuild)
        self.rebuild()

    def _apply_fps(self):
        try:
            fps = int(self.ctrl.get("preview_fps", 30))
        except Exception:
            fps = 30
        fps = max(1, min(TILE_FPS_MAX, fps))
        self.preview_timer.setInterval(max(1, int(round(1000.0 / fps))))

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.tiles = []
        cameras = list(self.group.cameras)
        for index, camera in enumerate(cameras):
            tile = CameraTile(self.group, camera)
            tile.chosen.connect(self.camera_chosen)
            self.tiles.append(tile)
            self.grid.addWidget(tile, index // 2, index % 2)
        self.add_tile = None
        if self.group.can_add:
            self.add_tile = AddTile()
            self.add_tile.clicked.connect(self.add_requested)
            self.grid.addWidget(self.add_tile, len(cameras) // 2, len(cameras) % 2)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)

    # -- 描画は見えているタイルだけ。最小化中・別ページのときは止める -----------------------------
    def _active(self):
        win = self.window()
        return self.isVisible() and not (win is not None and win.isMinimized())

    def _preview_tick(self):
        if not self._active():
            return
        for tile in self.tiles:
            if not tile.visibleRegion().isEmpty():
                tile.update_preview()

    def _refresh_tiles(self):
        if not self._active():
            return
        for tile in self.tiles:
            tile.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        self._refresh_tiles()
        self.preview_timer.start()
        self.status_timer.start()

    def hideEvent(self, e):
        self.preview_timer.stop()
        self.status_timer.stop()
        super().hideEvent(e)

    def on_activated(self):
        self._refresh_tiles()


def _grid(parent):
    lay = QGridLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setHorizontalSpacing(12)
    lay.setVerticalSpacing(12)
    return lay


# ---------------------------------------------------------------------------
# iPhone を選ぶ一覧
# ---------------------------------------------------------------------------

class PickEntry:
    """選択肢 1 つ。owner はすでに使っているカメラ (自分のカメラなら own=True)。"""

    def __init__(self, key, name, detail, device=None, address="", serial="", owner=None, own=False, found=False):
        self.key, self.name, self.detail = key, name, detail
        self.device, self.address, self.serial = device, address, serial
        self.owner, self.own, self.found = owner, own, found

    @property
    def selectable(self):
        return self.owner is None or self.own


def build_entries(group, camera, discovered, usb):
    """登録済みの iPhone (Wi-Fi で見つかったもの・USB でつながっているものを含む) と、USB だけの端末。
    usb は {USB シリアル: 端末 id (分からなければ "")}。"""
    found_ips = {ip for device in discovered for ip in device.addresses}
    users, usb_users = group.users(), group.usb_users()
    by_id = {ident: serial for serial, ident in usb.items() if ident}
    entries, seen = [], set()
    for device in merge_devices(group.known_devices(), discovered):
        if not device.code:
            continue   # 登録していない端末は、QR で追加してから選ぶ
        live = [ip for ip in device.addresses if ip in found_ips]
        serial = by_id.get(device.id, "")
        ways = []
        if serial:
            ways.append(t("wireless.way.usb"))
        if live:
            ways.append(t("wireless.way.wifi"))
        detail = " · ".join(part for part in (device.model, " / ".join(ways) or t("multi.pick.registered")) if part)
        owner = users.get(device.id) or (usb_users.get(serial) if serial else None)
        entries.append(PickEntry(device.id, device.title or t("wireless.unknown_device"), detail, device,
                                 live[0] if live else device.addresses[0], serial, owner,
                                 owner is camera and camera is not None, bool(ways)))
        if serial:
            seen.add(serial)
    for serial in usb:
        if serial in seen:
            continue
        owner = usb_users.get(serial)
        entries.append(PickEntry("usb:" + serial, t("multi.usb_phone"), t("wireless.way.usb"), None, "", serial,
                                 owner, owner is camera and camera is not None, True))
    return sorted(entries, key=lambda entry: (not entry.found, entry.name))


class PickRow(QWidget):
    """一覧の 1 行: アイコン | 名前 + 詳細 | 右に「カメラ 1 で使用中」か選択の丸。行のどこを押しても選べる。"""

    def __init__(self, entry, group, buttons, parent=None):
        super().__init__(parent)
        self.entry = entry
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        lay.addWidget(IconLabel("phone", 20), 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(entry.name)
        font = name.font()
        font.setWeight(QFont.Weight.DemiBold)
        name.setFont(font)
        texts.addWidget(name)
        texts.addWidget(Hint(entry.detail, wrap=False))
        lay.addLayout(texts, 1)
        self.radio = None
        if entry.owner is not None and not entry.own:
            lay.addWidget(Hint(t("multi.in_use", camera=group.label(entry.owner)), wrap=False), 0,
                          Qt.AlignmentFlag.AlignVCenter)
            self.setEnabled(False)
        else:
            self.radio = QRadioButton()
            self.radio.setAccessibleName(entry.name)
            buttons.addButton(self.radio)
            lay.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumHeight(58)

    def mouseReleaseEvent(self, e):
        if self.radio is not None and e.button() == Qt.MouseButton.LeftButton:
            self.radio.setChecked(True)
        super().mouseReleaseEvent(e)


class DevicePicker(QWidget):
    """iPhone を選ぶ。ウィザードのステップ 1 と、接続ページの「変更…」で共用する。
    camera を渡すと、そのカメラが今使っている iPhone を最初から選んだ状態にする。"""

    changed = Signal()

    def __init__(self, group, camera=None, parent=None):
        super().__init__(parent)
        self.group, self.camera = group, camera
        self.discovered = list(getattr(group.primary, "demo_discovered", []) or [])
        self.usb = dict(getattr(group.primary, "demo_usb", {}) or {})
        self.inbox = SimpleQueue()
        self._entries = []
        self._selected_key = None
        self._demo = group.primary.demo
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        from phonecam.qt.wireless import Spinner
        self.spinner = Spinner()
        self.scan_label = Hint(t("multi.pick.searching"), wrap=False)
        self.scan_label.hide()
        head.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.scan_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        lay.addLayout(head)
        self.card = Card()
        lay.addWidget(self.card)
        self.qr = button(t("multi.pick.qr"))
        self.qr.clicked.connect(self._pair)
        qr_row = QHBoxLayout()
        qr_row.addWidget(self.qr)
        qr_row.addStretch(1)
        lay.addLayout(qr_row)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._result)
        self._buttons = None
        self._rows = []
        self._rebuild()
        if not self._demo:
            self.scan()

    # -- 検索 (Wi-Fi の Bonjour と USB。画面を止めないよう別スレッド) ---------------------------------
    def scan(self):
        if self.timer.isActive() or self._demo:
            return
        from phonecam.qt.wireless import local_addresses
        addresses = local_addresses()
        group, inbox = self.group, self.inbox

        def work():
            try:
                found = discover(addresses)
            except Exception:
                found = []
            usb = {}
            try:
                for serial in usbmux_devices():
                    usb[serial] = group.routes.usb_identity(serial)
            except Exception:
                usb = {}
            inbox.put((found, usb))

        Thread(target=work, daemon=True).start()
        self.spinner.start()
        self.scan_label.show()
        self.timer.start()

    def _result(self):
        try:
            self.discovered, self.usb = self.inbox.get_nowait()
        except Empty:
            return
        self.timer.stop()
        self.spinner.stop()
        self.scan_label.hide()
        self._rebuild()

    # -- 一覧 ----------------------------------------------------------------
    def selected(self):
        for row in self._rows:
            if row.radio is not None and row.radio.isChecked():
                return row.entry
        return None

    def _rebuild(self, select_key=None):
        keep = select_key or (self.selected().key if self.selected() else self._selected_key)
        lay = self.card.layout()
        while lay.count():
            widget = lay.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        if self._buttons is not None:
            self._buttons.deleteLater()
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        self._entries = build_entries(self.group, self.camera, self.discovered, self.usb)
        for entry in self._entries:
            row = PickRow(entry, self.group, self._buttons)
            self._rows.append(row)
            lay.addWidget(row)
        if not self._entries:
            note = FixedHint(t("multi.pick.none"))
            box = QWidget()
            inner = QVBoxLayout(box)
            inner.setContentsMargins(16, 14, 16, 14)
            inner.addWidget(note)
            lay.addWidget(box)
        target = next((row for row in self._rows if row.radio is not None and row.entry.key == keep), None)
        if target is None and self.camera is not None:
            target = next((row for row in self._rows if row.entry.own), None)
        if target is not None:
            target.radio.setChecked(True)
        self._buttons.buttonToggled.connect(lambda *_: self._on_toggled())
        self._selected_key = self.selected().key if self.selected() else None
        self.changed.emit()

    def _on_toggled(self):
        entry = self.selected()
        self._selected_key = entry.key if entry else None
        self.changed.emit()

    # -- 新しい iPhone を QR で追加 ---------------------------------------------------------------
    def _pair(self):
        from phonecam.qt.wireless import PairDialog
        dialog = PairDialog(self.window())
        dialog.exec()
        device = dialog.device
        dialog.deleteLater()
        if not device:
            return
        known = self.group.known_devices()
        old = next((item for item in known if item.id == device.id), None)
        if old and old.alias:
            device = replace(device, alias=old.alias)
        root = self.group.primary
        root.changed("known_devices", [item.stored() for item in [device, *(i for i in known if i.id != device.id)][:64]])
        root.save_now()
        self._rebuild(select_key=device.id)


class DevicePickDialog(QDialog):
    """接続ページの「変更…」: このカメラにつなぐ iPhone を選び直す。"""

    def __init__(self, parent, group, camera):
        super().__init__(parent)
        self.group, self.camera = group, camera
        self.setWindowTitle(t("multi.pick.title", camera=group.label(camera)))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        title = QLabel(t("multi.pick.title", camera=group.label(camera)))
        font = title.font()
        font.setPointSizeF(13.0)
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        lay.addWidget(title)
        lay.addWidget(Hint(t("multi.pick.note")))
        self.picker = DevicePicker(group, camera)
        lay.addWidget(self.picker)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(t("multi.cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = button(t("multi.pick.ok"), primary=True)
        self.ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self.picker.changed.connect(self._update)
        self._update()

    def _update(self):
        entry = self.picker.selected()
        self.ok.setEnabled(entry is not None and not entry.own)

    def accept(self):
        entry = self.picker.selected()
        if entry is None or entry.own:
            return super().reject()
        if not self.group.assign(self.camera, entry.device, entry.address, entry.serial):
            self.camera.toast.emit(t("multi.in_use_error"), "error")
            return
        super().accept()


# ---------------------------------------------------------------------------
# 「カメラを追加」ウィザード
# ---------------------------------------------------------------------------

class AddCameraWizard(QDialog):
    """3 ステップ: 1 iPhone を選ぶ → 2 OBS に出す準備 (仮想カメラの登録) → 3 完了。
    ステップ 3 に着くまでにやめたら、作りかけのカメラは取り除く。"""

    STEPS = 3

    def __init__(self, parent, group):
        super().__init__(parent)
        self.group = group
        self.camera = None
        self.step = 0
        self._finished = False
        self._asked = False
        self.setWindowTitle(t("multi.wizard.title"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        self.step_label = Hint("")
        lay.addWidget(self.step_label)
        self.title = QLabel("")
        font = self.title.font()
        font.setPointSizeF(14.0)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_pick())
        self.stack.addWidget(self._build_prepare())
        self.stack.addWidget(self._build_done())
        lay.addWidget(self.stack, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = button(t("multi.cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        self.next_btn = button(t("multi.wizard.next"), primary=True)
        self.next_btn.clicked.connect(self._next)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.next_btn)
        lay.addLayout(buttons)
        self.picker.changed.connect(self._update_buttons)
        self.go(0)

    # -- ステップごとの中身 ----------------------------------------------------------------------
    def _build_pick(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(Hint(t("multi.wizard.pick_note")))
        self.picker = DevicePicker(self.group)
        lay.addWidget(self.picker)
        return page

    def _build_prepare(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        card = Card()
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(10)
        from phonecam.qt.wireless import Spinner
        self.prep_spinner = Spinner()
        self.prep_icon = QLabel()
        self.prep_icon.setFixedSize(18, 18)
        self.prep_text = QLabel("")
        font = self.prep_text.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.prep_text.setFont(font)
        self.prep_text.setWordWrap(True)
        row.addWidget(self.prep_spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.prep_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.prep_text, 1)
        card.layout().addWidget(box)
        lay.addWidget(card)
        self.prep_detail = FixedHint("")
        self.prep_detail.hide()
        lay.addWidget(self.prep_detail)
        retry_row = QHBoxLayout()
        self.retry_btn = button(t("multi.wizard.retry"))
        self.retry_btn.clicked.connect(self._register)
        self.retry_btn.hide()
        retry_row.addWidget(self.retry_btn)
        retry_row.addStretch(1)
        lay.addLayout(retry_row)
        lay.addStretch(1)
        return page

    def _build_done(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.done_note = QLabel("")
        self.done_note.setWordWrap(True)
        lay.addWidget(self.done_note)
        self.done_card = Card()
        lay.addWidget(self.done_card)
        self.done_rows = []
        for icon, key in (("video", "multi.wizard.obs1"), ("camera", "multi.wizard.obs2"), ("check", "multi.wizard.obs3")):
            row = Row(icon, "")
            self.done_card.layout().addWidget(row)
            self.done_rows.append((row, key))
        lay.addStretch(1)
        return page

    # -- 進行 ----------------------------------------------------------------------------------
    def go(self, step):
        self.step = step
        self.stack.setCurrentIndex(step)
        self.step_label.setText(t("multi.wizard.step_of", n=step + 1, total=self.STEPS))
        self.title.setText(t(("multi.wizard.step1", "multi.wizard.step2", "multi.wizard.step3")[step]))
        if step == 2:
            self.done_note.setText(t("multi.wizard.obs_title", name=self.group.obs_name(self.camera)))
            for row, key in self.done_rows:
                row.setTitle(t(key, name=self.group.obs_name(self.camera)))
        self.cancel_btn.setVisible(step < 2)
        self.next_btn.setText(t("multi.wizard.done" if step == 2 else "multi.wizard.next"))
        self._update_buttons()

    def _update_buttons(self):
        if self.step == 0:
            entry = self.picker.selected()
            self.next_btn.setEnabled(entry is not None and entry.selectable and entry.owner is None)
        elif self.step == 1:
            self.next_btn.setEnabled(self._prepared)
        else:
            self.next_btn.setEnabled(True)

    _prepared = False

    def _next(self):
        if self.step == 0:
            self._create()
        elif self.step == 1:
            self._finished = True   # ここから先は、閉じてもカメラを残す
            self._detach()
            self.go(2)
        else:
            self.accept()

    def _create(self):
        entry = self.picker.selected()
        if entry is None or entry.owner is not None:
            return
        camera = self.group.add()
        if camera is None:
            self.group.primary.toast.emit(t("multi.select_profile"), "error")
            return
        self.camera = camera
        if not self.group.assign(camera, entry.device, entry.address, entry.serial):
            self.group.remove(camera)
            self.camera = None
            self.group.primary.toast.emit(t("multi.in_use_error"), "error")
            return
        self.go(1)
        self._prepare()

    # -- ステップ 2: OBS に出す準備 --------------------------------------------------------------
    def _prepare(self):
        camera = self.camera
        camera.camera_registration_changed.connect(self._registration_changed)
        camera.toast.connect(self._toast)
        state = camera.camera_registration
        name = self.group.obs_name(camera)
        if state == "ok":
            self.show_prepare("ok", t("multi.wizard.prepared", name=name))
        elif camera.demo or not camera.frozen or state == "unavailable":
            self.show_prepare("skip", t("multi.wizard.skipped", name=name), t("multi.wizard.src_only"))
        else:
            self._register()

    def _register(self):
        self._asked = True
        self.show_prepare("busy", t("multi.wizard.preparing", name=self.group.obs_name(self.camera)),
                          t("multi.wizard.admin_hint"))
        self.camera.change_camera_registration("register")

    def _registration_changed(self):
        camera = self.camera
        if camera is None or camera.camera_registration_busy:
            return
        name = self.group.obs_name(camera)
        if camera.camera_registration == "ok":
            self.show_prepare("ok", t("multi.wizard.prepared", name=name))
        elif self._asked:
            self.show_prepare("error", t("multi.wizard.failed", name=name), t("multi.wizard.failed_note"))

    def _toast(self, text, kind="info"):
        # 失敗の理由 (キャンセル / 時間切れ / 失敗) は、登録を受け持つ Controller の通知で届く
        if kind == "error" and self.step == 1 and self._asked:
            self.prep_detail.setText(text)
            self.prep_detail.show()

    def show_prepare(self, kind, text, detail=""):
        """kind: busy (スピナー) / ok (✓) / error (理由と「もう一度」) / skip (登録できない環境。先へ進める)。"""
        self.prep_text.setText(text)
        self.prep_detail.setText(detail)
        self.prep_detail.setVisible(bool(detail))
        self.retry_btn.setVisible(kind == "error")
        if kind == "busy":
            self.prep_spinner.start()
        else:
            self.prep_spinner.stop()
        self.prep_icon.setVisible(kind in ("ok", "error"))
        if kind in ("ok", "error"):
            pal = ref_palette(self)
            self.prep_icon.setPixmap(icons.pixmap("check" if kind == "ok" else "warning", 18,
                                                  ok_color(pal) if kind == "ok" else error_color(pal),
                                                  self.devicePixelRatioF()))
        self._prepared = kind in ("ok", "skip")
        self._update_buttons()

    # -- 後始末 --------------------------------------------------------------------------------
    def _detach(self):
        camera = self.camera
        if camera is None:
            return
        camera.camera_registration_changed.disconnect(self._registration_changed)
        camera.toast.disconnect(self._toast)

    def done(self, result):
        if self.camera is not None and not self._finished:
            self._detach()
            self.group.remove(self.camera)   # 作りかけのカメラは残さない (仮想カメラの登録は残るので、次は自動で ✓)
            self.camera = None
        elif self.camera is not None:
            self._detach()
        self.prep_spinner.stop()
        super().done(result)


# ---------------------------------------------------------------------------
# カメラの削除
# ---------------------------------------------------------------------------

def confirm_remove(parent, group, camera):
    """確認ダイアログ。仮想カメラの登録解除もするかをチェックで選ばせる。消したら True。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(t("multi.remove.title"))
    box.setText(t("multi.remove.confirm", camera=group.label(camera)))
    box.setInformativeText(t("multi.remove.note"))
    check = QCheckBox(t("multi.remove.unregister", name=group.obs_name(camera)))
    box.setCheckBox(check)
    remove = box.addButton(t("multi.remove.ok"), QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(t("multi.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    if box.clickedButton() is not remove:
        return False
    return group.remove(camera, unregister=check.isChecked())
