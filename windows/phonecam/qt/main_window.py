# -*- coding: utf-8 -*-
"""メインウィンドウ: 左ナビ (アイコン + ラベル) + QStackedWidget、上部の細い通知帯、下部のトースト。"""

import importlib
import os
import sys

from PySide6.QtCore import QByteArray, QEvent, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from phonecam.core.paths import WINDOWS_DIR
from phonecam.i18n import t
from phonecam.qt import icons
from phonecam.qt.widgets import _THEME_EVENTS, Hint, Page, blend, button, ref_palette, text_color

# (キー, アイコン名)。順番がページ番号 (qt/pages/<キー>.py の build(ctrl) -> QWidget)
NAV_ITEMS = [("connection", "link"), ("video", "video"), ("quality", "sun"), ("effects", "sparkles"),
             ("audio", "mic"), ("recording", "record"), ("status", "gauge"),
             ("shortcuts", "keyboard"), ("settings", "gear")]

NAV_WIDTH = 200
TOAST_MS = 2000


def assets_dir():
    base = getattr(sys, "_MEIPASS", None) or WINDOWS_DIR
    return os.path.join(base, "assets")


def window_icon():
    d = assets_dir()
    for name in ("icon.ico", "icon_app.png", "icon.png"):
        path = os.path.join(d, name)
        if os.path.exists(path):
            ic = QIcon(path)
            if not ic.isNull():
                return ic
    return QIcon()


class NavDelegate(QStyledItemDelegate):
    """Windows 11 設定アプリ風: 選択行は薄い面 + 左のアクセントバー。アイコンと文字はパレットの文字色。"""

    def paint(self, p, opt, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(opt.widget) if opt.widget is not None else opt.palette
        win, txt = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window), pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        r = QRectF(opt.rect).adjusted(8, 1, -8, -1)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        hover = bool(opt.state & QStyle.StateFlag.State_MouseOver)
        if selected or hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(blend(win, txt, 0.08 if selected else 0.04))
            p.drawRoundedRect(r, 4, 4)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight))
            p.drawRoundedRect(QRectF(r.left(), r.center().y() - 8, 3, 16), 1.5, 1.5)
        ic = index.data(Qt.ItemDataRole.DecorationRole)
        x = r.left() + 14
        if isinstance(ic, QIcon):
            ic.paint(p, QRect(int(x), int(r.center().y() - 8), 16, 16))
        p.setPen(txt)
        p.setFont(opt.font)
        p.drawText(QRect(int(x + 28), int(r.top()), int(r.width() - 42), int(r.height())),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        p.restore()

    def sizeHint(self, opt, index):
        return QSize(opt.rect.width(), 40)


class Bar(QFrame):
    """上部の細い通知帯 (更新のお知らせ / 熱警告)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(16, 6, 16, 6)
        self._lay.setSpacing(10)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self._lay.addWidget(self.label, 1)
        self._buttons = []
        self.hide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        pal = ref_palette(self)
        win, txt = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window), pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        p.fillRect(self.rect(), blend(win, txt, 0.06))
        p.setPen(blend(win, txt, 0.12))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()

    def set_actions(self, actions):
        for b in self._buttons:
            self._lay.removeWidget(b)
            b.deleteLater()
        self._buttons = []
        for label, fn in actions or ():
            b = button(label)
            b.setMinimumWidth(0)
            b.clicked.connect(lambda _c=False, f=fn: f())
            self._lay.addWidget(b, 0)
            self._buttons.append(b)


class MainWindow(QMainWindow):
    def __init__(self, ctrl, tick=True):
        super().__init__()
        self.ctrl = ctrl
        self.setWindowTitle("PhoneCam")
        self.setWindowIcon(window_icon())
        self.resize(960, 720)
        self.setMinimumSize(640, 520)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 左ナビ
        navbox = QWidget()
        navbox.setFixedWidth(NAV_WIDTH)
        nl = QVBoxLayout(navbox)
        nl.setContentsMargins(0, 14, 0, 8)
        nl.setSpacing(10)
        self.app_icon = QLabel()
        self.app_icon.setContentsMargins(16, 0, 0, 0)
        self._set_app_icon()
        nl.addWidget(self.app_icon, 0, Qt.AlignmentFlag.AlignLeft)
        self.nav = QListWidget()
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.viewport().setAutoFillBackground(False)
        self.nav.setMouseTracking(True)
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setItemDelegate(NavDelegate(self.nav))
        self.nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for key, icon_name in NAV_ITEMS:
            item = QListWidgetItem(t("nav." + key))
            item.setData(Qt.ItemDataRole.UserRole, icon_name)
            self.nav.addItem(item)
        self._refresh_nav_icons()
        nl.addWidget(self.nav, 1)
        outer.addWidget(navbox)

        # 右: 通知帯 + ページ
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.update_bar = Bar()
        self.warn_bar = Bar()
        rl.addWidget(self.update_bar)
        rl.addWidget(self.warn_bar)
        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        outer.addWidget(right, 1)

        self.pages = []
        self.page_errors = []   # [(キー, エラー文)]: build に失敗したページ (CI のスモークテストが見る)
        for key, _icon in NAV_ITEMS:
            self.stack.addWidget(self._load_page(key))

        # トースト (下の中央)
        self.toast_label = QLabel(central)
        self.toast_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast_label.setContentsMargins(18, 8, 18, 8)
        f = self.toast_label.font()
        f.setBold(True)
        self.toast_label.setFont(f)
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self.nav.currentRowChanged.connect(self._page_selected)
        self.nav.setCurrentRow(0)

        ctrl.toast.connect(self.show_toast)
        ctrl.status_changed.connect(self._status)
        ctrl.update_changed.connect(self._update_banner)
        self._status(ctrl.status)
        self._update_banner(ctrl.update_banner)

        self._tick_timer = None
        if tick:
            self._tick_timer = QTimer(self)
            self._tick_timer.setInterval(50)
            self._tick_timer.timeout.connect(ctrl.tick)
            self._tick_timer.start()

    # -- ページ --------------------------------------------------------------------
    def _load_page(self, key):
        try:
            mod = importlib.import_module("phonecam.qt.pages." + key)
            page = mod.build(self.ctrl)
        except Exception as e:   # 1ページの不具合でアプリ本体を止めない
            import traceback
            traceback.print_exc()
            self.page_errors.append((key, "%s: %s" % (type(e).__name__, e)))
            page = Page()
            sec = page.add_section(t("nav." + key))
            sec.add_padded(Hint(t("shell.page_error", name=t("nav." + key), err=e)))
            page.finish()
        self.pages.append(page)
        return page

    def select(self, index):
        self.nav.setCurrentRow(max(0, min(len(NAV_ITEMS) - 1, int(index))))

    def current_page(self):
        return self.stack.currentWidget()

    def _page_selected(self, row):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        fn = getattr(self.stack.currentWidget(), "on_activated", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    # -- ナビのアイコン (パレットが変わったら作り直す) ------------------------------------
    def _refresh_nav_icons(self):
        col = text_color(self.nav)
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            item.setIcon(icons.icon(item.data(Qt.ItemDataRole.UserRole), col))

    def _set_app_icon(self):
        path = os.path.join(assets_dir(), "icon_app.png")
        dpr = self.devicePixelRatioF()
        pm = QPixmap(path)
        if pm.isNull():
            self.app_icon.clear()
            return
        pm = pm.scaled(int(20 * dpr), int(20 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self.app_icon.setPixmap(pm)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS and hasattr(self, "nav"):
            self._refresh_nav_icons()
            self.nav.viewport().update()

    # -- 通知 --------------------------------------------------------------------------------
    def _status(self, s):
        warn = s.get("warn_text", "") if s else ""
        self.warn_bar.label.setText(warn)
        self.warn_bar.setVisible(bool(warn))

    def _update_banner(self, banner):
        if banner:
            self.update_bar.label.setText(banner.get("text", ""))
            self.update_bar.set_actions(banner.get("actions"))
            self.update_bar.show()
        else:
            self.update_bar.hide()

    def show_toast(self, text, kind="info"):
        pal = ref_palette(self)
        # 反転配色 (ライト = 暗い帯 / ダーク = 明るい帯)
        bg = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)
        fg = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
        pal2 = QPalette()
        pal2.setColor(QPalette.ColorRole.WindowText, fg)
        pal2.setColor(QPalette.ColorRole.Window, bg)
        self.toast_label.setPalette(pal2)
        self.toast_label.setAutoFillBackground(True)
        self.toast_label.setText(text)
        self.toast_label.setMinimumSize(0, 0)
        self.toast_label.setMaximumSize(16777215, 16777215)
        self.toast_label.adjustSize()
        self._place_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        self._toast_timer.start(TOAST_MS)

    def _place_toast(self):
        c = self.centralWidget()
        lw = min(self.toast_label.width(), max(100, c.width() - 40))
        self.toast_label.setFixedWidth(lw)
        self.toast_label.move((c.width() - lw) // 2, c.height() - self.toast_label.height() - 24)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toast_label") and self.toast_label.isVisible():
            self._place_toast()

    def restore_state(self):
        """起動時: 最大化 (既定) か、前回の位置と大きさ。画面外なら無視。show の代わりに呼ぶ。"""
        s = self.ctrl.settings
        geo = s.get("window_geometry") or ""
        if geo and not s.get("window_maximized", True):
            try:
                if self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii"))):
                    from PySide6.QtGui import QGuiApplication
                    if not any(sc.availableGeometry().intersects(self.frameGeometry())
                               for sc in QGuiApplication.screens()):
                        self.resize(960, 720)
                        self.move(QGuiApplication.primaryScreen().availableGeometry().topLeft())
            except Exception:
                pass
        if s.get("window_maximized", True):
            if geo:   # 最大化を解除したときの戻り先
                try:
                    self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
                except Exception:
                    pass
            self.showMaximized()
        else:
            self.show()

    def _save_window_state(self):
        try:
            maximized = self.isMaximized() or self.isFullScreen()
            self.ctrl.settings["window_maximized"] = bool(maximized)
            if not maximized:
                self.ctrl.settings["window_geometry"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            pass

    def closeEvent(self, e):
        self._save_window_state()
        try:
            self.ctrl.close()
        finally:
            e.accept()
