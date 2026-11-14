"""iPhone microphone, Windows audio output and input meter."""

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QWidget

from phonecam.core.audio_engine import SYSTEM_DEFAULT_KEY, find_vbcable_control_panel, list_output_devices
from phonecam.core.settings import AUDIO_AVAILABLE
from phonecam.i18n import NO_VALUE, t
from phonecam.qt.widgets import Card, Combo, Page, Row, bound_combo, bound_slider, bound_switch, button, error_color, separator_color
from phonecam.labels import AUDIO_PATH_LABELS, METER_MIN_DB, MIC_PATH_LABELS, phone_mic_text


class _Meter(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.db = METER_MIN_DB
        self.setMinimumSize(280, 26)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._sample)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _sample(self):
        self.db = self.ctrl.meter_db()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = self.palette()
        width = max(0, self.width() - 84)
        bar = QRectF(0, (self.height() - 8) / 2, width, 8)
        painter.fillRect(bar, separator_color(palette))
        bar.setWidth(width * min(1, max(0, (self.db - METER_MIN_DB) / -METER_MIN_DB)))
        hot = self.db >= -3   # Tk 版と同じ: -3dB 以上は警告色
        painter.fillRect(bar, error_color(palette) if hot else palette.color(QPalette.ColorRole.Highlight))
        painter.setPen(palette.color(QPalette.ColorRole.WindowText))
        painter.drawText(width + 8, int((self.height() + self.fontMetrics().ascent()) / 2) - 1, f"{self.db:.0f} dBFS")


def _build_source(page, ctrl):
    """「音声を受け取るカメラ」(2 台以上のときだけ)。項目は「カメラ 1 · iPhone 16 Pro」。"""
    group = getattr(ctrl, "camera_group", None)
    if group is None:
        return
    card = Card()
    row = Row("mic", t("multi.audio_source"), t("multi.audio_hint"))
    combo = Combo()
    combo.setMinimumWidth(230)
    combo.setAccessibleName(t("multi.audio_source"))
    row.addControl(combo)
    card.layout().addWidget(row)
    page.add(card)

    def refresh(*_):
        combo.blockSignals(True)
        combo.clear()
        for index, camera in enumerate(group.cameras):
            combo.addItem(group.camera_choice(camera), index)
        combo.setCurrentIndex(group.audio_index)
        combo.blockSignals(False)
        card.setVisible(len(group.cameras) > 1)

    combo.activated.connect(lambda index: group.select_audio(combo.itemData(index)))
    group.changed.connect(refresh)
    group.audio_changed.connect(refresh)
    page.on_activated = refresh      # iPhone 名は接続したあとに分かることがあるので、開くたびに作り直す
    refresh()


def build(ctrl):
    page = Page()
    _build_source(page, ctrl)

    def apply(_):
        ctrl.apply_audio()

    mic = page.add_section(t("audio.mic.section"))
    enabled = bound_switch(ctrl, "mic_enabled", group="mic", on_change=apply)
    mic.add_row("mic", t("audio.mic.use")).addControl(enabled)
    mic.add_row("volume", t("audio.mute.label"), t("audio.mute.hint")).addControl(bound_switch(ctrl, "mic_mute"))
    phone_row = mic.add_row("mic", t("audio.phonemic.label"))   # iPhone 側のミュート状態 (読み取り専用)

    output = page.add_section(t("audio.output.section"))
    try:
        devices = list_output_devices() if AUDIO_AVAILABLE and not ctrl.demo else []
    except Exception:
        devices = []
    choices = [(SYSTEM_DEFAULT_KEY, t("audio.device.default"))]
    choices.extend((name, name) for name in dict.fromkeys(d["name"] for d in devices))
    saved = ctrl.get("mic_device")
    if saved and all(saved != value for value, _ in choices):
        choices.append((saved, saved))
    device = bound_combo(ctrl, "mic_device", choices, on_change=apply)
    output.add_row("speaker", t("audio.device.label")).addControl(device)
    volume = bound_slider(ctrl, "mic_volume", 0, 200, 5, lambda v: f"{v}%", as_int=True)
    output.add_row("volume", t("audio.volume.label")).addControl(volume)
    delay = bound_slider(ctrl, "mic_delay_ms", 0, 500, 10, lambda v: f"{v} ms", as_int=True)
    output.add_row("clock", t("audio.delay.label"), t("audio.delay.hint")).addControl(delay)
    priority = bound_combo(ctrl, "mic_priority", [(v, t(f"audio.priority.{v}")) for v in ("low", "stable")], on_change=apply)
    output.add_row("gauge", t("audio.priority.label")).addControl(priority)
    exclusive = bound_combo(ctrl, "mic_exclusive", [(None, t("labels.mode3.auto")), (True, t("status.iphone.on")),
                                                     (False, t("status.iphone.off"))], on_change=apply)
    output.add_row("gauge", t("audio.exclusive.label"), t("audio.exclusive.hint")).addControl(exclusive)
    for widget in (enabled, device, volume, delay, priority, exclusive):
        widget.setEnabled(AUDIO_AVAILABLE or ctrl.demo)

    level = page.add_section(t("audio.level.section"))
    level.add_row("mic", t("audio.level.label")).addControl(_Meter(ctrl))

    status_card = page.add_section(t("audio.status.section"))
    rows = {}
    for name, icon in (("info", "info"), ("error", "info"), ("path", "link"), ("micpath", "mic"),
                       ("mode", "play"), ("latency", "clock"), ("under", "info")):
        rows[name] = status_card.add_row(icon, t(f"audio.{name}.label"))

    cable = page.add_section(t("audio.vbcable.section"))
    panel = button(t("audio.vbcable.open"))
    panel.setEnabled(bool(find_vbcable_control_panel()) and not ctrl.demo)
    panel.clicked.connect(ctrl.open_vbcable_panel)
    cable.add_row("wrench", t("audio.vbcable.label"), t("audio.vbcable.hint")).addControl(panel)

    def refresh(status):
        audio = ctrl.audio
        mic = status.phone.get("mic")
        mic = mic if isinstance(mic, dict) else {}
        snapshot = ctrl.state.snapshot()
        on = ctrl.get("mic_enabled")
        phone_row.setHint(phone_mic_text(status.connected, status.phone_muted))
        info = t("shell.sync.output", name=audio.device_name) if audio.running and on and not audio.error else ""
        if info and mic.get("sampleRate"):
            info = t("shell.sync.output_detail", name=audio.device_name, hz=int(mic["sampleRate"]),
                     buf=round(audio.buffered_ms()), target=round(audio.target_ms()))
        error = audio.error if on else ""
        if not AUDIO_AVAILABLE:
            error = t("audio.error.nolib")
        elif on and status.connected and mic.get("available") is False:
            error = error or t("shell.sync.mic_unavailable")
        path = AUDIO_PATH_LABELS.get(snapshot.get("audio_path"), NO_VALUE) if status.connected else NO_VALUE
        mic_path = MIC_PATH_LABELS.get(status.phone.get("micPath") or mic.get("micPath"), NO_VALUE) if on else NO_VALUE
        mode = t("shell.sync.excl" if audio.mode == "exclusive" else "shell.sync.shared",
                 d=audio.mode_detail) if audio.running else ""
        latency = audio.latency_ms() if audio.running and status.connected else None
        latency_text = f"{latency:.0f} ms" if latency is not None else NO_VALUE
        under = t("shell.sync.underruns", n=audio.underruns) if audio.running else ""
        for key, value in dict(info=info, error=error, path=path, micpath=mic_path,
                               mode="\n".join(filter(None, (audio.notice, mode))) or NO_VALUE,
                               latency=t("audio.latency.value_with_hint", latency=latency_text, hint=t("audio.latency.hint")), under=under).items():
            rows[key].setHint(value)
            if key in ("info", "error", "under"):
                rows[key].setVisible(bool(value))   # 空のときは行ごと隠す (見出しだけの空行を残さない)

    ctrl.status_changed.connect(refresh)
    ctrl.settings_changed.connect(lambda *_: refresh(ctrl.status))
    refresh(ctrl.status)
    return page.finish()
