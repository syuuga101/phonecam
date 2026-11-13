"""Arrange the most used settings above the page's collapsible groups."""

from functools import partial

from PySide6.QtWidgets import QCheckBox, QComboBox, QKeySequenceEdit, QLabel, QLineEdit

from phonecam.i18n import t
from phonecam.qt.widgets import Expander, Row, Section, SliderValue, StatusValue, Switch


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

# Section title, icon, and (where useful) the rows to summarize, in display order.
GROUPS = {
    "connection": (
        ("connection.wifi.title", "wifi", ()),
        ("connection.run.title", "play", ("connection.run.mode", "connection.run.idle")),
    ),
    "video": (
        ("compact.video.input", "lens", ("video.lens.label", "video.outsize.label", "video.fps.label")),
        ("compact.video.encoding", "video", ("video.codec.label", "video.bitrate.label")),
        ("video.orient.title", "rotate", ("video.orient.label", "video.mirror.label", "video.rotate180.label")),
        ("video.vcam.title", "camera", ()),
        ("video.power.title", "light", ()),
    ),
    "quality": (
        ("quality.exposure.title", "sun", ("quality.exposure.mode.label", "quality.exposure.bias.label")),
        ("quality.wb.title", "droplet", ()),
        ("quality.focus.title", "focus", ("quality.focus.mode.label", "quality.focus.position.label")),
    ),
    "effects": (
        ("effects.background.title", "person", ("effects.background.mode.label", "effects.background.quality.label")),
        ("effects.color.title", "palette", ()),
    ),
    "audio": (
        ("audio.mic.section", "mic", ()),
        ("audio.output.section", "speaker", ()),
        ("audio.level.section", "gauge", ()),
        ("audio.status.section", "info", ()),
        ("audio.vbcable.section", "wrench", ()),
    ),
    "recording": (
        ("recording.control.section", "record", ("recording.state.label", "recording.size.label")),
        ("recording.quality.section", "video", ("recording.preset.label", "recording.resolution.label", "recording.container.label")),
        ("recording.save.section", "folder", ()),
    ),
    "status": (
        ("status.iphone.title", "battery", ("status.iphone.thermal", "status.iphone.battery")),
        ("status.video.title", "video", ("status.video.sentfps", "status.video.encode")),
        ("status.pc.title", "camera", ("status.pc.out", "status.pc.decoder")),
        ("status.latency.title", "wifi", ("status.latency.total",)),
        ("status.diag.title", "wrench", ()),
        ("status.graph.title", "gauge", ()),
    ),
    "shortcuts": (
        ("compact.shortcuts.capture", "camera", ()),
        ("compact.shortcuts.adjust", "zoom", ()),
    ),
    "settings": (
        ("settings.general.title", "gauge", ()),
        ("update.section", "download", ("update.version.label", "update.auto.label")),
        ("settings.setup.title", "wrench", ()),
        ("settings.about.title", "info", ("settings.about.license",)),
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
        if isinstance(control, (QLabel, StatusValue)) and control.text() not in ("", "-", t("compact.no_value")):
            return control.text()
    return ""


def _set_summary(expander, rows):
    values = [_row_value(row) for row in rows]
    expander.set_summary(" · ".join(value for value in values if value) or t("compact.no_value"))


def compact_page(page, ctrl, key):
    """Move the existing widgets without rebuilding their controls or callbacks."""
    wanted = [t(name) for name in PRIMARY[key]]
    selected = {}
    rows = {}
    groups = {}
    extra = []
    while page.column.count():
        item = page.column.takeAt(0)
        widget = item.widget()
        if widget is None:
            continue
        if not isinstance(widget, Section) or widget.title_label and widget.title_label.text() == t("multi.output"):
            extra.append((widget, item.alignment(), widget.isHidden()))
            continue
        title = widget.title_label.text()
        children = groups.setdefault(title, [])
        layout = widget.card.layout()
        while layout.count():
            child = layout.takeAt(0).widget()
            if isinstance(child, Row):
                label = child.title_label.text()
                rows[label] = child
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

    for title_key, icon, summary_keys in GROUPS[key]:
        title = t(title_key)
        children = groups.pop(title, [])
        if not children:
            continue
        group_key = f"{key}.{title_key}"
        expander = Expander(ctrl, group_key, icon, title, "")
        for child, hidden in children:
            expander.add(child)
            child.setVisible(not hidden)
        page.column.addWidget(expander)
        if title_key == "connection.wifi.title" and hasattr(page, "wireless"):
            page.wireless.bind_expander(expander)   # 要約は Wi-Fi 側が「接続中 · 名前」などを出す
            continue
        summary_rows = ([rows[t(name)] for name in summary_keys if t(name) in rows]
                        if summary_keys else [child for child, _ in children if isinstance(child, Row)])
        refresh = partial(_set_summary, expander, summary_rows[:3])
        for row in summary_rows[:3]:
            for control in row.controls():
                if isinstance(control, QComboBox):
                    control.currentTextChanged.connect(lambda *_args, fn=refresh: fn())
                elif isinstance(control, QLineEdit):
                    control.textChanged.connect(lambda *_args, fn=refresh: fn())
                elif isinstance(control, QKeySequenceEdit):
                    control.keySequenceChanged.connect(lambda *_args, fn=refresh: fn())
        expander.header.clicked.connect(refresh)
        ctrl.settings_changed.connect(lambda *_args, fn=refresh: fn())
        if title_key == "video.vcam.title":
            ctrl.camera_registration_changed.connect(refresh)
        if key in ("status", "recording", "audio", "connection", "video"):
            ctrl.status_changed.connect(lambda *_args, fn=refresh: fn())
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
