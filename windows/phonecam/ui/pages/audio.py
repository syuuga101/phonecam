# -*- coding: utf-8 -*-
"""音声タブ (マイク・出力デバイス・排他モード・レベルメーター)
"""

import os
import time
from phonecam.core.audio_engine import (
    AudioEngine,
    DEFAULT_DEVICE_LABEL,
    OBS_AUDIO_TIP,
    SYSTEM_DEFAULT_KEY,
    VBCABLE_LATENCY_HINT,
    VBCABLE_PANEL_MISSING,
    choose_output_device,
    find_vbcable_control_panel,
    list_output_devices,
)
from phonecam.core.deps import sd
from phonecam.core.settings import AUDIO_AVAILABLE
from phonecam.ui.dpi import px
from phonecam.ui.labels import HINT_FG, METER_MIN_DB
from phonecam.ui.tkdeps import tk, ttk


def _build_audio(app, parent):
    f = app._section(parent, "iPhoneのマイク")
    app.mic_var = tk.BooleanVar(value=bool(app.settings["mic_enabled"]))
    app.mic_cb = ttk.Checkbutton(f, text="iPhoneのマイクを使う", variable=app.mic_var, command=app._mic_toggled)
    app.mic_cb.grid(row=0, column=0, columnspan=3, sticky="w", pady=2)

    ttk.Label(f, text="出力先").grid(row=1, column=0, sticky="w", pady=2)
    try:
        app._audio_devices = list_output_devices() if AUDIO_AVAILABLE else []
    except Exception:
        app._audio_devices = []
    names = [d["name"] for d in app._audio_devices]
    cur = choose_output_device(app._audio_devices, app.settings["mic_device"])
    app.audio_dev_var = tk.StringVar(value=cur["name"] if cur else DEFAULT_DEVICE_LABEL)
    app.audio_dev_cb = ttk.Combobox(f, textvariable=app.audio_dev_var, values=[DEFAULT_DEVICE_LABEL] + names,
                                     state="readonly", width=42)
    app.audio_dev_cb.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6)
    app.audio_dev_cb.bind("<<ComboboxSelected>>", lambda e: app._audio_device_changed())

    app.vol_scale, _, _ = app._slider(f, 2, "音量", "mic_volume", 0, 200, None,
                                        lambda v: "%d%%" % round(v), 5, as_int=True)
    mf = ttk.Frame(f)
    mf.grid(row=3, column=0, columnspan=3, sticky="w")
    # メインのミュート: iPhoneに伝えて音声そのものを送らせない
    app.mic_muted_var = tk.BooleanVar(value=bool(app.settings.get("mic_muted", False)))
    app.mic_muted_cb = ttk.Checkbutton(mf, text="ミュート", variable=app.mic_muted_var,
                                        command=lambda: app._changed("mic_muted", bool(app.mic_muted_var.get()), "micMuted"))
    app.mic_muted_cb.grid(row=0, column=0, sticky="w")
    app.phone_mute_var = tk.StringVar(value="")
    app.phone_mute_lbl = ttk.Label(mf, textvariable=app.phone_mute_var, foreground="#c0392b", font=("", 9, "bold"))
    app.phone_mute_lbl.grid(row=0, column=1, sticky="w", padx=(10, 0))
    app.mute_var = tk.BooleanVar(value=bool(app.settings["mic_mute"]))
    app.mute_cb = ttk.Checkbutton(mf, text="PCで消音（iPhoneからは送られ続けます）", variable=app.mute_var,
                                   command=lambda: app._changed("mic_mute", bool(app.mute_var.get()), None))
    app.mute_cb.grid(row=1, column=0, columnspan=2, sticky="w")

    ttk.Label(f, text="入力レベル").grid(row=4, column=0, sticky="w", pady=(6, 2))
    app.meter = tk.Canvas(f, height=12, highlightthickness=0, borderwidth=0, background="#e5e5ea")
    app.meter.grid(row=4, column=1, sticky="ew", padx=6, pady=(6, 2))
    app._meter_bar = app.meter.create_rectangle(0, 0, 0, 12, width=0, fill="#34c759")
    app.meter_text = ttk.Label(f, text="", width=9, anchor="e", foreground=HINT_FG)
    app.meter_text.grid(row=4, column=2, padx=(6, 0))

    app.delay_scale, _, _ = app._slider(f, 5, "音声の遅延", "mic_delay_ms", 0, 500, None,
                                          lambda v: "%d ms" % round(v), 10, as_int=True)
    app._hint(f, 6, "映像より音が早いときに増やして、映像に合わせます", pady=(0, 4))

    pf = ttk.Frame(f)
    pf.grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))
    ttk.Label(pf, text="再生バッファ").pack(side="left")
    app.prio_var = tk.StringVar(value=app.settings["mic_priority"])
    app.prio_rbs = []
    for text, val in (("低遅延優先", "low"), ("安定優先", "stable")):
        rb = ttk.Radiobutton(pf, text=text, value=val, variable=app.prio_var, command=app._prio_changed)
        rb.pack(side="left", padx=(10, 0))
        app.prio_rbs.append(rb)
    app.excl_var = tk.BooleanVar(value=app._exclusive_effective())
    app.excl_cb = ttk.Checkbutton(f, text="排他モード（さらに低遅延）", variable=app.excl_var,
                                   command=app._exclusive_toggled)
    app.excl_cb.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

    app.audio_info_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.audio_info_var, foreground=HINT_FG, wraplength=px(520), justify="left").grid(
        row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))
    app.audio_err_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.audio_err_var, foreground="#c0392b", wraplength=px(520), justify="left").grid(
        row=10, column=0, columnspan=3, sticky="w", pady=(2, 0))
    ttk.Label(f, text="音声経路").grid(row=11, column=0, sticky="w", pady=(6, 2))
    app.audio_path_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.audio_path_var).grid(row=11, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 2))
    ttk.Label(f, text="iPhone側のマイク").grid(row=12, column=0, sticky="w", pady=2)
    app.mic_path_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.mic_path_var).grid(row=12, column=1, columnspan=2, sticky="w", padx=6)
    ttk.Label(f, text="再生モード").grid(row=13, column=0, sticky="w", pady=2)
    app.audio_mode_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.audio_mode_var, wraplength=px(380), justify="left").grid(
        row=13, column=1, columnspan=2, sticky="w", padx=6)
    ttk.Label(f, text="音声の遅延（PCの出力まで）").grid(row=14, column=0, sticky="w", pady=2)
    app.audio_lat_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.audio_lat_var, font=("", 9, "bold")).grid(row=14, column=1, columnspan=2, sticky="w", padx=6)
    app.audio_under_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.audio_under_var, foreground=HINT_FG, font=("", 8)).grid(
        row=15, column=0, columnspan=3, sticky="w")
    app._hint(f, 16, "アンダーランが増えるときは「安定優先」にするか、排他モードをオフにしてください。VB-CABLE内部の遅延は含みません")
    app.vbcable_btn = ttk.Button(f, text="VB-CABLEの遅延設定を開く", command=app._open_vbcable_panel)
    app.vbcable_btn.grid(row=17, column=0, columnspan=3, sticky="w", pady=(6, 2))
    app._vbcable_path = find_vbcable_control_panel() if os.name == "nt" else None
    if app._vbcable_path is None:
        app._set_enabled(app.vbcable_btn, False)
    app._hint(f, 18, VBCABLE_LATENCY_HINT)
    app._hint(f, 19, OBS_AUDIO_TIP)
    app._hint(f, 20, "VB-CABLE（無料）をインストールして出力先を「CABLE Input」にすると、"
                      "OBSやDiscordでマイクとして「CABLE Output」を選べます", pady=(6, 0))
    app._hint(f, 21, "初めて使うときは、iPhoneでマイクの許可が必要です")
    if not AUDIO_AVAILABLE:
        app.audio_err_var.set("音声ライブラリ(sounddevice)が見つかりません。install.bat を実行してください")
        for w in [app.mic_cb, app.audio_dev_cb, app.vol_scale, app.mute_cb, app.delay_scale,
                  app.excl_cb] + app.prio_rbs:
            app._set_enabled(w, False)


def _open_vbcable_panel(app):
    """VB-CABLE のコントロールパネルを管理者として開く (Max Latency の変更に管理者権限が要る)。"""
    path = find_vbcable_control_panel() if os.name == "nt" else None
    if path is None:
        app.audio_err_var.set(VBCABLE_PANEL_MISSING)
        return
    try:
        os.startfile(path, "runas")
    except Exception:
        try:
            os.startfile(path)
        except Exception as e:
            app.audio_err_var.set("VB-CABLEのコントロールパネルを開けませんでした（%s）" % e)


def _mic_toggled(app):
    app._changed("mic_enabled", bool(app.mic_var.get()), "mic")
    app._apply_audio()


def _audio_device_changed(app):
    label = app.audio_dev_var.get()
    app._changed("mic_device", SYSTEM_DEFAULT_KEY if label == DEFAULT_DEVICE_LABEL else label, None)
    app.excl_var.set(app._exclusive_effective())  # 自動設定のときは出力先に合わせて既定が変わる
    app._apply_audio()


def _selected_output_name(app):
    """今の設定で使う出力先の名前 (システム既定のときは既定デバイスの名前、分からなければ空)。"""
    try:
        cur = choose_output_device(app._audio_devices, app.settings["mic_device"])
        if cur is not None:
            return cur["name"]
        if sd is not None and app.settings["mic_device"] in ("", SYSTEM_DEFAULT_KEY):
            idx = sd.default.device[1]
            if idx is not None and idx >= 0:
                return sd.query_devices(idx)["name"]
    except Exception:
        pass
    return ""


def _exclusive_effective(app):
    v = app.settings.get("mic_exclusive")
    if v is None:
        return AudioEngine.default_exclusive(app._selected_output_name())
    return bool(v)


def _exclusive_toggled(app):
    app._changed("mic_exclusive", bool(app.excl_var.get()), None)
    app._apply_audio()


def _prio_changed(app):
    app._changed("mic_priority", app.prio_var.get(), None)
    app.audio.low_latency = app.settings["mic_priority"] == "low"


def _apply_audio(app):
    """設定に合わせて音声ストリームを開く/閉じる。"""
    if not AUDIO_AVAILABLE:
        return
    app._audio_retry_t = time.monotonic()
    if app.settings["mic_enabled"]:
        app.audio.low_latency = app.settings["mic_priority"] == "low"
        app.audio.start(app.settings["mic_device"], app.settings["mic_exclusive"])
    else:
        app.audio.stop()


def _meter_tick(app):
    """入力レベルメーターを約20Hzで更新する。"""
    try:
        db = app.audio.level_db() if (AUDIO_AVAILABLE and app.audio.running) else -100.0
        if db >= app._meter_db:
            app._meter_db = db
        else:
            app._meter_db = max(db, app._meter_db - 3.0)
        shown = max(METER_MIN_DB, min(0.0, app._meter_db))
        w = app.meter.winfo_width()
        x = int(w * (shown - METER_MIN_DB) / -METER_MIN_DB)
        color = "#34c759" if shown < -12 else ("#ff9f0a" if shown < -3 else "#ff3b30")
        app.meter.coords(app._meter_bar, 0, 0, x, 12)
        app.meter.itemconfigure(app._meter_bar, fill=color)
        app.meter_text.config(text="%d dBFS" % round(shown) if app._meter_db > METER_MIN_DB else "-")
    except Exception:
        pass
    app.root.after(50, app._meter_tick)


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_audio(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_audio',
    '_open_vbcable_panel',
    '_mic_toggled',
    '_audio_device_changed',
    '_selected_output_name',
    '_exclusive_effective',
    '_exclusive_toggled',
    '_prio_changed',
    '_apply_audio',
    '_meter_tick',

)
