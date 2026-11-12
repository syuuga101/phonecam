# -*- coding: utf-8 -*-
"""画質調整タブ (露出・ホワイトバランス・フォーカス・ズーム)
"""

from phonecam.ui.dpi import px
from phonecam.ui.labels import SHUTTER_VALUES
from phonecam.ui.tkdeps import tk, ttk


def _build_exposure(app, parent):
    f = app._section(parent, "露出")
    app.exp_mode_cb = app._mode_combo(f, 0, "exp_mode", "exposure", app._update_enabled)
    app.bias_scale, _, _ = app._slider(f, 1, "露出補正", "exp_bias", -2.0, 2.0, "exposure", lambda v: "%+.1f EV" % v, 0.1)
    app.iso_scale, app.iso_label, _ = app._slider(f, 2, "ISO", "iso", 25, 3200, "exposure", lambda v: "%d" % round(v), 1, as_int=True)

    ttk.Label(f, text="シャッター速度").grid(row=3, column=0, sticky="w", pady=2)
    app.shutter_var = tk.StringVar(value="1/%d" % int(app.settings["shutter"]))
    app.shutter_cb = ttk.Combobox(f, textvariable=app.shutter_var, values=["1/%d" % s for s in SHUTTER_VALUES], state="readonly", width=14)
    app.shutter_cb.grid(row=3, column=1, sticky="w", padx=6)

    def on_shutter(_e=None):
        try:
            v = int(app.shutter_var.get().split("/")[1])
        except Exception:
            return
        app._changed("shutter", v, "exposure")

    app.shutter_cb.bind("<<ComboboxSelected>>", on_shutter)
    ttk.Label(f, text="東日本(50Hz)は1/50か1/100、西日本(60Hz)は1/60か1/120でちらつき防止",
              foreground="#666666", wraplength=px(480), justify="left").grid(row=4, column=0, columnspan=3, sticky="w")
    app.cur_exp_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.cur_exp_var, foreground="#666666", font=("", 8)).grid(row=5, column=0, columnspan=3, sticky="w")


def _build_wb(app, parent):
    f = app._section(parent, "ホワイトバランス")
    app.wb_mode_cb = app._mode_combo(f, 0, "wb_mode", "wb", app._update_enabled)
    app.temp_scale, _, _ = app._slider(f, 1, "色温度", "temperature", 2500, 8000, "wb", lambda v: "%d K" % round(v), 50, as_int=True)
    app.tint_scale, _, _ = app._slider(f, 2, "色かぶり", "tint", -50, 50, "wb", lambda v: "%d" % round(v), 1, as_int=True)
    app.cur_wb_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.cur_wb_var, foreground="#666666", font=("", 8)).grid(row=3, column=0, columnspan=3, sticky="w")


def _build_focus(app, parent):
    f = app._section(parent, "フォーカス")
    app.focus_mode_cb = app._mode_combo(f, 0, "focus_mode", "focus", app._update_enabled)
    app.focus_scale, _, _ = app._slider(f, 1, "位置 (0=近い 1=遠い)", "focus_pos", 0.0, 1.0, "focus", lambda v: "%.2f" % v, 0.01)
    app.cur_focus_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.cur_focus_var, foreground="#666666", font=("", 8)).grid(row=2, column=0, columnspan=3, sticky="w")
    app.focus_widgets = [app.focus_mode_cb, app.focus_scale]


def _build_zoom(app, parent):
    f = app._section(parent, "ズーム・ライト")
    app.zoom_scale, _, app.zoom_var = app._slider(f, 0, "ズーム", "zoom", 1.0, 10.0, "zoom", lambda v: "%.1fx" % v, 0.1)
    app.torch_var = tk.BooleanVar(value=bool(app.settings["torch"]))
    app.torch_cb = ttk.Checkbutton(f, text="ライト", variable=app.torch_var,
                                    command=lambda: app._changed("torch", bool(app.torch_var.get()), "torch"))
    app.torch_cb.grid(row=1, column=0, columnspan=2, sticky="w")


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_exposure(parent)
    app._build_wb(parent)
    app._build_focus(parent)
    app._build_zoom(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_exposure',
    '_build_wb',
    '_build_focus',
    '_build_zoom',

)
