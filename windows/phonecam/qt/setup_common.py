# -*- coding: utf-8 -*-
"""セットアップ画面 (ウィザード・診断) の共通部品。Windows 11 設定アプリの質感で、色はパレットから計算する。

- WrapLabel     折り返す文章。色の役割 (text/sub/ok/warn/bad/accent) を持つ。高さは今の幅で固定する
- StatusGlyph   状態アイコン (✓ / ⚠ / ✗ / ○ / −)。テーマ・DPI が変わると描き直す
- ThinBar       細い直線の進捗バー
- StepDots      段階の位置を示す小さな点
- Fold          小さな折りたたみ (見出し + 中身)
- NumberedLines 番号つき / 箇条書きの行
- QrView        QR コード (phonecam_qr の行列を描く)
- Spinner / Rule / centered   スピナー・区切り線・中央寄せの入れ物
- QtRoot        SetupController が使う after() をタイマーで実現する (Tk の root の代わり)
- fit_to_work_area / clear_layout / link_button / big_button

画面のロジック (検出・ジョブ・状態機械) は phonecam_setup.py にあり、ここは tkinter に依存しない。
"""

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from phonecam.qt import icons
from phonecam.qt.widgets import (
    _ICON_REFRESH,
    _THEME_EVENTS,
    _own_text_palette,
    blend,
    button,
    error_color,
    is_dark,
    ok_color,
    ref_palette,
    secondary_color,
    separator_color,
)

# ---------------------------------------------------------------------------
# 色
# ---------------------------------------------------------------------------


def warn_color(palette):
    """警告のくすんだ琥珀色 (ライト/ダーク)。"""
    return QColor("#ffb864") if is_dark(palette) else QColor("#9d5000")


def accent_color(palette):
    return palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)


def _text(palette):
    return palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)


ROLE_COLORS = {
    "text": _text,
    "sub": secondary_color,
    "ok": ok_color,
    "warn": warn_color,
    "bad": error_color,
    "accent": accent_color,
}


# ---------------------------------------------------------------------------
# ラベル
# ---------------------------------------------------------------------------

class WrapLabel(QLabel):
    """折り返す文章。色は role から (パレットが変わると計算し直す)。
    高さは今の幅に合わせて固定する (入れ子のレイアウトでも、2 行目以降が切れたり重なったりしない)。"""

    _busy = False

    def __init__(self, text="", role="text", size=None, bold=False, wrap=True, parent=None):
        super().__init__(text, parent)
        self._role = role
        self._custom = False
        self.setTextFormat(Qt.TextFormat.PlainText)
        if wrap:
            self.setWordWrap(True)
            self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            policy = self.sizePolicy()
            policy.setHeightForWidth(False)
            self.setSizePolicy(policy)
        if size is not None or bold:
            f = self.font()
            if size is not None:
                f.setPointSizeF(float(size))
            if bold:
                f.setWeight(QFont.Weight.DemiBold)
            self.setFont(f)
        self._apply()

    def role(self):
        return self._role

    def set_role(self, role):
        if role != self._role:
            self._role = role
            self._apply()

    def _apply(self):
        if self._busy:
            return
        self._busy = True
        try:
            if self._role == "text":
                if self._custom:   # 既定の文字色に戻す (自前のパレットを手放す)
                    self._custom = False
                    self.setPalette(QPalette())
            else:
                self._custom = True
                _own_text_palette(self, ROLE_COLORS[self._role](ref_palette(self.parentWidget())))
        finally:
            self._busy = False

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS or e.type() == QEvent.Type.ParentChange:
            self._apply()

    def _pin(self):
        if not self.wordWrap() or self.width() <= 1:
            return
        h = QLabel.heightForWidth(self, self.width())
        if h > 0 and (h != self.minimumHeight() or h != self.maximumHeight()):
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pin()

    def setText(self, text):
        super().setText(text)
        self._pin()


def heading(text, size=20.0, parent=None):
    """見出し (DemiBold)。1 行 (折り返しは WrapLabel の見出しを使う)。"""
    label = QLabel(text, parent)
    f = label.font()
    f.setPointSizeF(float(size))
    f.setWeight(QFont.Weight.DemiBold)
    label.setFont(f)
    return label


# ---------------------------------------------------------------------------
# 状態アイコン
# ---------------------------------------------------------------------------

GLYPHS = {
    "ok": ("check", ok_color),
    "warn": ("warning", warn_color),
    "bad": ("close", error_color),
    "idle": ("circle", secondary_color),
    "skip": ("minus", secondary_color),
}


class StatusGlyph(QLabel):
    """状態を表す 16px のアイコン (ok ✓ / warn ⚠ / bad ✗ / idle ○ / skip −)。"""

    def __init__(self, state="idle", size=16, parent=None):
        super().__init__(parent)
        self._state = state
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self._render()

    def state(self):
        return self._state

    def set_state(self, state):
        if state != self._state:
            self._state = state
            self._render()

    def _render(self):
        name, color_fn = GLYPHS.get(self._state, GLYPHS["idle"])
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.setPixmap(icons.pixmap(name, self._size, color_fn(ref_palette(self)), dpr))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _ICON_REFRESH:
            self._render()

    def showEvent(self, e):
        super().showEvent(e)
        self._render()


# ---------------------------------------------------------------------------
# 進捗バー
# ---------------------------------------------------------------------------

class ThinBar(QWidget):
    """細い (4px) 直線の進捗バー。トラックは文字色を薄く、進んだ分はアクセント色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def value(self):
        return self._value

    def set_value(self, value):
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            value = 0.0
        if value != self._value:
            self._value = value
            self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        win = pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window)
        track = blend(win, _text(pal), 0.14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 2, 2)
        if self._value > 0:
            p.setBrush(accent_color(pal))
            p.drawRoundedRect(QRectF(0, 0, max(4.0, self.width() * self._value), self.height()), 2, 2)
        p.end()


class StepDots(QWidget):
    """段階の位置を示す小さな点 (今の段階だけ細長いアクセント色)。"""

    def __init__(self, total, current, parent=None):
        super().__init__(parent)
        self._total = int(total)
        self._current = int(current)
        self.setFixedSize(max(0, self._total - 1) * 12 + 18, 8)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        dim = blend(pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window), _text(pal), 0.22)
        p.setPen(Qt.PenStyle.NoPen)
        x = 0.0
        for i in range(self._total):
            if i == self._current:
                p.setBrush(accent_color(pal))
                p.drawRoundedRect(QRectF(x, 0, 18, 8), 4, 4)
                x += 18 + 6
            else:
                p.setBrush(dim)
                p.drawEllipse(QRectF(x, 1, 6, 6))
                x += 6 + 6
        p.end()


# ---------------------------------------------------------------------------
# 折りたたみ・番号つき行
# ---------------------------------------------------------------------------

class Fold(QWidget):
    """小さな折りたたみ。見出しをクリックで中身を出し入れする (中身は最初に開いたときに build する)。"""

    def __init__(self, title, build, on_toggle=None, parent=None):
        super().__init__(parent)
        self._build = build
        self._built = False
        self._on_toggle = on_toggle
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QPushButton()
        self.header.setFlat(True)
        self.header.setCheckable(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setAccessibleName(title)
        self.header.setMinimumHeight(36)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(4, 4, 8, 4)
        row.setSpacing(8)
        self.arrow = _Arrow()
        label = WrapLabel(title, "text")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label, 1)
        outer.addWidget(self.header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(28, 2, 0, 8)
        self.body_layout.setSpacing(4)
        self.body.hide()
        outer.addWidget(self.body)
        self.header.clicked.connect(lambda: self.set_open(not self.is_open()))

    def is_open(self):
        return not self.body.isHidden()

    def set_open(self, value):
        value = bool(value)
        if value == self.is_open():
            return
        if value and not self._built:
            self._built = True
            self._build(self.body_layout)
        self.body.setVisible(value)
        self.header.setChecked(value)
        self.arrow.set_icon("chevron_up" if value else "chevron_down")
        if self._on_toggle is not None:
            try:
                self._on_toggle()
            except Exception:
                pass


class _Arrow(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = "chevron_down"
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._render()

    def set_icon(self, name):
        self._name = name
        self._render()

    def _render(self):
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.setPixmap(icons.pixmap(self._name, 16, secondary_color(ref_palette(self)), dpr))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _ICON_REFRESH:
            self._render()

    def showEvent(self, e):
        super().showEvent(e)
        self._render()


class NumberedLines(QWidget):
    """番号つき (1. 2. …) または箇条書き (•) の行。番号と本文は横並びにし、折り返すのは本文だけ。"""

    def __init__(self, lines, numbered=False, role="text", size=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for n, line in enumerate(lines, 1):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            mark = QLabel(("%d." % n) if numbered else "•")
            mark.setFixedWidth(22)
            mark.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            if size is not None:
                f = mark.font()
                f.setPointSizeF(float(size))
                mark.setFont(f)
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(WrapLabel(line, role, size), 1)
            lay.addLayout(row)


# ---------------------------------------------------------------------------
# QR コード
# ---------------------------------------------------------------------------

class QrView(QWidget):
    """QR コード。明るい地が必須なので、テーマに依らず白地に黒で描く。"""

    def __init__(self, modules, cell=4, quiet=3, parent=None):
        super().__init__(parent)
        self._modules = modules
        self._cell = int(cell)
        self._quiet = int(quiet)
        total = (len(modules) + 2 * self._quiet) * self._cell
        self.setFixedSize(total, total)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        n = len(self._modules)
        cell, quiet = self._cell, self._quiet
        for y, row in enumerate(self._modules):
            x = 0
            while x < n:
                if row[x]:
                    x2 = x
                    while x2 + 1 < n and row[x2 + 1]:
                        x2 += 1
                    p.drawRect((x + quiet) * cell, (y + quiet) * cell, (x2 - x + 1) * cell, cell)
                    x = x2 + 1
                else:
                    x += 1
        p.end()


class Spinner(QWidget):
    """小さな回転スピナー (16px)。start() の間だけタイマーを回す。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._angle = 0
        self._timer = QTimer(self, interval=60)
        self._timer.timeout.connect(self._step)

    def start(self):
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _step(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(secondary_color(ref_palette(self)), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(2, 2, 12, 12), -self._angle * 16, 270 * 16)
        p.end()


class Rule(QWidget):
    """1px の区切り線。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), separator_color(ref_palette(self)))
        p.end()


def centered(widget, max_width):
    """widget を最大幅 max_width で中央に置く入れ物 (ウィンドウが広いときも、ヘッダ・本文・ボタン行の左端をそろえる)。"""
    wrap = QWidget()
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    widget.setMaximumWidth(max_width)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())
    row.addStretch(1)
    row.addWidget(widget, 1000)
    row.addStretch(1)
    return wrap


# ---------------------------------------------------------------------------
# ボタン・レイアウト・ウィンドウ
# ---------------------------------------------------------------------------

def link_button(text, callback=None, parent=None):
    """枠のない文字だけのボタン (補助の操作)。"""
    b = QPushButton(text, parent)
    b.setFlat(True)
    b.setAutoDefault(False)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setMinimumHeight(30)
    if callback is not None:
        b.clicked.connect(lambda _checked=False: callback())
    return b


def big_button(text, callback=None, primary=True, parent=None):
    """押す場所をはっきり見せたい大きいボタン。"""
    b = button(text, primary=primary, parent=parent)
    b.setAutoDefault(False)
    b.setMinimumHeight(40)
    b.setMinimumWidth(180)
    f = b.font()
    f.setPointSizeF(11.0)
    f.setWeight(QFont.Weight.DemiBold)
    b.setFont(f)
    if callback is not None:
        b.clicked.connect(lambda _checked=False: callback())
    return b


def plain_button(text, callback=None, enabled=True, parent=None):
    b = button(text, parent=parent)
    b.setAutoDefault(False)
    b.setEnabled(bool(enabled))
    if callback is not None:
        b.clicked.connect(lambda _checked=False: callback())
    return b


def clear_layout(layout):
    """レイアウトの中身 (ウィジェットと入れ子のレイアウト) をすべて取り除いて破棄する。"""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            clear_layout(sub)
            sub.deleteLater()


def available_geometry(widget=None):
    """タスクバーを除いた作業領域 (論理 px)。"""
    screen = None
    try:
        if widget is not None:
            screen = widget.screen()
    except Exception:
        screen = None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def fit_to_work_area(widget, want_w, want_h, center=True):
    """widget を want_w x want_h にするが、作業領域 (タスクバーを除く画面) からははみ出させない。
    中央に置く。戻り値: (w, h)。"""
    avail = available_geometry(widget)
    frame_h = 48   # タイトルバーと枠の分
    w = max(320, min(int(want_w), int(avail.width() * 0.94)))
    h = max(300, min(int(want_h), avail.height() - frame_h - 8))
    widget.setMinimumSize(min(480, w), min(420, h))
    widget.resize(w, h)
    if center:
        x = avail.x() + (avail.width() - w) // 2
        y = avail.y() + max(0, (avail.height() - h - frame_h) // 2)
        widget.move(QPoint(x, y))
    return w, h


class QtRoot:
    """SetupController が root.after(ms, fn) で UI スレッドへ戻るための入れ物 (Tk の root の代わり)。"""

    def after(self, ms, fn):
        QTimer.singleShot(int(ms), fn)
        return None

    def after_cancel(self, _job):
        return None
