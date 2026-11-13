"""Video input, output and power settings."""

from phonecam.i18n import t
from phonecam.qt.widgets import Hint, Page, StatusValue, bound_combo, bound_slider, bound_switch, button
from phonecam.ui.labels import (
    BACKEND_LABELS, CANVAS_LABELS, CODEC_LABELS, DECODER_LABELS, FIT_LABELS,
    FPS_LABELS, LENS_LABELS, ORIENT_LABELS, OUTPUT_SIZE_LABELS, RES_LABELS, SCREEN_AUTO_LABELS,
)


def build(ctrl):
    page = Page()
    send = page.add_section(t("compact.video.input"))
    for icon, name, key, choices, group, setter in (
        ("lens", "lens", "lens", LENS_LABELS, None, ctrl.set_lens),
        ("video", "resolution", "resolution", RES_LABELS, "video", None),
        ("gauge", "fps", "fps", FPS_LABELS, "video", None),
    ):
        send.add_row(icon, t(f"video.{name}.label")).addControl(
            bound_combo(ctrl, key, choices, group=group, setter=setter))
    encoding = page.add_section(t("compact.video.encoding"))
    encoding.add_row("download", t("video.bitrate.label")).addControl(
        bound_slider(ctrl, "bitrate", 5, 50, 1, lambda v: f"{v} Mbps", group="bitrate", as_int=True))
    # 「現在: …」は補足文に混ぜず、操作の左に小さく置く (Tk 版と同じ。補足文が長くて切れるのを避ける)
    codec = encoding.add_row("video", t("video.codec.label"), t("video.codec.hint"))
    codec_now = Hint("", wrap=False)
    codec.addControl(codec_now)
    codec.addControl(bound_combo(ctrl, "codec", CODEC_LABELS, group="codec"))
    decoder = encoding.add_row("gear", t("video.hw.label"), t("video.hw.hint"))
    decoder_now = Hint("", wrap=False)
    decoder.addControl(decoder_now)
    decoder.addControl(bound_switch(ctrl, "hw_decode", setter=ctrl.set_hw_decode))

    orientation = page.add_section(t("video.orient.title"))
    orientation.add_row("rotate", t("video.orient.label")).addControl(
        bound_combo(ctrl, "orientation", ORIENT_LABELS, group="video"))
    orientation.add_row("flip", t("video.mirror.label")).addControl(bound_switch(ctrl, "mirror_current"))
    orientation.add_row("rotate", t("video.rotate180.label")).addControl(bound_switch(ctrl, "rotate180"))

    output = send
    if not ctrl.secondary:
        output.add_row("camera", t("video.backend.label")).addControl(
            bound_combo(ctrl, "backend", BACKEND_LABELS, setter=ctrl.set_backend))
    for name, key, choices in (
        ("outsize", "output_size", OUTPUT_SIZE_LABELS),
        ("canvas", "canvas", CANVAS_LABELS),
        ("fit", "fit", FIT_LABELS),
    ):
        hint = t("video.outsize.hint") if name == "outsize" else None
        output.add_row("aspect", t(f"video.{name}.label"), hint).addControl(
            bound_combo(ctrl, key, choices, group="canvas"))

    camera = page.add_section(t("video.vcam.title"))
    registration = camera.add_row("camera", t("video.vcam.label"))
    registration_status = registration.addControl(StatusValue())
    controls = {}
    for action in ("register", "unregister"):
        control = button(t(f"video.vcam.{action}"))
        control.clicked.connect(lambda _, name=action: ctrl.change_camera_registration(name))
        registration.addControl(control)
        controls[action] = control

    def refresh_registration():
        state = ctrl.camera_registration
        hint = t("video.vcam.hint_exe" if ctrl.frozen else "video.vcam.hint_src")
        text = t(f"video.vcam.status.{state}") if ctrl.frozen else t("video.vcam.src_only")
        registration.setHint(hint)
        registration_status.set_status(text, {"ok": "ok", "outdated": "warning"}.get(state))
        controls["register"].setEnabled(ctrl.frozen and not ctrl.camera_registration_busy and state != "unavailable")
        controls["unregister"].setEnabled(ctrl.frozen and not ctrl.camera_registration_busy and state in ("ok", "outdated"))

    ctrl.camera_registration_changed.connect(refresh_registration)
    refresh_registration()

    power = page.add_section(t("video.power.title"))
    black = power.add_row("sun", t("video.power.black"), t("video.power.hint"))
    black.addControl(bound_switch(ctrl, "screen_black", setter=lambda v: ctrl.set_screen("screen_black", v)))
    power.add_row("clock", t("video.power.auto")).addControl(
        bound_combo(ctrl, "screen_auto", SCREEN_AUTO_LABELS, setter=lambda v: ctrl.set_screen("screen_auto", v)))

    def refresh(status):
        snapshot = ctrl.state.snapshot()
        codec_id = snapshot["codec_in_use"] or status.phone.get("codec") if status.connected else None
        codec_now.setText(t("status.pc.now", v=dict(CODEC_LABELS).get(codec_id, "-")))
        decoder_name = DECODER_LABELS.get(snapshot["decoder"], "-") if status.connected else "-"
        decoder_now.setText(t("status.pc.now", v=decoder_name))
        screen = ctrl.screen_state_text()
        black.setHint(t("video.power.hint") + (" · " + screen if screen else ""))

    ctrl.status_changed.connect(refresh)
    refresh(ctrl.status)
    return page.finish()
