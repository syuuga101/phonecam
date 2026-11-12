# -*- coding: utf-8 -*-
"""エフェクトタブ (背景・色調整)
"""

from phonecam.core.settings import COLOR_DEFAULTS
from phonecam.ui.labels import BG_MODE_LABELS, BG_QUALITY_LABELS
from phonecam.ui.tkdeps import tk, ttk


def _build_background(app, parent):
    f = app._section(parent, "背景")
    ttk.Label(f, text="モード").grid(row=0, column=0, sticky="w", pady=2)
    app.bg_mode_var = tk.StringVar(value=app._value_to_label(BG_MODE_LABELS, app.settings["bg_mode"]))
    cb = ttk.Combobox(f, textvariable=app.bg_mode_var, values=[l for _, l in BG_MODE_LABELS], state="readonly", width=14)
    cb.grid(row=0, column=1, sticky="w", padx=6)

    def on_mode(_e=None):
        app._changed("bg_mode", app._label_to_value(BG_MODE_LABELS, app.bg_mode_var.get()), "background")
        app._update_enabled()

    cb.bind("<<ComboboxSelected>>", on_mode)
    app.bg_strength_scale, _, _ = app._slider(
        f, 1, "強さ", "bg_strength", 0.0, 1.0, "background", lambda v: "%d%%" % round(v * 100), 0.05)

    ttk.Label(f, text="品質").grid(row=2, column=0, sticky="w", pady=2)
    app.bg_quality_var = tk.StringVar(value=app._value_to_label(BG_QUALITY_LABELS, app.settings["bg_quality"]))
    app.bg_quality_cb = ttk.Combobox(f, textvariable=app.bg_quality_var, values=[l for _, l in BG_QUALITY_LABELS],
                                      state="readonly", width=14)
    app.bg_quality_cb.grid(row=2, column=1, sticky="w", padx=6)
    app.bg_quality_cb.bind("<<ComboboxSelected>>", lambda e: app._changed(
        "bg_quality", app._label_to_value(BG_QUALITY_LABELS, app.bg_quality_var.get()), "background"))
    app.bg_hint = app._hint(f, 3, "OBSのソースに「クロマキー」フィルタ(緑)を追加すると背景が透明になります", pady=(4, 0))
    app.bg_hint_visible = True


def _build_color(app, parent):
    f = app._section(parent, "色調整")
    app._color_vars = {}
    specs = [("明るさ", "brightness", -0.5, 0.5, lambda v: "%+.2f" % v, 0.01),
             ("コントラスト", "contrast", 0.5, 1.5, lambda v: "%.2f" % v, 0.01),
             ("彩度", "saturation", 0.0, 2.0, lambda v: "%.2f" % v, 0.01),
             ("シャープネス", "sharpness", 0.0, 1.0, lambda v: "%.2f" % v, 0.01)]
    for row, (label, key, lo, hi, fmt, step) in enumerate(specs):
        _, _, var = app._slider(f, row, label, key, lo, hi, "color", fmt, step)
        app._color_vars[key] = var
    ttk.Button(f, text="リセット", command=app._reset_color).grid(row=len(specs), column=2, sticky="e", pady=(6, 0))


def _reset_color(app):
    for key, var in app._color_vars.items():
        var.set(COLOR_DEFAULTS[key])


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_background(parent)
    app._build_color(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_background',
    '_build_color',
    '_reset_color',

)
