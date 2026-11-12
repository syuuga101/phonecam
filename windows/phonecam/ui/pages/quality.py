# -*- coding: utf-8 -*-
"""画質ページ (露出・ホワイトバランス・フォーカス・ズーム・ライト)
"""

from phonecam.i18n import t
from phonecam.ui.dpi import px
from phonecam.ui.labels import MODE3_LABELS, SHUTTER_VALUES
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import hint, page, row, section

SLIDER_LEN = 200


def _mode_row(app, card, key, group, title_key):
    """モード(自動/固定/マニュアル)の行。戻り値: Combobox。"""
    r = row(card, "gauge", t(title_key))
    var = tk.StringVar(value=app._value_to_label(MODE3_LABELS, app.settings[key]))
    cb = ttk.Combobox(r, textvariable=var, values=[l for _, l in MODE3_LABELS], state="readonly", width=14)
    cb.pack(side="right")
    app._mode_vars[key] = var

    def sel(_e=None):
        app._changed(key, app._label_to_value(MODE3_LABELS, var.get()), group)
        app._update_enabled()

    cb.bind("<<ComboboxSelected>>", sel)
    return cb


def _slider_row(app, card, icon, title, key, lo, hi, group, fmt, round_to, as_int=False, hint_text=None):
    """ラベル | スライダー(約200px) + 値表示 の行。戻り値: (scale, value_label, var)。"""
    r = row(card, icon, title, hint_text)
    var = tk.DoubleVar(value=float(app.settings[key]))
    val_label = ttk.Label(r, width=9, anchor="e")
    val_label.pack(side="right", padx=(6, 0))
    scale = ttk.Scale(r, from_=lo, to=hi, variable=var, orient="horizontal", length=px(SLIDER_LEN))
    scale.pack(side="right")

    def show(v):
        val_label.config(text=fmt(v))

    show(var.get())

    def on_write(*_):
        try:
            v = float(var.get())
        except Exception:
            return
        v = round(v / round_to) * round_to
        v = int(round(v)) if as_int else round(v, 4)
        show(v)
        if app._ui_sync:  # 設定からUIを合わせているだけのときは、保存も送信もしない
            return
        app._changed(key, v, group)

    var.trace_add("write", on_write)
    app._slider_vars[key] = var
    return scale, val_label, var


def _status_line(card, var):
    """現在値の表示(二次色の1行)。"""
    h = hint(card, "")
    h.configure(textvariable=var)
    return h


def _build_exposure(app, parent):
    f = section(parent, t("quality.exposure.title"))
    app.exp_mode_cb = _mode_row(app, f, "exp_mode", "exposure", "quality.exposure.mode.label")
    app.bias_scale, _, _ = _slider_row(app, f, "sun", t("quality.exposure.bias.label"), "exp_bias", -2.0, 2.0,
                                       "exposure", lambda v: "%+.1f EV" % v, 0.1)
    app.iso_scale, app.iso_label, _ = _slider_row(app, f, "gauge", t("quality.exposure.iso.label"), "iso", 25, 3200,
                                                  "exposure", lambda v: "%d" % round(v), 1, as_int=True)

    r = row(f, "clock", t("quality.exposure.shutter.label"), t("quality.exposure.shutter.hint"))
    app.shutter_var = tk.StringVar(value="1/%d" % int(app.settings["shutter"]))
    app.shutter_cb = ttk.Combobox(r, textvariable=app.shutter_var, values=["1/%d" % s for s in SHUTTER_VALUES],
                                  state="readonly", width=14)
    app.shutter_cb.pack(side="right")

    def on_shutter(_e=None):
        try:
            v = int(app.shutter_var.get().split("/")[1])
        except Exception:
            return
        app._changed("shutter", v, "exposure")

    app.shutter_cb.bind("<<ComboboxSelected>>", on_shutter)
    app.cur_exp_var = tk.StringVar(value="")
    _status_line(f, app.cur_exp_var)


def _build_wb(app, parent):
    f = section(parent, t("quality.wb.title"))
    app.wb_mode_cb = _mode_row(app, f, "wb_mode", "wb", "quality.wb.mode.label")
    app.temp_scale, _, _ = _slider_row(app, f, "thermometer", t("quality.wb.temperature.label"), "temperature",
                                       2500, 8000, "wb", lambda v: "%d K" % round(v), 50, as_int=True)
    app.tint_scale, _, _ = _slider_row(app, f, "droplet", t("quality.wb.tint.label"), "tint", -50, 50, "wb",
                                       lambda v: "%d" % round(v), 1, as_int=True)
    app.cur_wb_var = tk.StringVar(value="")
    _status_line(f, app.cur_wb_var)


def _build_focus(app, parent):
    f = section(parent, t("quality.focus.title"))
    app.focus_mode_cb = _mode_row(app, f, "focus_mode", "focus", "quality.focus.mode.label")
    app.focus_scale, _, _ = _slider_row(app, f, "focus", t("quality.focus.position.label"), "focus_pos", 0.0, 1.0,
                                        "focus", lambda v: "%.2f" % v, 0.01, hint_text=t("quality.focus.position.hint"))
    app.cur_focus_var = tk.StringVar(value="")
    _status_line(f, app.cur_focus_var)
    app.focus_widgets = [app.focus_mode_cb, app.focus_scale]


def _build_zoom(app, parent):
    f = section(parent, t("quality.zoom.title"))
    app.zoom_scale, _, app.zoom_var = _slider_row(app, f, "zoom", t("quality.zoom.label"), "zoom", 1.0, 10.0,
                                                  "zoom", lambda v: "%.1fx" % v, 0.1)
    f2 = section(parent, t("quality.light.title"))
    r = row(f2, "light", t("quality.light.label"))
    app.torch_var = tk.BooleanVar(value=bool(app.settings["torch"]))
    app.torch_cb = ttk.Checkbutton(r, style="Switch.TCheckbutton", variable=app.torch_var,
                                   command=lambda: app._changed("torch", bool(app.torch_var.get()), "torch"))
    app.torch_cb.pack(side="right")


def build(app, parent):
    """このページの中身を parent に作る (App._build_ui から呼ばれる)。"""
    p = page(parent)
    app._build_exposure(p)
    app._build_wb(p)
    app._build_focus(p)
    app._build_zoom(p)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_exposure',
    '_build_wb',
    '_build_focus',
    '_build_zoom',
)
