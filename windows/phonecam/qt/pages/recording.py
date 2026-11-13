"""iPhone recording controls and storage estimates."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from phonecam.core.settings import REC_AUDIO_KBPS, rec_effective_resolution, rec_mb_per_min, rec_video_mbps
from phonecam.core.stats import format_bytes, format_mmss
from phonecam.i18n import t
from phonecam.qt.widgets import ErrorLabel, Hint, Page, bound_combo, bound_slider, bound_switch, button
from phonecam.ui.labels import REC_CONTAINER_LABELS, REC_PRESET_LABELS, REC_RES_LABELS


def build(ctrl):
    page = Page()
    control = page.add_section(t("recording.control.section"))
    start = button(t("recording.button.start"), primary=True)
    control.add_row("record", t("recording.button.label")).addControl(start)
    values = {}
    for key, icon in (("state", "info"), ("file", "document"), ("size", "download"), ("free", "folder")):
        label = QLabel("-")
        label.setWordWrap(True)
        label.setMaximumWidth(320)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        control.add_row(icon, t(f"recording.{key}.label")).addControl(label)
        values[key] = label
    error = ErrorLabel("")
    control.add_padded(error)

    quality = page.add_section(t("recording.quality.section"))
    for name, icon, choices, hint in (
        ("preset", "sparkles", REC_PRESET_LABELS, None),
        ("resolution", "aspect", REC_RES_LABELS, None),
        ("container", "document", REC_CONTAINER_LABELS, t("recording.container.hint")),
    ):
        row = quality.add_row(icon, t(f"recording.{name}.label"), hint)
        combo = row.addControl(bound_combo(ctrl, f"rec_{name}", choices, group="recordSettings"))
        if name == "resolution":
            row.setToolTip(t("recording.resolution.hint"))
            combo.setToolTip(row.toolTip())
    estimate = quality.add_row("gauge", t("recording.estimate.label"))
    quality.add_row("wrench", t("recording.custom.label"), t("recording.custom.hint")).addControl(
        bound_switch(ctrl, "rec_custom_bitrate", group="recordSettings"))
    bitrate = bound_slider(ctrl, "rec_bitrate", 10, 100, 5, lambda v: f"{v} Mbps", group="recordSettings", as_int=True)
    quality.add_row("gauge", t("recording.bitrate.label")).addControl(bitrate)

    save = page.add_section(t("recording.save.section"))
    for name, icon in (("audio", "mic"), ("photos", "folder")):
        save.add_row(icon, t(f"recording.{name}.label")).addControl(
            bound_switch(ctrl, f"rec_{name}", group="recordSettings"))
    save.add_row("folder", t("recording.location.label"), t("recording.location.hint"))
    quality.add_padded(Hint(t("recording.hint.4k")))

    def refresh_estimate(*_):
        bitrate.setEnabled(bool(ctrl.get("rec_custom_bitrate")))
        mbps = rec_video_mbps(ctrl.settings)
        audio = ctrl.get("rec_audio", True)
        size = rec_mb_per_min(mbps, audio)
        detail = (t("recording.estimate.video_audio", mbps=mbps, kbps=REC_AUDIO_KBPS) if audio
                  else t("recording.estimate.video", mbps=mbps))
        estimate.setHint(t("recording.estimate.summary",
                           resolution=rec_effective_resolution(ctrl.settings).upper(),
                           minute=round(size), detail=detail, hour=f"{size * .06:.1f}"))

    def refresh(status):
        rec = status.phone.get("recording")
        rec = rec if isinstance(rec, dict) else {}
        start.setEnabled(status.rec_enabled)
        start.setText(t("recording.button.stop" if status.rec_active else "recording.button.start"))
        state = t("shell.sync.rec_stopped") if status.get("connected") else "-"   # 折りたたみの要約が「—」だけにならないように
        if rec:
            state = (t("shell.sync.rec_active", time=format_mmss(rec.get("seconds", 0)))
                     if status.rec_active else t("shell.sync.rec_stopped"))
        values["state"].setText(state)
        values["file"].setText(rec.get("fileName") or "-")
        values["size"].setText(format_bytes(rec.get("bytes")) if status.rec_active or rec.get("bytes") else "-")
        values["free"].setText(format_bytes(rec.get("freeBytes")) if rec.get("freeBytes") else "-")
        error.setText(rec.get("error") or "")

    def toggle():
        if ctrl.toggle_record():
            start.setEnabled(False)

    start.clicked.connect(toggle)
    ctrl.settings_changed.connect(refresh_estimate)
    ctrl.status_changed.connect(refresh)
    refresh_estimate()
    refresh(ctrl.status)
    return page.finish()
