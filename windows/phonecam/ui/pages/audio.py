# -*- coding: utf-8 -*-
"""音声タブ (マイク・出力デバイス・排他モード・レベルメーター)
"""

import os
import time
from phonecam.core.audio_engine import (
    AudioEngine,
    DEFAULT_DEVICE_LABEL,
    SYSTEM_DEFAULT_KEY,
    VBCABLE_PANEL_MISSING,
    choose_output_device,
    find_vbcable_control_panel,
    list_output_devices,
)
from phonecam.core.deps import sd
from phonecam.core.settings import AUDIO_AVAILABLE
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.dpi import px
from phonecam.ui.labels import METER_MIN_DB
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import page, row, section


def _switch(r, var, command):
    """行の右端にスイッチ (Switch.TCheckbutton) を置く。"""
    cb = ttk.Checkbutton(r, style="Switch.TCheckbutton", variable=var, command=command)
    cb.pack(side="right")
    return cb


def _value_label(r, var, **kw):
    """行の右端に値表示 (読み取り専用の文字) を置く。"""
    lbl = ttk.Label(r, textvariable=var, anchor="e", justify="right", **kw)
    lbl.pack(side="right")
    return lbl


def _row_slider(app, r, key, lo, hi, fmt, round_to):
    """行の右側にスライダー + 値表示を置く (設定保存は従来の _slider と同じ)。戻り値: (scale, value_label, var)。"""
    var = tk.DoubleVar(value=float(app.settings[key]))
    val_label = ttk.Label(r, width=8, anchor="e")
    val_label.pack(side="right")
    scale = ttk.Scale(r, from_=lo, to=hi, variable=var, orient="horizontal", length=px(180))
    scale.pack(side="right", padx=(0, 8))
    val_label.config(text=fmt(var.get()))

    def on_write(*_):
        try:
            v = float(var.get())
        except Exception:
            return
        v = int(round(round(v / round_to) * round_to))
        val_label.config(text=fmt(v))
        if app._ui_sync:  # 設定からUIを合わせているだけのときは、保存も送信もしない
            return
        app._changed(key, v, None)

    var.trace_add("write", on_write)
    app._slider_vars[key] = var
    return scale, val_label, var


def _build_audio(app, parent):
    pg = page(parent)

    # --- iPhoneのマイク ---
    c = section(pg, t("audio.mic.section"))
    r = row(c, "mic", t("audio.mic.use"))
    app.mic_var = tk.BooleanVar(value=bool(app.settings["mic_enabled"]))
    app.mic_cb = _switch(r, app.mic_var, app._mic_toggled)

    # メインのミュート: iPhoneに伝えて音声そのものを送らせない
    r = row(c, "volume", t("audio.mute.label"))
    app.mic_muted_var = tk.BooleanVar(value=bool(app.settings.get("mic_muted", False)))
    app.mic_muted_cb = _switch(
        r, app.mic_muted_var,
        lambda: app._changed("mic_muted", bool(app.mic_muted_var.get()), "micMuted"))
    app.phone_mute_var = tk.StringVar(value="")
    app.phone_mute_lbl = ttk.Label(r, textvariable=app.phone_mute_var, foreground=theme.colors()["live"])
    app.phone_mute_lbl.pack(side="right", padx=(0, 10))

    r = row(c, "speaker", t("audio.pcmute.label"), t("audio.pcmute.hint"))
    app.mute_var = tk.BooleanVar(value=bool(app.settings["mic_mute"]))
    app.mute_cb = _switch(r, app.mute_var, lambda: app._changed("mic_mute", bool(app.mute_var.get()), None))

    # --- 出力と音量 ---
    c = section(pg, t("audio.output.section"))
    r = row(c, "speaker", t("audio.device.label"))
    try:
        app._audio_devices = list_output_devices() if AUDIO_AVAILABLE else []
    except Exception:
        app._audio_devices = []
    names = [d["name"] for d in app._audio_devices]
    cur = choose_output_device(app._audio_devices, app.settings["mic_device"])
    app.audio_dev_var = tk.StringVar(value=cur["name"] if cur else DEFAULT_DEVICE_LABEL)
    app.audio_dev_cb = ttk.Combobox(r, textvariable=app.audio_dev_var, values=[DEFAULT_DEVICE_LABEL] + names,
                                     state="readonly", width=34)
    app.audio_dev_cb.pack(side="right")
    app.audio_dev_cb.bind("<<ComboboxSelected>>", lambda e: app._audio_device_changed())

    r = row(c, "volume", t("audio.volume.label"))
    app.vol_scale, _, _ = _row_slider(app, r, "mic_volume", 0, 200, lambda v: "%d%%" % round(v), 5)

    r = row(c, "clock", t("audio.delay.label"), t("audio.delay.hint"))
    app.delay_scale, _, _ = _row_slider(app, r, "mic_delay_ms", 0, 500, lambda v: "%d ms" % round(v), 10)

    r = row(c, "gauge", t("audio.priority.label"))
    app.prio_var = tk.StringVar(value=app.settings["mic_priority"])
    app.prio_rbs = []
    for text, val in ((t("audio.priority.stable"), "stable"), (t("audio.priority.low"), "low")):
        rb = ttk.Radiobutton(r, text=text, value=val, variable=app.prio_var, command=app._prio_changed)
        rb.pack(side="right", padx=(8, 0))
        app.prio_rbs.append(rb)

    r = row(c, "gauge", t("audio.exclusive.label"), t("audio.exclusive.hint"))
    app.excl_var = tk.BooleanVar(value=app._exclusive_effective())
    app.excl_cb = _switch(r, app.excl_var, app._exclusive_toggled)

    # --- 入力レベル ---
    c = section(pg, t("audio.level.section"))
    r = row(c, "mic", t("audio.level.label"))
    app.meter_text = ttk.Label(r, text="", width=9, anchor="e", foreground=theme.colors()["text2"])
    app.meter_text.pack(side="right")
    app.meter = tk.Canvas(r, height=px(10), width=px(180), highlightthickness=0, borderwidth=0,
                          background=theme.colors()["separator"])
    app.meter.pack(side="right", padx=(0, 8))
    app._meter_bar = app.meter.create_rectangle(0, 0, 0, px(10), width=0, fill=theme.colors()["accent"])

    def recolor(*_):
        try:
            col = theme.colors()
            app.meter.configure(background=col["separator"])
            app.meter_text.configure(foreground=col["text2"])
            app.phone_mute_lbl.configure(foreground=col["live"])
            app.audio_err_lbl.configure(foreground=col["live"])
        except Exception:
            pass
    theme.on_change(recolor)

    # --- 状態 (読み取り専用) ---
    c = section(pg, t("audio.status.section"))
    app.audio_info_var = tk.StringVar(value="")
    app.audio_err_var = tk.StringVar(value="")
    r = row(c, "info", t("audio.info.label"))
    _value_label(r, app.audio_info_var, wraplength=px(320))
    r = row(c, "info", t("audio.error.label"))
    app.audio_err_lbl = _value_label(r, app.audio_err_var, wraplength=px(320), foreground=theme.colors()["live"])
    app.audio_path_var = tk.StringVar(value="-")
    r = row(c, "link", t("audio.path.label"))
    _value_label(r, app.audio_path_var)
    app.mic_path_var = tk.StringVar(value="-")
    r = row(c, "mic", t("audio.micpath.label"))
    _value_label(r, app.mic_path_var)
    app.audio_mode_var = tk.StringVar(value="-")
    r = row(c, "play", t("audio.mode.label"))
    _value_label(r, app.audio_mode_var, wraplength=px(320))
    app.audio_lat_var = tk.StringVar(value="-")
    app.audio_under_var = tk.StringVar(value="")
    r = row(c, "clock", t("audio.latency.label"), t("audio.latency.hint"))
    _value_label(r, app.audio_lat_var)
    r = row(c, "info", t("audio.under.label"))
    _value_label(r, app.audio_under_var)

    # --- VB-CABLE (1行 + ボタン) ---
    c = section(pg, t("audio.vbcable.section"))
    r = row(c, "wrench", t("audio.vbcable.label"), t("audio.vbcable.hint"))
    app.vbcable_btn = ttk.Button(r, text=t("audio.vbcable.open"), command=app._open_vbcable_panel)
    app.vbcable_btn.pack(side="right")
    app._vbcable_path = find_vbcable_control_panel() if os.name == "nt" else None
    if app._vbcable_path is None:
        app._set_enabled(app.vbcable_btn, False)

    if not AUDIO_AVAILABLE:
        app.audio_err_var.set(t("audio.error.nolib"))
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
            app.audio_err_var.set(t("audio.vbcable.openfail", err=e))


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
        col = theme.colors()
        color = col["accent"] if shown < -3 else col["live"]
        app.meter.coords(app._meter_bar, 0, 0, x, px(10))
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
