# -*- coding: utf-8 -*-
"""エフェクトページ (背景・色調整)
"""

from phonecam.core.settings import COLOR_DEFAULTS
from phonecam.i18n import t
from phonecam.ui.dpi import px
from phonecam.ui.labels import BG_MODE_LABELS, BG_QUALITY_LABELS
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import hint, page, row, section

SLIDER_LEN = 200


def _slider_row(app, card, icon, title, key, lo, hi, group, fmt, round_to):
    """ラベル | スライダー(約200px) + 値表示 の行。戻り値: (scale, var)。"""
    r = row(card, icon, title)
    var = tk.DoubleVar(value=float(app.settings[key]))
    val_label = ttk.Label(r, width=9, anchor="e")
    val_label.pack(side="right", padx=(6, 0))
    scale = ttk.Scale(r, from_=lo, to=hi, variable=var, orient="horizontal", length=px(SLIDER_LEN))
    scale.pack(side="right")
    val_label.config(text=fmt(var.get()))

    def on_write(*_):
        try:
            v = float(var.get())
        except Exception:
            return
        v = round(round(v / round_to) * round_to, 4)
        val_label.config(text=fmt(v))
        if app._ui_sync:  # 設定からUIを合わせているだけのときは、保存も送信もしない
            return
        app._changed(key, v, group)

    var.trace_add("write", on_write)
    app._slider_vars[key] = var
    return scale, var


def _build_background(app, parent):
    f = section(parent, t("effects.background.title"))
    r = row(f, "person", t("effects.background.mode.label"))
    app.bg_mode_var = tk.StringVar(value=app._value_to_label(BG_MODE_LABELS, app.settings["bg_mode"]))
    cb = ttk.Combobox(r, textvariable=app.bg_mode_var, values=[l for _, l in BG_MODE_LABELS], state="readonly", width=14)
    cb.pack(side="right")

    def on_mode(_e=None):
        app._changed("bg_mode", app._label_to_value(BG_MODE_LABELS, app.bg_mode_var.get()), "background")
        app._update_enabled()

    cb.bind("<<ComboboxSelected>>", on_mode)
    app.bg_strength_scale, _ = _slider_row(app, f, "sparkles", t("effects.background.strength.label"), "bg_strength",
                                           0.0, 1.0, "background", lambda v: "%d%%" % round(v * 100), 0.05)

    r = row(f, "video", t("effects.background.quality.label"))
    app.bg_quality_var = tk.StringVar(value=app._value_to_label(BG_QUALITY_LABELS, app.settings["bg_quality"]))
    app.bg_quality_cb = ttk.Combobox(r, textvariable=app.bg_quality_var, values=[l for _, l in BG_QUALITY_LABELS],
                                     state="readonly", width=14)
    app.bg_quality_cb.pack(side="right")
    app.bg_quality_cb.bind("<<ComboboxSelected>>", lambda e: app._changed(
        "bg_quality", app._label_to_value(BG_QUALITY_LABELS, app.bg_quality_var.get()), "background"))

    # app._update_enabled が grid()/grid_remove() で出し入れするので、専用の入れ物の中で grid 管理にする
    holder = ttk.Frame(f)
    holder.pack(fill="x", pady=(px(4), 0))
    holder.columnconfigure(0, weight=1)
    h = hint(holder, t("effects.background.hint"))
    try:
        h.pack_forget()
    except Exception:
        pass
    h.grid(row=0, column=0, sticky="w")
    app.bg_hint = h
    app.bg_hint_visible = True


def _build_color(app, parent):
    f = section(parent, t("effects.color.title"))
    app._color_vars = {}
    specs = [("sun", "brightness", -0.5, 0.5, lambda v: "%+.2f" % v),
             ("lens", "contrast", 0.5, 1.5, lambda v: "%.2f" % v),
             ("palette", "saturation", 0.0, 2.0, lambda v: "%.2f" % v),
             ("focus", "sharpness", 0.0, 1.0, lambda v: "%.2f" % v)]
    for icon, key, lo, hi, fmt in specs:
        _, var = _slider_row(app, f, icon, t("effects.color.%s.label" % key), key, lo, hi, "color", fmt, 0.01)
        app._color_vars[key] = var
    r = row(f, "rotate", t("effects.color.reset.label"))
    ttk.Button(r, text=t("effects.color.reset.button"), command=app._reset_color).pack(side="right")


def _reset_color(app):
    for key, var in app._color_vars.items():
        var.set(COLOR_DEFAULTS[key])


def build(app, parent):
    """このページの中身を parent に作る (App._build_ui から呼ばれる)。"""
    p = page(parent)
    app._build_background(p)
    app._build_color(p)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_background',
    '_build_color',
    '_reset_color',
)
