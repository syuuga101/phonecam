"""Exposure, white balance, focus, zoom and lighting."""

from phonecam.i18n import t
from phonecam.qt.widgets import Hint, Page, bound_combo, bound_slider, bound_switch
from phonecam.ui.labels import MODE3_LABELS, SHUTTER_VALUES


def build(ctrl):
    page = Page()
    network = page.add_section(t("quality.adaptive.title"))
    network.add_row("wifi", t("quality.adaptive.label"), t("quality.adaptive.hint")).addControl(
        bound_switch(ctrl, "adaptive_bitrate", group="adaptive_bitrate"))

    def mode(name, key, group):
        card = page.add_section(t(f"quality.{name}.title"))
        combo = bound_combo(ctrl, key, MODE3_LABELS, group=group)
        card.add_row("gauge", t(f"quality.{name}.mode.label")).addControl(combo)
        return card, combo

    def slider(card, icon, name, key, lo, hi, step, fmt, group, *, as_int=False, hint=None):
        control = bound_slider(ctrl, key, lo, hi, step, fmt, group=group, as_int=as_int)
        card.add_row(icon, t(f"quality.{name}.label"), hint).addControl(control)
        return control

    def readout(card):
        label = Hint("")
        card.add_padded(label)
        return label

    exposure, _ = mode("exposure", "exp_mode", "exposure")
    bias = slider(exposure, "sun", "exposure.bias", "exp_bias", -2, 2, .1, lambda v: f"{v:+.1f} EV", "exposure")
    iso = slider(exposure, "gauge", "exposure.iso", "iso", 25, 3200, 1, str, "exposure", as_int=True)
    shutter = bound_combo(ctrl, "shutter", [(v, f"1/{v}") for v in SHUTTER_VALUES], group="exposure")
    exposure.add_row("clock", t("quality.exposure.shutter.label"), t("quality.exposure.shutter.hint")).addControl(shutter)
    current_exp = readout(exposure)

    wb, _ = mode("wb", "wb_mode", "wb")
    temp = slider(wb, "thermometer", "wb.temperature", "temperature", 2500, 8000, 50, lambda v: f"{v} K", "wb", as_int=True)
    tint = slider(wb, "droplet", "wb.tint", "tint", -50, 50, 1, str, "wb", as_int=True)
    current_wb = readout(wb)

    focus, focus_mode = mode("focus", "focus_mode", "focus")
    position = slider(focus, "focus", "focus.position", "focus_pos", 0, 1, .01, lambda v: f"{v:.2f}", "focus",
                      hint=t("quality.focus.position.hint"))
    current_focus = readout(focus)
    zoom = page.add_section(t("quality.zoom.title"))
    zoom_slider = slider(zoom, "zoom", "zoom", "zoom", 1, 10, .1, lambda v: f"{v:.1f}x", "zoom")
    light = page.add_section(t("quality.light.title"))
    torch = bound_switch(ctrl, "torch", group="torch")
    light.add_row("light", t("quality.light.label")).addControl(torch)

    def refresh(status):
        phone = status.phone
        manual = ctrl.get("exp_mode") == "manual"
        bias.setEnabled(ctrl.get("exp_mode") == "auto")
        iso.setEnabled(manual)
        shutter.setEnabled(manual)
        temp.setEnabled(ctrl.get("wb_mode") == "manual")
        tint.setEnabled(temp.isEnabled())
        focus_ok = phone.get("focusSupported", True)
        focus_mode.setEnabled(focus_ok)
        position.setEnabled(focus_ok and ctrl.get("focus_mode") == "manual")
        torch.setEnabled(phone.get("torchAvailable", True))
        lo, hi = phone.get("isoMin"), phone.get("isoMax")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and round(hi) > round(lo):
            iso.set_bounds(round(lo), round(hi))
        else:
            iso.set_bounds(25, 3200)
        maximum = phone.get("zoomMax")
        zoom_slider.set_bounds(1, maximum if isinstance(maximum, (int, float)) and maximum >= 1 else 10)

        parts = []
        if "iso" in phone:
            parts.append(t("shell.cur.iso", v=round(phone["iso"])))
        if phone.get("exposureSeconds"):
            parts.append(t("shell.cur.shutter", v=round(1 / phone["exposureSeconds"])))
        current_exp.setText(" / ".join(parts))
        current_wb.setText(t("shell.cur.wb", k=round(phone["temperature"]), tint=round(phone.get("tint", 0)))
                           if "temperature" in phone else "")
        current_focus.setText(t("shell.cur.focus", v=f"{phone['lensPosition']:.2f}") if "lensPosition" in phone else "")
        for label in (current_exp, current_wb, current_focus):
            label.setVisible(bool(label.text()))

    ctrl.status_changed.connect(refresh)
    ctrl.settings_changed.connect(lambda *_: refresh(ctrl.status))
    refresh(ctrl.status)
    return page.finish()
