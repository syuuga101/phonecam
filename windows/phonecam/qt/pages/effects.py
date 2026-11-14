"""Background and color effects."""

from phonecam.core.settings import COLOR_DEFAULTS, NUMERIC_RANGES
from phonecam.i18n import t
from phonecam.qt.widgets import Hint, Page, bound_combo, bound_slider, button
from phonecam.labels import BG_MODE_LABELS, BG_QUALITY_LABELS


def build(ctrl):
    page = Page()
    background = page.add_section(t("effects.background.title"))
    background.add_row("person", t("effects.background.mode.label")).addControl(
        bound_combo(ctrl, "bg_mode", BG_MODE_LABELS, group="background"))
    strength = bound_slider(ctrl, "bg_strength", 0, 1, .05, lambda v: f"{v:.0%}", group="background")
    background.add_row("sparkles", t("effects.background.strength.label")).addControl(strength)
    quality = bound_combo(ctrl, "bg_quality", BG_QUALITY_LABELS, group="background")
    background.add_row("video", t("effects.background.quality.label")).addControl(quality)
    green_hint = Hint(t("effects.background.hint"))
    background.add_padded(green_hint)

    color = page.add_section(t("effects.color.title"))
    for icon, key, fmt in (
        ("sun", "brightness", "+.2f"), ("lens", "contrast", ".2f"),
        ("palette", "saturation", ".2f"), ("focus", "sharpness", ".2f"),
    ):
        low, high = NUMERIC_RANGES[key]
        color.add_row(icon, t(f"effects.color.{key}.label")).addControl(
            bound_slider(ctrl, key, low, high, .01, lambda v, spec=fmt: format(v, spec), group="color"))

    def reset():
        for key, value in COLOR_DEFAULTS.items():
            ctrl.changed(key, value, "color")

    reset_button = button(t("effects.color.reset.button"))
    reset_button.clicked.connect(reset)
    color.add_row("rotate", t("effects.color.reset.label")).addControl(reset_button)

    def refresh(key, value):
        if key == "bg_mode":
            strength.setEnabled(value != "off")
            quality.setEnabled(value != "off")
            green_hint.setVisible(value == "green")

    ctrl.settings_changed.connect(refresh)
    refresh("bg_mode", ctrl.get("bg_mode"))
    return page.finish()
