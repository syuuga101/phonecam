# -*- coding: utf-8 -*-
"""共通部品 (Windows 11 設定アプリ風): Page / Section / Row / Hint と、設定キーに結び付いた入力部品

ページ module は Controller と、ここの部品だけで組み立てる (Qt の素のウィジェットも使ってよい)。

    from phonecam.qt.widgets import Page, bound_combo, bound_switch, bound_slider, Hint, button
    def build(ctrl):
        page = Page()
        sec = page.add_section(t("video.send.title"))
        row = sec.add_row("video", t("video.resolution.label"))
        row.addControl(bound_combo(ctrl, "resolution", RES_LABELS, group="video"))
        return page

色は直書きしない (パレットから計算)。パレットが変わると自動で描き直す。
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from phonecam.qt import icons

MAX_CONTENT_W = 760   # 内容列の最大幅 (論理px)
PAGE_PAD_X = 24


# ---------------------------------------------------------------------------
# 色 (パレットから計算する。装飾色は使わない)
# ---------------------------------------------------------------------------

def blend(a, b, t):
    """a と b を t (0..1) の割合で混ぜる。"""
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _role(palette, role):
    """Active グループの色を返す (Inactive/Disabled グループが未設定で黒になる環境への備え)。"""
    return palette.color(QPalette.ColorGroup.Active, role)


def ref_palette(w=None):
    """色計算の基準パレット。ウィジェットが属するトップレベルのパレット (= 実際に背景を塗っている色)。
    無ければアプリのパレット。個々のウィジェット自身のパレットは使わない。"""
    try:
        if w is not None:
            top = w.window()
            if top is not None:
                return top.palette()
    except Exception:
        pass
    return QApplication.palette()


def is_dark(palette):
    win = _role(palette, QPalette.ColorRole.Window)
    txt = _role(palette, QPalette.ColorRole.WindowText)
    return win.lightness() < txt.lightness()


def text_color(w):
    return _role(ref_palette(w), QPalette.ColorRole.WindowText)


def secondary_color(palette):
    return blend(_role(palette, QPalette.ColorRole.Window), _role(palette, QPalette.ColorRole.WindowText), 0.62)


def card_color(palette):
    """カードの面の色 (Windows 11 設定: ライト #FBFBFB / ダーク #2B2B2B)。"""
    return QColor("#2b2b2b") if is_dark(palette) else QColor("#fbfbfb")


def border_color(palette):
    return QColor("#353535") if is_dark(palette) else QColor("#e5e5e5")


def separator_color(palette):
    return QColor("#353535") if is_dark(palette) else QColor("#ebebeb")


def error_color(palette):
    return QColor("#ff99a4") if is_dark(palette) else QColor("#c42b1c")


def ok_color(palette):
    return QColor("#6ccb5f") if is_dark(palette) else QColor("#0f7b0f")


def app_palette():
    return QApplication.palette()


_THEME_EVENTS = tuple(x for x in (getattr(QEvent.Type, n, None) for n in
                                  ("PaletteChange", "ApplicationPaletteChange", "ThemeChange", "StyleChange"))
                      if x is not None)


def _own_text_palette(w, color):
    """w の WindowText だけを color にする (パレット全体のコピーは持たない)。"""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.WindowText, color)
    w.setPalette(pal)


# ---------------------------------------------------------------------------
# ラベル
# ---------------------------------------------------------------------------

def _fit_wrapped_height(label):
    """折り返しラベルの高さを今の幅に合わせる。入れ子のレイアウトでは heightForWidth が親行まで伝わらず、
    2行目以降が行の下端で切れる/次の行に重なることがあるため、最小高さとして明示する。"""
    if not label.wordWrap() or label.width() <= 0:
        return
    h = label.heightForWidth(label.width())
    if h > 0 and h != label.minimumHeight():
        label.setMinimumHeight(h)


class Hint(QLabel):
    """二次色の小さめの説明文 (折り返しあり)。親のパレットから色を計算するので、テーマ切替に追従する。"""

    _busy = False

    def __init__(self, text="", wrap=True, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(bool(wrap))
        f = self.font()
        f.setPointSizeF(9.0)
        self.setFont(f)
        self._apply()

    def _apply(self):
        if self._busy:
            return
        self._busy = True
        try:
            _own_text_palette(self, secondary_color(ref_palette(self.parentWidget())))
        finally:
            self._busy = False

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS or e.type() == QEvent.Type.ParentChange:
            self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        _fit_wrapped_height(self)

    def setText(self, text):
        super().setText(text)
        _fit_wrapped_height(self)


_ICON_REFRESH = _THEME_EVENTS + tuple(x for x in (getattr(QEvent.Type, "DevicePixelRatioChange", None),) if x is not None)


class ErrorLabel(QLabel):
    """エラー文 (赤系。ライト/ダークで色を切り替える)。空文字のときは隠す。"""

    _busy = False

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        if not text:
            self.setVisible(False)
        self._apply()

    def setText(self, text):
        super().setText(text)
        if self.parentWidget() is not None:
            self.setVisible(bool(text))

    def _apply(self):
        if self._busy:
            return
        self._busy = True
        try:
            _own_text_palette(self, error_color(ref_palette(self.parentWidget())))
        finally:
            self._busy = False

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS or e.type() == QEvent.Type.ParentChange:
            self._apply()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        _fit_wrapped_height(self)


class IconLabel(QLabel):
    """16px の線アイコン。文字色で塗り、パレットが変わったら描き直す。"""

    def __init__(self, name, size=16, parent=None):
        super().__init__(parent)
        self._name = name
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self._render()

    def set_icon(self, name):
        self._name = name
        self._render()

    def _render(self):
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self.setPixmap(icons.pixmap(self._name, self._size, text_color(self), dpr))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _ICON_REFRESH:
            self._render()

    def showEvent(self, e):
        super().showEvent(e)
        self._render()


# ---------------------------------------------------------------------------
# Page / Section / Row
# ---------------------------------------------------------------------------

class Card(QFrame):
    """角丸 + 1px の枠のカード。子 (Row) の間に 1px の区切り線を引く。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)   # 毎回その時点のパレットから計算 (テーマ切替に追従)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border_color(pal), 1))
        p.setBrush(card_color(pal))
        p.drawRoundedRect(r, 5, 5)
        p.setPen(QPen(separator_color(pal), 1))
        lay = self.layout()
        first = True
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is None or not w.isVisible():
                continue
            if not first:
                y = w.geometry().top()
                p.drawLine(1, y, self.width() - 2, y)
            first = False
        p.end()


class Row(QWidget):
    """「アイコン | ラベル (+1行の補足) | 右に操作」の設定行。addControl(widget) で右側に積む。"""

    def __init__(self, icon_name, title, hint=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(12)
        if icon_name:
            self.icon = IconLabel(icon_name)
        else:
            self.icon = QWidget()
            self.icon.setFixedSize(16, 16)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.hint_label = Hint(hint or "")
        if not hint:
            self.hint_label.setVisible(False)
        text.addWidget(self.title_label)
        text.addWidget(self.hint_label)
        lay.addLayout(text, 1)
        self._controls = QHBoxLayout()
        self._controls.setSpacing(8)
        self._controls.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._controls)
        self.setMinimumHeight(48)

    def addControl(self, widget, stretch=0):
        self._controls.addWidget(widget, stretch, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def setTitle(self, text):
        self.title_label.setText(text)

    def setHint(self, text):
        self.hint_label.setText(text or "")
        self.hint_label.setVisible(bool(text))   # Row に載った後なので子ウィジェットとして出し入れされる


class _Padded(QWidget):
    """add_padded の入れ物。中身が隠れたら入れ物ごと隠す (空の余白行を残さない)。"""

    def __init__(self, widget, margins):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*margins)
        lay.addWidget(widget)
        self._inner = widget
        widget.installEventFilter(self)
        self.setVisible(not widget.isHidden())

    def eventFilter(self, obj, e):
        if obj is self._inner and e.type() in (QEvent.Type.HideToParent, QEvent.Type.ShowToParent):
            self.setVisible(e.type() == QEvent.Type.ShowToParent)
        return False


class Section(QWidget):
    """小見出し (Semibold) + その下のカード。add(widget) / add_row(...) でカードへ積む。"""

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.title_label = None
        if title:
            self.title_label = QLabel(title)
            f = self.title_label.font()
            f.setWeight(QFont.Weight.DemiBold)
            self.title_label.setFont(f)
            lay.addWidget(self.title_label)
        self.card = Card()
        lay.addWidget(self.card)

    def add(self, widget):
        self.card.layout().addWidget(widget)
        return widget

    def add_row(self, icon_name, title, hint=None):
        return self.add(Row(icon_name, title, hint))

    def add_padded(self, widget, margins=(16, 8, 16, 8)):
        """Row ではない中身 (グラフやメーター等) をカードへ余白つきで入れる。"""
        box = _Padded(widget, margins)
        return self.add(box)


class Page(QScrollArea):
    """スクロールできる内容列。最大 760 論理px で中央寄せ、左右の余白 24px。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.setWidget(inner)
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(0, 0, 0, 0)
        self._col = QWidget()
        self._col.setMaximumWidth(MAX_CONTENT_W + 2 * PAGE_PAD_X)
        self._col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # 幅は常に (最大幅 or 使える幅いっぱい) にする。alignment を付けると sizeHint 幅に縮むのでスペーサで中央寄せ。
        outer.addStretch(1)
        outer.addWidget(self._col, 1000)
        outer.addStretch(1)
        self.column = QVBoxLayout(self._col)
        self.column.setContentsMargins(PAGE_PAD_X, 16, PAGE_PAD_X, 28)
        self.column.setSpacing(20)

    def content_width(self):
        """内容列の実幅 (左右余白を除く、論理px)。"""
        return max(0, self._col.width() - 2 * PAGE_PAD_X)

    def add(self, widget):
        self.column.addWidget(widget)
        return widget

    def add_layout(self, layout):
        self.column.addLayout(layout)
        return layout

    def add_section(self, title=""):
        return self.add(Section(title))

    def finish(self):
        """最後に1回呼ぶ (下に余白を寄せる)。build() の最後で。"""
        self.column.addStretch(1)
        return self

    def on_activated(self):
        """このページが表示されたとき (ナビで選ばれたとき) に呼ばれる。必要なページが上書きする。"""


# ---------------------------------------------------------------------------
# 入力部品 (ホイールで勝手に変わらない / 設定キーに結び付く)
# ---------------------------------------------------------------------------

class Combo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()   # ページのスクロールに回す


class Slider(QSlider):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class Switch(QAbstractButton):
    """Windows 11 風のオン/オフ (40x20 論理px)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def sizeHint(self):
        return QSize(40, 20)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        on = self.isChecked()
        enabled = self.isEnabled()
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        txt = _role(pal, QPalette.ColorRole.WindowText)
        if on:
            track = _role(pal, QPalette.ColorRole.Highlight)
            knob = _role(pal, QPalette.ColorRole.HighlightedText)
            p.setPen(Qt.PenStyle.NoPen)
        else:
            track = Qt.GlobalColor.transparent
            knob = blend(_role(pal, QPalette.ColorRole.Window), txt, 0.62)
            p.setPen(QPen(blend(_role(pal, QPalette.ColorRole.Window), txt, 0.55), 1))
        if not enabled:
            p.setOpacity(0.4)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = 12.0
        cy = self.height() / 2.0
        cx = (self.width() - 4 - d / 2) if on else (4 + d / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QPointF(cx, cy), d / 2, d / 2)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(txt, 1, Qt.PenStyle.DotLine))
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 10, 10)
        p.end()


def button(text, primary=False, parent=None):
    """ボタン。primary は既定ボタン (Windows 11 スタイルではアクセント色)。"""
    b = QPushButton(text, parent)
    if primary:
        b.setDefault(True)
        b.setAutoDefault(True)
    b.setMinimumWidth(88)
    b.setMinimumHeight(30)
    return b


def _listen(ctrl, key, fn):
    def cb(k, v):
        if k == key:
            fn(v)
    ctrl.settings_changed.connect(cb)


def _norm_items(items):
    out = []
    for it in items:
        if isinstance(it, (tuple, list)) and len(it) == 2:
            out.append((it[0], str(it[1])))
        else:
            out.append((it, str(it)))
    return out


def bound_combo(ctrl, key, items, group=None, width=None, on_change=None, setter=None):
    """設定 key に結び付いたコンボボックス。items: [(値, 表示)] か [値]。選んだら ctrl.changed(key, 値, group)
    (setter(値) を渡すとそれを呼ぶ。例: ctrl.set_run_mode)。
    設定側が変わったら表示も合わせる (signal は出さない)。"""
    cb = Combo()
    pairs = _norm_items(items)
    for v, label in pairs:
        cb.addItem(label, v)
    if width:
        cb.setMinimumWidth(int(width))

    def show(v):
        i = cb.findData(v)
        if i >= 0 and i != cb.currentIndex():
            cb.setCurrentIndex(i)

    show(ctrl.get(key))

    def picked(i):
        v = cb.itemData(i)
        if setter is not None:
            setter(v)
        else:
            ctrl.changed(key, v, group)
        if on_change:
            on_change(v)

    cb.activated.connect(picked)
    _listen(ctrl, key, show)
    cb.sync = lambda: show(ctrl.get(key))
    return cb


def bound_switch(ctrl, key, group=None, on_change=None, invert=False, setter=None):
    """設定 key (bool) に結び付いたスイッチ。"""
    sw = Switch()

    def show(v):
        want = bool(v) != bool(invert)
        if sw.isChecked() != want:
            sw.blockSignals(True)
            sw.setChecked(want)
            sw.blockSignals(False)
            sw.update()

    show(ctrl.get(key))

    def toggled(on):
        v = bool(on) != bool(invert)
        if setter is None:
            ctrl.changed(key, v, group)
        else:
            setter(v)
        if on_change:
            on_change(v)

    sw.toggled.connect(toggled)
    _listen(ctrl, key, show)
    return sw


def bound_check(ctrl, key, text="", group=None, on_change=None):
    """設定 key (bool) に結び付いたチェックボックス。"""
    cb = QCheckBox(text)

    def show(v):
        if cb.isChecked() != bool(v):
            cb.blockSignals(True)
            cb.setChecked(bool(v))
            cb.blockSignals(False)

    show(ctrl.get(key))

    def toggled(on):
        ctrl.changed(key, bool(on), group)
        if on_change:
            on_change(bool(on))

    cb.toggled.connect(toggled)
    _listen(ctrl, key, show)
    return cb


def bound_line(ctrl, key, width=160, placeholder="", on_change=None):
    """設定 key (文字列) に結び付いた1行入力。打つたびに ctrl.changed(key, text)。"""
    le = QLineEdit()
    le.setMinimumWidth(int(width))
    le.setPlaceholderText(placeholder)
    le.setText(str(ctrl.get(key, "") or ""))

    def show(v):
        if le.text() != str(v):
            le.blockSignals(True)
            le.setText(str(v))
            le.blockSignals(False)

    def edited(text):
        ctrl.changed(key, text, None)
        if on_change:
            on_change(text)

    le.textEdited.connect(edited)
    _listen(ctrl, key, show)
    return le


class SliderValue(QWidget):
    """スライダー + 右に値表示。.slider / .value_label。"""

    def __init__(self, ctrl, key, lo, hi, step, fmt, group=None, width=180, as_int=False):
        super().__init__()
        self._ctrl, self._key, self._lo, self._step, self._fmt = ctrl, key, float(lo), float(step), fmt
        self._as_int = as_int
        self._group = group
        n = max(1, int(round((float(hi) - float(lo)) / float(step))))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.slider = Slider()
        self.slider.setRange(0, n)
        self.slider.setFixedWidth(int(width))
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        wmax = max(self.value_label.fontMetrics().horizontalAdvance(fmt(v)) for v in (lo, hi)) + 6
        self.value_label.setMinimumWidth(max(wmax, 64))
        lay.addWidget(self.slider)
        lay.addWidget(self.value_label)
        self._show(ctrl.get(key))
        self.slider.valueChanged.connect(self._moved)
        _listen(ctrl, key, self._show)

    def set_bounds(self, lo, hi):
        steps = max(0, int((hi - lo) / self._step + 1e-9))
        if lo == self._lo and steps == self.slider.maximum():
            return
        self._lo = lo
        previous = self.slider.blockSignals(True)
        try:
            self.slider.setRange(0, steps)
            self._show(self._ctrl.get(self._key))
        finally:
            self.slider.blockSignals(previous)

    def _value_of(self, i):
        v = self._lo + i * self._step
        return int(round(v)) if self._as_int else round(v, 4)

    def _show(self, v):
        try:
            v = float(v)
        except Exception:
            return
        i = int(round((v - self._lo) / self._step))
        i = max(self.slider.minimum(), min(self.slider.maximum(), i))
        if i != self.slider.value():
            previous = self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(previous)
        self.value_label.setText(self._fmt(self._value_of(i)))

    def _moved(self, i):
        v = self._value_of(i)
        self.value_label.setText(self._fmt(v))
        self._ctrl.changed(self._key, v, self._group)


def bound_slider(ctrl, key, lo, hi, step, fmt=None, group=None, width=180, as_int=False):
    """設定 key (数値) に結び付いたスライダー + 値表示。fmt(v) -> 文字列 (省略は str)。"""
    return SliderValue(ctrl, key, lo, hi, step, fmt or (lambda v: str(v)), group=group, width=width, as_int=as_int)
