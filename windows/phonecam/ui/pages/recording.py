# -*- coding: utf-8 -*-
"""録画タブ
"""

import time
from phonecam.core.settings import rec_estimate_text
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.dpi import px
from phonecam.ui.labels import REC_CONTAINER_LABELS, REC_PRESET_LABELS, REC_RES_LABELS
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import hint, page, row, section


def _rec_combo(app, r, var, labels, key, width):
    cb = ttk.Combobox(r, textvariable=var, values=[l for _, l in labels], state="readonly", width=width)
    cb.pack(side="right")
    cb.bind("<<ComboboxSelected>>", lambda e: app._rec_option_changed(
        key, app._label_to_value(labels, var.get())))
    return cb


def _rec_switch(r, var, command):
    cb = ttk.Checkbutton(r, style="Switch.TCheckbutton", variable=var, command=command)
    cb.pack(side="right")
    return cb


def _build_record(app, parent):
    pg = page(parent)

    # --- 録画 (ボタンと状態) ---
    g = section(pg, t("recording.control.section"))
    r = row(g, "record", t("recording.button.label"))
    app.rec_btn = ttk.Button(r, text=t("recording.button.start"), style="Accent.TButton", command=app._rec_clicked)
    app.rec_btn.pack(side="right")
    app.rec_vars = {}
    app.rec_labels = {}
    for key, icon in (("state", "info"), ("file", "document"), ("size", "download"), ("free", "folder")):
        rr = row(g, icon, t("recording.%s.label" % key))
        var = tk.StringVar(value="-")
        lbl = ttk.Label(rr, textvariable=var, anchor="e", justify="right", wraplength=px(320))
        lbl.pack(side="right")
        app.rec_vars[key] = var
        app.rec_labels[key] = lbl
    app.rec_error_var = tk.StringVar(value="")
    app.rec_error_lbl = ttk.Label(g, textvariable=app.rec_error_var, foreground=theme.colors()["live"],
                                  wraplength=px(480), justify="left")
    app.rec_error_lbl.pack(anchor="w")

    def recolor(*_):
        try:
            app.rec_error_lbl.configure(foreground=theme.colors()["live"])
        except Exception:
            pass
    theme.on_change(recolor)

    # --- 画質と形式 ---
    f = section(pg, t("recording.quality.section"))
    r = row(f, "sparkles", t("recording.preset.label"))
    app.rec_preset_var = tk.StringVar(value=app._value_to_label(REC_PRESET_LABELS, app.settings["rec_preset"]))
    _rec_combo(app, r, app.rec_preset_var, REC_PRESET_LABELS, "rec_preset", 14)
    r = row(f, "aspect", t("recording.resolution.label"), t("recording.resolution.hint"))
    app.rec_res_var = tk.StringVar(value=app._value_to_label(REC_RES_LABELS, app.settings["rec_resolution"]))
    _rec_combo(app, r, app.rec_res_var, REC_RES_LABELS, "rec_resolution", 14)
    r = row(f, "document", t("recording.container.label"), t("recording.container.hint"))
    app.rec_container_var = tk.StringVar(
        value=app._value_to_label(REC_CONTAINER_LABELS, app.settings["rec_container"]))
    _rec_combo(app, r, app.rec_container_var, REC_CONTAINER_LABELS, "rec_container", 24)
    r = row(f, "gauge", t("recording.estimate.label"))
    app.rec_estimate_var = tk.StringVar(value="")
    ttk.Label(r, textvariable=app.rec_estimate_var, anchor="e", justify="right", wraplength=px(320)).pack(side="right")

    r = row(f, "wrench", t("recording.custom.label"), t("recording.custom.hint"))
    app.rec_custom_var = tk.BooleanVar(value=bool(app.settings["rec_custom_bitrate"]))
    _rec_switch(r, app.rec_custom_var, app._rec_custom_toggled)
    r = row(f, "gauge", t("recording.bitrate.label"))
    app.rec_bitrate_label = ttk.Label(r, width=8, anchor="e")
    app.rec_bitrate_label.pack(side="right")
    var = tk.DoubleVar(value=float(app.settings["rec_bitrate"]))
    app.rec_bitrate_scale = ttk.Scale(r, from_=10, to=100, variable=var, orient="horizontal", length=px(180))
    app.rec_bitrate_scale.pack(side="right", padx=(0, 8))
    fmt = lambda v: "%d Mbps" % round(v)
    app.rec_bitrate_label.config(text=fmt(var.get()))

    def on_write(*_):
        try:
            v = int(round(round(float(var.get()) / 5) * 5))
        except Exception:
            return
        app.rec_bitrate_label.config(text=fmt(v))
        if app._ui_sync:  # 設定からUIを合わせているだけのときは、保存も送信もしない
            return
        app._changed("rec_bitrate", v, "recordSettings")

    var.trace_add("write", on_write)
    app._slider_vars["rec_bitrate"] = var
    # 詳細設定がオフのときはビットレートの行を無効にする (行の出し入れはしない)
    app.rec_bitrate_widgets = [app.rec_bitrate_scale, app.rec_bitrate_label]

    # --- 音声・保存先 ---
    h = section(pg, t("recording.save.section"))
    r = row(h, "mic", t("recording.audio.label"))
    app.rec_audio_var = tk.BooleanVar(value=bool(app.settings["rec_audio"]))
    _rec_switch(r, app.rec_audio_var, lambda: app._rec_option_changed("rec_audio", bool(app.rec_audio_var.get())))
    r = row(h, "folder", t("recording.photos.label"))
    app.rec_photos_var = tk.BooleanVar(value=bool(app.settings["rec_photos"]))
    _rec_switch(r, app.rec_photos_var,
                lambda: app._changed("rec_photos", bool(app.rec_photos_var.get()), "recordSettings"))
    # 保存先はiPhone内 (PCにはフォルダを作らない) ので「フォルダを開く」は無し
    row(h, "folder", t("recording.location.label"), t("recording.location.hint"))
    hint(pg, t("recording.hint.4k"))
    app._refresh_rec_estimate()
    app.rec_btn.state(["disabled"])


def _rec_option_changed(app, key, value):
    app._changed(key, value, "recordSettings")
    app._refresh_rec_estimate()


def _rec_custom_toggled(app):
    app._rec_option_changed("rec_custom_bitrate", bool(app.rec_custom_var.get()))


def _refresh_rec_estimate(app):
    """録画サイズの目安と、詳細設定 (ビットレートのスライダー) の表示切り替え。"""
    app.rec_estimate_var.set(rec_estimate_text(app.settings))
    on = bool(app.settings.get("rec_custom_bitrate"))
    for w in app.rec_bitrate_widgets:
        app._set_enabled(w, on)


def _rec_clicked(app):
    rec = app.state.snapshot()["phone_status"].get("recording")
    active = bool(rec.get("active")) if isinstance(rec, dict) else False
    if app.link.send_control({"record": "stop" if active else "start"}):
        app._rec_lock_until = time.monotonic() + 1.5
        app.rec_btn.state(["disabled"])


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_record(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_record',
    '_rec_option_changed',
    '_rec_custom_toggled',
    '_refresh_rec_estimate',
    '_rec_clicked',

)
