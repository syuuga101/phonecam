"""Keep frequently used controls visible and group the rest by page."""

from phonecam.i18n import t
from phonecam.qt.widgets import Expander, Row, Section


PRIMARY = {
    "connection": ("connection.method.label", "connection.run.mode"),
    "video": ("video.resolution.label", "video.fps.label", "video.orient.label"),
    "quality": ("quality.exposure.mode.label", "quality.wb.mode.label", "quality.focus.mode.label", "quality.zoom.label"),
    "effects": ("effects.background.mode.label", "effects.background.strength.label", "effects.color.brightness.label"),
    "audio": ("audio.mic.use", "audio.mute.label", "audio.device.label", "audio.volume.label"),
    "recording": ("recording.button.label", "recording.preset.label", "recording.resolution.label"),
    "status": ("status.iphone.thermal", "status.iphone.battery", "status.video.sentfps"),
    "shortcuts": ("shortcuts.enable.label", "shortcuts.action.camera", "shortcuts.action.mic"),
    "settings": ("settings.language.label", "settings.theme.label", "update.check.label"),
}


def compact_page(page, ctrl, key):
    """Move the existing widgets without rebuilding their controls or callbacks."""
    wanted = [t(name) for name in PRIMARY[key]]
    selected = {}
    original = []
    while page.column.count():
        item = page.column.takeAt(0)
        if item.widget() is not None:
            original.append(item.widget())

    details = Expander(ctrl, key, "gear", t("compact.details"), "")
    kept = []
    section_names = []
    for index, widget in enumerate(original):
        if key == "connection" and index < 3:
            kept.append(widget)
            continue
        if isinstance(widget, Section):
            layout = widget.card.layout()
            for i in range(layout.count() - 1, -1, -1):
                row = layout.itemAt(i).widget()
                if isinstance(row, Row) and row.title_label.text() in wanted and row.title_label.text() not in selected:
                    layout.removeWidget(row)
                    selected[row.title_label.text()] = row
            if layout.count() == 0:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                continue
            if widget.title_label is not None:
                section_names.append(widget.title_label.text())
        details.add(widget)

    details.summary_label.setText(t("compact.summary", sections=" · ".join(section_names[:3])))
    details.summary_label.setToolTip(details.summary_label.text())
    details.header.setAccessibleDescription(details.summary_label.text())
    for widget in kept:
        page.column.addWidget(widget)
    primary = Section(t("compact.primary"))
    for label in wanted:
        row = selected.get(label)
        if row is not None:
            hint = row.hint_label.text()
            if hint and len(hint) > 40:
                row.setToolTip(hint)
                row.setHint("")
            primary.add(row)
    page.column.addWidget(primary)
    page.column.addWidget(details)
    page.column.addStretch(1)
    return page
