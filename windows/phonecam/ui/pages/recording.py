# -*- coding: utf-8 -*-
"""録画タブ
"""

import time
from phonecam.core.settings import rec_estimate_text
from phonecam.ui.dpi import px
from phonecam.ui.labels import REC_CONTAINER_LABELS, REC_PRESET_LABELS, REC_RES_LABELS
from phonecam.ui.tkdeps import tk, ttk


def _build_record(app, parent):
    f = app._section(parent, "録画の設定（iPhone側で高画質に録画）")
    ttk.Label(f, text="解像度").grid(row=0, column=0, sticky="w", pady=2)
    app.rec_res_var = tk.StringVar(value=app._value_to_label(REC_RES_LABELS, app.settings["rec_resolution"]))
    cb = ttk.Combobox(f, textvariable=app.rec_res_var, values=[l for _, l in REC_RES_LABELS],
                      state="readonly", width=14)
    cb.grid(row=0, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._rec_option_changed(
        "rec_resolution", app._label_to_value(REC_RES_LABELS, app.rec_res_var.get())))
    ttk.Label(f, text="画質").grid(row=1, column=0, sticky="w", pady=2)
    app.rec_preset_var = tk.StringVar(value=app._value_to_label(REC_PRESET_LABELS, app.settings["rec_preset"]))
    cb = ttk.Combobox(f, textvariable=app.rec_preset_var, values=[l for _, l in REC_PRESET_LABELS],
                      state="readonly", width=14)
    cb.grid(row=1, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._rec_option_changed(
        "rec_preset", app._label_to_value(REC_PRESET_LABELS, app.rec_preset_var.get())))
    ttk.Label(f, text="ファイル形式").grid(row=2, column=0, sticky="w", pady=2)
    app.rec_container_var = tk.StringVar(
        value=app._value_to_label(REC_CONTAINER_LABELS, app.settings["rec_container"]))
    cb = ttk.Combobox(f, textvariable=app.rec_container_var, values=[l for _, l in REC_CONTAINER_LABELS],
                      state="readonly", width=24)
    cb.grid(row=2, column=1, columnspan=2, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._rec_option_changed(
        "rec_container", app._label_to_value(REC_CONTAINER_LABELS, app.rec_container_var.get())))
    app.rec_estimate_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.rec_estimate_var, wraplength=px(480), justify="left").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(2, 4))
    app.rec_custom_var = tk.BooleanVar(value=bool(app.settings["rec_custom_bitrate"]))
    ttk.Checkbutton(f, text="詳細設定: ビットレートを自分で指定する", variable=app.rec_custom_var,
                    command=app._rec_custom_toggled).grid(row=4, column=0, columnspan=3, sticky="w")
    app.rec_bitrate_scale, app.rec_bitrate_label, _ = app._slider(
        f, 5, "ビットレート (Mbps)", "rec_bitrate", 10, 100, "recordSettings",
        lambda v: "%d Mbps" % round(v), 5, as_int=True)
    app.rec_bitrate_widgets = f.grid_slaves(row=5)
    app.rec_audio_var = tk.BooleanVar(value=bool(app.settings["rec_audio"]))
    ttk.Checkbutton(f, text="音声を入れる", variable=app.rec_audio_var,
                    command=lambda: app._rec_option_changed("rec_audio", bool(app.rec_audio_var.get()))
                    ).grid(row=6, column=0, columnspan=3, sticky="w")
    app.rec_photos_var = tk.BooleanVar(value=bool(app.settings["rec_photos"]))
    ttk.Checkbutton(f, text="写真アプリにも保存", variable=app.rec_photos_var,
                    command=lambda: app._changed("rec_photos", bool(app.rec_photos_var.get()), "recordSettings")
                    ).grid(row=7, column=0, columnspan=3, sticky="w")
    app._hint(f, 8, "4K録画中は配信用の映像を縮小して送ります。発熱が増えるので30fpsがおすすめです", pady=(6, 0))
    app._hint(f, 9, "録画ファイルはiPhoneの「ファイル」アプリ（このiPhone内 > PhoneCam）にも残ります")
    app._hint(f, 10, "初めて使うときは、iPhoneでマイクと写真への保存の許可が必要です")
    app._hint(f, 11, "MP4は多くのソフトでそのまま開けます。MOVは編集ソフト向けです。サイズの目安は実際の映像によって前後します")
    app._refresh_rec_estimate()

    g = app._section(parent, "録画")
    ttk.Style().configure("Rec.TButton", font=("", 12, "bold"), padding=(24, 10))
    app.rec_btn = ttk.Button(g, text="録画開始", style="Rec.TButton", command=app._rec_clicked)
    app.rec_btn.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(2, 8))
    app.rec_vars = {}
    app.rec_labels = {}
    for row, (key, name) in enumerate([("state", "状態"), ("file", "ファイル名"),
                                       ("size", "サイズ"), ("free", "iPhoneの空き容量")], start=1):
        ttk.Label(g, text=name).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value="-")
        lbl = ttk.Label(g, textvariable=var, wraplength=px(380), justify="left")
        lbl.grid(row=row, column=1, columnspan=2, sticky="w", padx=6)
        app.rec_vars[key] = var
        app.rec_labels[key] = lbl
    app.rec_error_var = tk.StringVar(value="")
    ttk.Label(g, textvariable=app.rec_error_var, foreground="#c0392b", wraplength=px(520), justify="left").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
    app.rec_btn.state(["disabled"])


def _rec_option_changed(app, key, value):
    app._changed(key, value, "recordSettings")
    app._refresh_rec_estimate()


def _rec_custom_toggled(app):
    app._rec_option_changed("rec_custom_bitrate", bool(app.rec_custom_var.get()))


def _refresh_rec_estimate(app):
    """録画サイズの目安と、詳細設定 (ビットレートのスライダー) の表示切り替え。"""
    app.rec_estimate_var.set(rec_estimate_text(app.settings))
    for w in app.rec_bitrate_widgets:
        try:
            (w.grid if app.settings.get("rec_custom_bitrate") else w.grid_remove)()
        except Exception:
            pass


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
