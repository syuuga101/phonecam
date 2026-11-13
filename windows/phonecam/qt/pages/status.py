"""Phone and PC diagnostics, latency and receive history."""

import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from phonecam.core.canvas import canvas_size
from phonecam.core.decoder import THIN_NOTICE_SEC
from phonecam.core.stats import GRAPH_RANGES, THERMAL_LEVELS, format_mmss, graph_window, range_label
from phonecam.i18n import t
from phonecam.qt.widgets import Combo, Hint, Page, button, error_color, secondary_color, separator_color
from phonecam.ui.labels import CODEC_LABELS, DECODER_LABELS, THERMAL_LABELS, THIN_TEXT


def _number(value, spec):
    return format(value, spec) if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


class _History(QWidget):
    def __init__(self, log):
        super().__init__()
        self.log = log
        self.minutes = GRAPH_RANGES[0]
        self.setMinimumHeight(206)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.update)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        window = graph_window(self.log.samples, self.minutes * 60)
        left = max(self.fontMetrics().horizontalAdvance(t(f"status.graph.{level}")) for level in THERMAL_LEVELS) + 12
        right = max(left + 1, self.width() - 30)
        x_step = (right - left) / max(1, len(window) - 1)
        for index, (name, limit, baseline, height, color) in enumerate((
            ("thermal", 3, 94, 48, error_color(palette)),
            ("fps", 60, 178, 48, palette.color(QPalette.ColorRole.Highlight)),
        )):
            painter.setPen(secondary_color(palette))
            painter.drawText(0, baseline - height - 10, t(f"status.graph.{name}"))
            ticks = range(4) if index == 0 else (0, 30, 60)
            for tick in ticks:
                y = round(baseline - height * tick / limit)
                painter.setPen(secondary_color(palette))
                label = t(f"status.graph.{THERMAL_LEVELS[tick]}") if index == 0 else str(tick)
                painter.drawText(0, y + 4, label)
                painter.setPen(separator_color(palette))
                painter.drawLine(left, y, right, y)
            painter.setPen(color)
            previous = None
            for i, (level, fps) in enumerate(window):
                value = level if index == 0 else fps if fps >= .5 else None
                if value is None:
                    previous = None
                    continue
                point = QPointF(left + i * x_step, baseline - height * min(limit, value) / limit)
                if previous is None:
                    painter.drawPoint(point)
                else:
                    painter.drawLine(previous, point)
                previous = point
        painter.setPen(secondary_color(palette))
        painter.drawText(left, 199, range_label(self.minutes))
        now = t("status.graph.now")
        painter.drawText(right - self.fontMetrics().horizontalAdvance(now), 199, now)


def _open_timer(page):
    dialog = QDialog(page, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle(t("status.timer.title"))
    layout = QVBoxLayout(dialog)
    label = QLabel()
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = label.font()
    font.setFamily("Consolas")
    font.setPointSize(72)
    font.setBold(True)
    label.setFont(font)
    layout.addWidget(label)
    layout.addWidget(Hint(t("status.timer.help")))
    start = time.perf_counter()

    def tick():
        ms = int((time.perf_counter() - start) * 1000)
        label.setText(f"{ms // 60000 % 100:02}:{ms // 1000 % 60:02}.{ms % 1000:03}")

    timer = QTimer(dialog, interval=10)
    timer.timeout.connect(tick)
    tick()
    timer.start()
    dialog.show()
    return dialog


def build(ctrl):
    page = Page()
    rows = {}
    sections = (
        ("iphone", (("thermal", "thermometer"), ("pressure", "gauge"), ("factors", "info"),
                    ("hot", "clock"), ("battery", "battery"), ("lowpower", "battery"))),
        ("video", (("sentfps", "video"), ("dropped", "video"), ("dropreasons", "info"),
                   ("recdropped", "record"), ("encode", "clock"), ("processing", "sparkles"))),
        ("pc", (("codec", "video"), ("decoder", "gear"), ("out", "aspect"), ("watch", "camera"), ("dshow", "camera"))),
        ("latency", (("iphone", "camera"), ("comm", "wifi"), ("pc", "gear"), ("total", "clock"), ("rtt", "clock"))),
    )
    for name, specs in sections:
        card = page.add_section(t(f"status.{name}.title"))
        for key, icon in specs:
            rows[name, key] = card.add_row(icon, t(f"status.{name}.{key}"))
        if name == "pc":
            thin = Hint("")
            card.add_padded(thin)
        if name in ("video", "pc", "latency"):
            hint = "watch_hint" if name == "pc" else "hint"
            card.add_padded(Hint(t(f"status.{name}.{hint}")))

    diag = page.add_section(t("status.diag.title"))
    open_timer = button(t("status.diag.timer_open"))
    diag.add_row("clock", t("status.diag.timer"), t("status.diag.timer_hint")).addControl(open_timer)
    timer_window = None

    def forget_timer():
        nonlocal timer_window
        timer_window = None

    def show_timer():
        nonlocal timer_window
        if timer_window is None:
            timer_window = _open_timer(page)
            timer_window.destroyed.connect(forget_timer)
        else:
            timer_window.raise_()
            timer_window.activateWindow()

    open_timer.clicked.connect(show_timer)
    graph = page.add_section(t("status.graph.title"))
    history = _History(ctrl.thermal_log)
    period = Combo()
    for value in GRAPH_RANGES:
        period.addItem(t("status.graph.minutes", n=value), value)
    current = graph.add_row("clock", t("status.graph.thermal"))
    current.addControl(period)

    def set_period(index):
        history.minutes = period.itemData(index)
        history.update()

    period.activated.connect(set_period)
    graph.add_padded(history)

    def refresh(status):
        phone = status.phone
        thinning = status.connected and status.thin_total and time.monotonic() - status.thin_last < THIN_NOTICE_SEC
        thin.setText(t("status.pc.thin", text=THIN_TEXT, n=status.thin_total) if thinning else "")
        thin.setVisible(bool(thinning))
        snap = ctrl.state.snapshot()
        rec = phone.get("recording")
        cam, net = phone.get("cameraDroppedFrames"), phone.get("networkDroppedFrames")
        dropped = (t("status.video.dropped_fmt", cam=cam, net=net)
                   if isinstance(cam, int) and isinstance(net, int) else "-")
        reasons = phone.get("cameraDropReasons")
        reasons = reasons if isinstance(reasons, dict) else {}
        drops = " / ".join(t(f"status.video.drop.{key}", count=reasons.get(key, "-"))
                           for key in ("late", "outOfBuffers", "discontinuity")) if reasons else "-"
        pressure = phone.get("pressureLevel")
        pressure_text = (t("status.iphone.pressure_shutdown") if pressure == "shutdown"
                         else THERMAL_LABELS.get(pressure, "-"))
        factors = phone.get("pressureFactors")
        factor_names = {"systemTemperature", "peakPower", "depthModuleTemperature", "cameraTemperature"}
        factor_text = ", ".join(t(f"status.iphone.factor.{f}") if f in factor_names else f
                                for f in factors if isinstance(f, str)) if isinstance(factors, list) else ""
        low = phone.get("lowPowerMode")
        codec = snap["codec_in_use"] or phone.get("codec")
        decoder = DECODER_LABELS.get(snap["decoder"], "-") if status.connected else "-"
        if status.connected and snap.get("decoder_note"):
            decoder += t("status.pc.note", v=snap["decoder_note"])
        size = canvas_size(ctrl.settings)
        output = f"{size[0]}×{size[1]}" if size else t("status.pc.out_native")
        data = {
            "iphone": (status.thermal_text, pressure_text, factor_text or "-",
                       format_mmss(ctrl.thermal_log.hot_seconds) if status.connected else "-", status.battery_text,
                       "-" if low is None else t("status.iphone.on" if low else "status.iphone.off")),
            "video": (_number(phone.get("sentFps"), ".1f"), dropped, drops,
                      _number(rec.get("droppedFrames"), "d") if isinstance(rec, dict) and isinstance(rec.get("droppedFrames"), int) else "-",
                      _number(phone.get("encodeMs"), ".1f") + " ms" if isinstance(phone.get("encodeMs"), (float, int)) else "-",
                      _number(phone.get("processingMs"), ".1f") + " ms" if isinstance(phone.get("processingMs"), (float, int)) else "-"),
            "pc": (dict(CODEC_LABELS).get(codec, "-") if status.connected else "-", decoder, output, status.watch_text,
                   status.dshow_text),
        }
        for section, specs in sections[:-1]:
            for (key, _), value in zip(specs, data[section]):
                rows[section, key].setHint(value or "-")
        latency = ctrl.link.latency.averages() if status.connected else {}
        for key, _ in sections[-1][1]:
            value = latency.get(key)
            rows["latency", key].setHint(status.latency_text if key == "total" else f"{value:.1f} ms" if value is not None else "-")
        current.setHint(t("status.graph.current", level=status.thermal_text or "-",
                          hot=format_mmss(ctrl.thermal_log.hot_seconds)))

    ctrl.status_changed.connect(refresh)
    ctrl.settings_changed.connect(lambda *_: refresh(ctrl.status))
    refresh(ctrl.status)
    return page.finish()
