"""Arrange the most used settings above the page's collapsible groups."""

from functools import partial

from PySide6.QtWidgets import QCheckBox, QComboBox, QKeySequenceEdit, QLabel, QLineEdit

from phonecam.core.settings import COLOR_DEFAULTS
from phonecam.i18n import t
from phonecam.qt.widgets import NO_VALUE, Expander, Row, Section, SliderValue, StatusValue, Switch


PRIMARY = {
    "connection": ("connection.run.mode",),
    "video": ("video.resolution.label", "video.fps.label", "video.orient.label"),
    "quality": ("quality.zoom.label", "quality.exposure.bias.label", "quality.focus.mode.label", "quality.light.label"),
    "effects": ("effects.background.mode.label", "effects.background.strength.label", "effects.color.brightness.label"),
    "audio": ("audio.mic.use", "audio.mute.label", "audio.device.label", "audio.volume.label"),
    "recording": ("recording.button.label", "recording.preset.label", "recording.resolution.label"),
    "status": ("status.iphone.thermal", "status.iphone.battery", "status.video.sentfps"),
    "shortcuts": ("shortcuts.enable.label", "shortcuts.action.camera", "shortcuts.action.mic"),
    "settings": ("settings.language.label", "settings.theme.label", "update.check.label"),
}

# Section title, icon, description (what you can learn / do inside), and the summary shown at the right of the header.
# A summary function returns short phrases that say what the value is ("auto-stops after 5 min", not just "5 min");
# it returns nothing when there is no meaningful value, and then nothing is shown.
GROUPS = {
    "connection": (
        ("connection.wifi.title", "wifi", "compact.desc.wifi", None),
        ("connection.run.title", "play", "compact.desc.run", "run"),
        ("multi.manage", "camera", "compact.desc.camera", "camera"),
    ),
    "video": (
        ("compact.video.input", "lens", "compact.desc.video_input", "video_input"),
        ("compact.video.encoding", "video", "compact.desc.video_encoding", "video_encoding"),
        ("video.orient.title", "rotate", "compact.desc.video_orient", "video_orient"),
        ("video.vcam.title", "camera", "compact.desc.video_vcam", "video_vcam"),
        ("video.power.title", "light", "compact.desc.video_power", "video_power"),
    ),
    "quality": (
        ("quality.adaptive.title", "wifi", "compact.desc.quality_adaptive", "quality_adaptive"),
        ("quality.exposure.title", "sun", "compact.desc.quality_exposure", "quality_exposure"),
        ("quality.wb.title", "droplet", "compact.desc.quality_wb", "quality_wb"),
        ("quality.focus.title", "focus", "compact.desc.quality_focus", "quality_focus"),
    ),
    "effects": (
        ("effects.background.title", "person", "compact.desc.effects_background", "effects_background"),
        ("effects.color.title", "palette", "compact.desc.effects_color", "effects_color"),
    ),
    "audio": (
        ("audio.mic.section", "mic", "compact.desc.audio_mic", "audio_mic"),
        ("audio.output.section", "speaker", "compact.desc.audio_output", "audio_output"),
        ("audio.level.section", "gauge", "compact.desc.audio_level", None),
        ("audio.status.section", "info", "compact.desc.audio_status", None),
        ("audio.vbcable.section", "wrench", "compact.desc.audio_vbcable", None),
    ),
    "recording": (
        ("recording.control.section", "record", "compact.desc.rec_control", "rec_control"),
        ("recording.quality.section", "video", "compact.desc.rec_quality", "rec_quality"),
        ("recording.save.section", "folder", "compact.desc.rec_save", "rec_save"),
    ),
    "status": (
        ("status.iphone.title", "battery", "compact.desc.status_iphone", "status_iphone"),
        ("status.video.title", "video", "compact.desc.status_video", "status_video"),
        ("status.pc.title", "camera", "compact.desc.status_pc", "status_pc"),
        ("status.latency.title", "wifi", "compact.desc.status_latency", "status_latency"),
        ("status.diag.title", "wrench", "compact.desc.status_diag", None),
        ("status.graph.title", "gauge", "compact.desc.status_graph", "status_graph"),
    ),
    "shortcuts": (
        ("compact.shortcuts.capture", "camera", "compact.desc.keys_capture", "keys"),
        ("compact.shortcuts.adjust", "zoom", "compact.desc.keys_adjust", "keys"),
    ),
    "settings": (
        ("settings.general.title", "gauge", "compact.desc.settings_general", "settings_general"),
        ("update.section", "download", "compact.desc.settings_update", "settings_update"),
        ("settings.setup.title", "wrench", "compact.desc.settings_setup", None),
        ("settings.about.title", "info", "compact.desc.settings_about", "settings_about"),
    ),
}


def _row_value(row):
    label = row.title_label.text()
    controls = tuple(row.controls())
    for control in controls:
        if isinstance(control, QComboBox):
            value = control.currentText()
            if label == t("video.fps.label"):
                return f"{value}fps"
            if label == t("video.outsize.label") and "×" in value:
                return value.split(maxsplit=1)[0]
            return value
    for control in controls:
        if isinstance(control, SliderValue):
            return control.value_label.text()
        if isinstance(control, (Switch, QCheckBox)):
            return t("status.iphone.on" if control.isChecked() else "status.iphone.off")
        if isinstance(control, QLineEdit):
            # Do not display the pairing code in a collapsed card.
            if label != t("connection.wifi.code"):
                return control.text()
        if isinstance(control, QKeySequenceEdit):
            return control.keySequence().toString()
        if isinstance(control, (QLabel, StatusValue)) and control.text() not in ("", "-", NO_VALUE):
            return control.text()
    return ""


class _Values:
    """One group's rows. Looks a row up in the group first, then in the whole page (rows moved to "common settings").
    Rows are matched by (section, label) because two sections can use the same label (for example "Temperature")."""

    def __init__(self, ctrl, own, first):
        self.ctrl, self.own, self._first = ctrl, own, first

    def row(self, key):
        label = t(key)
        return self.own.get(label) or self._first.get(label)

    def text(self, key):
        row = self.row(key)
        return _row_value(row) if row is not None else ""

    def on(self, key):
        row = self.row(key)
        for control in row.controls() if row is not None else ():
            if isinstance(control, (Switch, QCheckBox)):
                return control.isChecked()
        return None

    def get(self, key, default=None):
        return self.ctrl.get(key, default)


def _head(text):
    """"MP4 (recommended)" -> "MP4": the part before the explanation in parentheses."""
    return text.split("（")[0].split(" (")[0].strip()


def _labeled(key, value):
    return t(key, v=value) if value else ""


def _run(v):
    mode = v.get("run_mode", "auto")
    parts = [t("compact.sum.run." + mode) if mode in ("auto", "always", "manual") else v.text("connection.run.mode")]
    row = v.row("connection.run.idle")
    combo = next((c for c in row.controls() if isinstance(c, QComboBox)), None) if row is not None else None
    if combo is not None and combo.isEnabled():
        parts.append(t("compact.sum.idle.never") if v.get("idle_stop_sec") == 0
                     else _labeled("compact.sum.idle", combo.currentText()))
    return parts


def _color_changed(v):
    try:
        return any(abs(float(v.get(name, default)) - float(default)) > 1e-6 for name, default in COLOR_DEFAULTS.items())
    except (TypeError, ValueError):
        return False


def _keys(v):
    editors = [c for row in v.own.values() for c in row.controls() if isinstance(c, QKeySequenceEdit)]
    if not editors:
        return []
    return [t("compact.sum.keys", n=sum(1 for e in editors if not e.keySequence().isEmpty()), total=len(editors))]


SUMMARIES = {
    "run": _run,
    "camera": lambda v: [v.text("multi.this_camera")],
    "video_input": lambda v: [v.text("video.lens.label"),
                              _labeled("compact.sum.send", _head(v.text("video.resolution.label"))),
                              v.text("video.fps.label")],
    "video_encoding": lambda v: [v.text("video.codec.label"), v.text("video.bitrate.label")],
    "video_orient": lambda v: [_labeled("compact.sum.orient", v.text("video.orient.label")),
                               t("video.mirror.label") if v.on("video.mirror.label") else "",
                               t("video.rotate180.label") if v.on("video.rotate180.label") else ""],
    "video_vcam": lambda v: [v.text("video.vcam.label")],
    "video_power": lambda v: [t("compact.sum.power.black") if v.on("video.power.black")
                              else t("compact.sum.power.auto") if v.get("screen_auto")
                              else t("compact.sum.power.off")],
    "quality_adaptive": lambda v: [t("compact.sum.adaptive.on") if v.on("quality.adaptive.label")
                                   else t("compact.sum.adaptive.off")],
    "quality_exposure": lambda v: [_labeled("compact.sum.exposure", v.text("quality.exposure.mode.label")),
                                   _labeled("compact.sum.bias", v.text("quality.exposure.bias.label"))
                                   if v.get("exp_mode") == "auto" else ""],
    "quality_wb": lambda v: [_labeled("compact.sum.wb", v.text("quality.wb.mode.label"))],
    "quality_focus": lambda v: [_labeled("compact.sum.focus", v.text("quality.focus.mode.label"))],
    "effects_background": lambda v: ([t("compact.sum.bg.off")] if v.get("bg_mode") == "off" else
                                     [v.text("effects.background.mode.label"),
                                      _labeled("compact.sum.strength", v.text("effects.background.strength.label"))]),
    "effects_color": lambda v: [t("compact.sum.color.changed" if _color_changed(v) else "compact.sum.color.default")],
    "audio_mic": lambda v: ([t("compact.sum.mic.off")] if v.on("audio.mic.use") is False else
                            [t("compact.sum.mic.muted" if v.on("audio.mute.label") else "compact.sum.mic.on"),
                             t("compact.sum.mic.pcmuted") if v.on("audio.pcmute.label") else ""]),
    "audio_output": lambda v: [v.text("audio.device.label"), _labeled("compact.sum.volume", v.text("audio.volume.label"))],
    "rec_control": lambda v: [v.text("recording.state.label")],
    "rec_quality": lambda v: [v.text("recording.preset.label"),
                              "4K" if v.text("recording.resolution.label") == "4K" else "",
                              _head(v.text("recording.container.label"))],
    "rec_save": lambda v: [t("compact.sum.rec.audio" if v.on("recording.audio.label") else "compact.sum.rec.noaudio"),
                           t("compact.sum.rec.photos") if v.on("recording.photos.label") else ""],
    "status_iphone": lambda v: [_labeled("compact.sum.thermal", v.text("status.iphone.thermal")),
                                _labeled("compact.sum.battery", v.text("status.iphone.battery"))],
    "status_video": lambda v: [v.text("status.video.sentfps")],
    "status_pc": lambda v: [_labeled("compact.sum.out", v.text("status.pc.out")),
                            _labeled("compact.sum.decoder", v.text("status.pc.decoder"))],
    "status_latency": lambda v: [_labeled("compact.sum.total", v.text("status.latency.total"))],
    "status_graph": lambda v: [_labeled("compact.sum.graph", v.text("status.graph.thermal"))],
    "keys": _keys,
    "settings_general": lambda v: [_labeled("compact.sum.preview", v.text("settings.preview_fps.label"))],
    "settings_update": lambda v: [_labeled("compact.sum.version", v.text("update.version.label"))],
    "settings_about": lambda v: [_labeled("compact.sum.license", v.text("settings.about.license"))],
}


def _set_summary(expander, values, summary):
    try:
        parts = summary(values)
    except Exception:   # 要約の失敗で画面を止めない。要約なしにする
        parts = []
    expander.set_summary(" · ".join(part for part in parts if part))


def compact_page(page, ctrl, key):
    """Move the existing widgets without rebuilding their controls or callbacks."""
    wanted = [t(name) for name in PRIMARY[key]]
    selected = {}
    first = {}      # label -> the first row with that label on the page
    section_rows = {}   # section title -> {label: row}
    groups = {}
    extra = []
    while page.column.count():
        item = page.column.takeAt(0)
        widget = item.widget()
        if widget is None:
            continue
        if not isinstance(widget, Section):
            extra.append((widget, item.alignment(), widget.isHidden()))
            continue
        title = widget.title_label.text()
        children = groups.setdefault(title, [])
        own = section_rows.setdefault(title, {})
        layout = widget.card.layout()
        while layout.count():
            child = layout.takeAt(0).widget()
            if isinstance(child, Row):
                label = child.title_label.text()
                own.setdefault(label, child)
                first.setdefault(label, child)
                if label in wanted and label not in selected:
                    selected[label] = child
                    continue
            children.append((child, child.isHidden()))
        widget.hide()
        widget.deleteLater()

    for widget, alignment, hidden in extra:
        if not isinstance(widget, Section):
            page.column.addWidget(widget, 0, alignment)
            widget.setVisible(not hidden)

    primary = Section(t("compact.primary"))
    for label in wanted:
        if label in selected:
            row = selected[label]
            hint = row.hint_label.text()
            if hint and len(hint) > 40:
                row.setToolTip(hint)
                row.setHint("")
            primary.add(row)
    page.column.addWidget(primary)
    for widget, alignment, hidden in extra:
        if isinstance(widget, Section):
            page.column.addWidget(widget, 0, alignment)
            widget.setVisible(not hidden)

    for title_key, icon, desc_key, summary_name in GROUPS[key]:
        title = t(title_key)
        children = groups.pop(title, [])
        if not children:
            continue
        group_key = f"{key}.{title_key}"
        expander = Expander(ctrl, group_key, icon, title, t(desc_key))
        for child, hidden in children:
            expander.add(child)
            child.setVisible(not hidden)
        page.column.addWidget(expander)
        if title_key == "connection.wifi.title" and hasattr(page, "wireless"):
            page.wireless.bind_expander(expander)   # 要約は Wi-Fi 側が「接続中 · 名前」などを出す
            continue
        if summary_name is None:
            continue
        values = _Values(ctrl, section_rows.get(title, {}), first)
        refresh = partial(_set_summary, expander, values, SUMMARIES[summary_name])
        for row in values.own.values():
            for control in row.controls():
                if isinstance(control, QComboBox):
                    control.currentTextChanged.connect(lambda *_args, fn=refresh: fn())
                elif isinstance(control, QLineEdit):
                    control.textChanged.connect(lambda *_args, fn=refresh: fn())
                elif isinstance(control, QKeySequenceEdit):
                    control.keySequenceChanged.connect(lambda *_args, fn=refresh: fn())
        expander.header.clicked.connect(lambda *_args, fn=refresh: fn())
        ctrl.settings_changed.connect(lambda *_args, fn=refresh: fn())
        ctrl.status_changed.connect(lambda *_args, fn=refresh: fn())
        if title_key == "video.vcam.title":
            ctrl.camera_registration_changed.connect(refresh)
        refresh()

    # Keep any future sections accessible even before they get a group definition.
    for title, children in groups.items():
        if children:
            section = Section(title)
            for child, hidden in children:
                section.add(child)
                child.setVisible(not hidden)
            page.column.addWidget(section)
    page.column.addStretch(1)
    return page
