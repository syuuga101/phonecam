# -*- coding: utf-8 -*-
"""接続ページ (状態・プレビュー・接続方法・実行モード) と 映像ページ (解像度/fps/コーデック・出力先・専用カメラ登録・省電力)
"""

import threading
import time
from phonecam.core.canvas import canvas_size
from phonecam.core.deps import av, np, pyvirtualcam
from phonecam.core.elevate import run_elevated
from phonecam.core.paths import _is_frozen
from phonecam.core.settings import mirror_key, normalize_pair_code, valid_pair_code
from phonecam.core.softcam_reg import registration_status
from phonecam.core.softcam_shmem import (
    AUTO_UNSUPPORTED_NOTE,
    IDLE_STOP_LABELS,
    RUN_MODE_LABELS,
    WATCH_INTERVAL_MS,
)
from phonecam.core.stream import camera_state_text
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.dpi import px
from phonecam.ui.labels import (
    BACKEND_LABELS,
    CANVAS_LABELS,
    CODEC_LABELS,
    FIT_LABELS,
    FPS_LABELS,
    LENS_LABELS,
    ORIENT_LABELS,
    OUTPUT_SIZE_LABELS,
    RES_LABELS,
    SCREEN_AUTO_LABELS,
    watch_text,
)
from phonecam.ui.pages.preview import build_preview
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.widgets import page, row, section


def _dim(label):
    """二次色のラベルにして、テーマ切替でも色を追従させる。"""
    def apply(*_):
        try:
            label.configure(foreground=theme.colors()["text2"])
        except Exception:
            pass
    apply()
    theme.on_change(apply)
    return label


def _mode_changed(app):
    app._changed("mode", app.mode_var.get(), None)
    app.link.retry_auth()  # 認証失敗で止まっていた場合、つなぎ方を変えたらもう一度試す


def _pair_code_edited(app):
    """ペアリングコード欄の編集: 数字だけ6桁にそろえて保存し、認証失敗で止まっていた接続を再開する。"""
    raw = app.pair_var.get()
    code = normalize_pair_code(raw)
    if code != raw:
        app.pair_var.set(code)  # 再度 trace が呼ばれるので、ここで終わる
        return
    if code == app.settings.get("pair_code", ""):
        return
    app._changed("pair_code", code, None)
    if valid_pair_code(code):
        app.link.retry_auth()


def _lens_changed(app, value=None, send=True):
    """レンズ変更。左右反転チェックはレンズごとに記憶した値へ切り替える。
    value を渡すとコンボボックスもその値にする。send=False は iPhone 側の操作を取り込むとき (送り返さない)。"""
    if value is not None:
        app.lens_var.set(app._value_to_label(LENS_LABELS, value))
    app._changed("lens", app._label_to_value(LENS_LABELS, app.lens_var.get()), "video" if send else None)
    app.mirror_var.set(bool(app.settings[mirror_key(app.settings)]))


def _status_summary(app, snap, now):
    """上部の状態ラベル用の (キー, 点の色キー)。キー: live/idle/paused/off"""
    if not snap["connected"]:
        return "off"
    st = app.stream
    if st.desired(True, now):
        return "live"
    if getattr(st, "user_paused", False):
        return "paused"
    return "idle"


def _ok_color():
    c = theme.colors()
    return c.get("ok") or c.get("accent")


def _init_vars(app):
    app.stream_state_var = tk.StringVar(value="-")
    app.status_var = tk.StringVar(value=t("connection.state.stopped"))
    app.fps_var = tk.StringVar(value="-")
    app.dec_var = tk.StringVar(value="-")
    app.res_var = tk.StringVar(value="-")
    app.error_var = tk.StringVar(value="")


def _build_header(app, parent):
    """接続ページ上部: 状態 (点 + 文言 + iPhone名・接続方法) と 開始/一時停止・接続/切断 ボタン。"""
    box = ttk.Frame(parent)
    box.pack(fill="x", pady=(16, 0))
    left = ttk.Frame(box)
    left.pack(side="left", fill="x", expand=True)
    top = ttk.Frame(left)
    top.pack(anchor="w")
    dot = tk.Canvas(top, width=px(10), height=px(10), highlightthickness=0, borderwidth=0)
    dot.pack(side="left", padx=(0, 8))
    app.conn_state_var = tk.StringVar(value=t("connection.state.off"))
    ttk.Label(top, textvariable=app.conn_state_var, font=theme.font(14, "bold")).pack(side="left")
    app.conn_device_var = tk.StringVar(value="")
    _dim(ttk.Label(top, textvariable=app.conn_device_var)).pack(side="left", padx=(10, 0))
    app.conn_sub_var = tk.StringVar(value="")
    sub = _dim(ttk.Label(left, textvariable=app.conn_sub_var))
    sub.pack(anchor="w", pady=(2, 0))
    err = ttk.Label(left, textvariable=app.error_var, foreground=theme.colors()["live"], wraplength=px(420), justify="left")
    err.pack(anchor="w")

    btns = ttk.Frame(box)
    btns.pack(side="right", anchor="n", padx=(px(12), 0))
    app.stream_btn = ttk.Button(btns, text=t("connection.button.start"), width=9, command=app._stream_button)
    try:
        app.stream_btn.configure(style="Accent.TButton")
    except Exception:
        pass
    app.stream_btn.pack(side="left", padx=(0, 8))
    app.connect_btn = ttk.Button(btns, text=t("connection.button.connect"), width=9, command=app.toggle_connect)
    app.connect_btn.pack(side="left")

    def recolor(*_):
        c = theme.colors()
        try:
            err.configure(foreground=c["live"])
            _draw_dot(app, dot)
        except Exception:
            pass

    app._conn_dot = dot
    theme.on_change(recolor)
    _draw_dot(app, dot)


def _draw_dot(app, dot):
    key = getattr(app, "_conn_key", "off")
    c = theme.colors()
    color = _ok_color() if key == "live" or key == "idle" else c["text2"]
    if key == "paused":
        color = c["text2"]
    dot.config(bg=c["bg"])
    dot.delete("all")
    d = px(10)
    dot.create_oval(1, 1, d - 1, d - 1, fill=color, outline=color)


def _build_numbers(app, parent):
    """プレビュー下の小さな数値の行: 解像度・fps / 遅延 / 温度 / バッテリー。"""
    box = ttk.Frame(parent)
    box.pack(fill="x", pady=(12, 0))
    app._num_vars = {}
    for i, key in enumerate(("res", "latency", "thermal", "battery")):
        box.columnconfigure(i, weight=1, uniform="num")
        cell = ttk.Frame(box)
        cell.grid(row=0, column=i, sticky="w", padx=(0, px(8)))
        _dim(ttk.Label(cell, text=t("connection.num.%s" % key), font=theme.font(9))).pack(anchor="w")
        var = tk.StringVar(value="-")
        ttk.Label(cell, textvariable=var, font=theme.font(11)).pack(anchor="w", pady=(px(2), 0))
        app._num_vars[key] = var


def _header_tick(app):
    """0.25秒ごと: 上部の状態・数値の行を更新する。"""
    if getattr(app, "_closing", False):
        return
    try:
        now = time.monotonic()
        snap = app.state.snapshot()
        st = snap["phone_status"] if snap["connected"] else {}
        key = _status_summary(app, snap, now)
        app.conn_state_var.set(t("connection.state." + key))
        if key != getattr(app, "_conn_key", None):
            app._conn_key = key
            _draw_dot(app, app._conn_dot)
        if snap["connected"]:
            name = st.get("deviceName") or st.get("name") or t("connection.device.default")
            way = "Wi-Fi" if app.settings.get("mode") == "wifi" else "USB"
            app.conn_device_var.set("%s · %s" % (name, way))
            app.conn_sub_var.set(app.stream_state_var.get() if key != "live" else "")
        else:
            app.conn_device_var.set("")
            app.conn_sub_var.set(app.status_var.get() if app.wanted else "")
        res = snap["resolution"] or "-"
        if "→" in res:
            res = res.split("→", 1)[1].replace("出力", "").strip()
        v = app._num_vars
        v["res"].set("%s · %.0ffps" % (res, snap["recv_fps"]) if snap["connected"] and res != "-" else "-")
        avg = app.link.latency.averages().get("total") if snap["connected"] else None
        v["latency"].set("%d ms" % round(avg) if avg is not None else "-")
        thermal = st.get("thermalState")
        v["thermal"].set(t("connection.thermal." + thermal) if thermal in ("nominal", "fair", "serious", "critical") else "-")
        if "batteryLevel" in st:
            try:
                lv = float(st.get("batteryLevel", -1))
            except Exception:
                lv = -1.0
            charging = st.get("batteryState") in ("charging", "full")
            v["battery"].set(("%d%%" % round(lv * 100) + "") if lv >= 0 else "-")
        else:
            v["battery"].set("-")
    except Exception:
        pass
    try:
        app.root.after(250, lambda: _header_tick(app))
    except Exception:
        pass


def _build_connection(app, parent):
    """接続方法 (USB/Wi-Fi) と Wi-Fi のペアリング。変数名は従来のまま。"""
    f = section(parent, t("connection.method.title"))
    r = row(f, "link", t("connection.method.label"))
    app.mode_var = tk.StringVar(value=app.settings["mode"])
    ttk.Radiobutton(r, text="Wi-Fi", value="wifi", variable=app.mode_var,
                    command=app._mode_changed).pack(side="right", padx=(12, 0))
    ttk.Radiobutton(r, text="USB", value="usb", variable=app.mode_var,
                    command=app._mode_changed).pack(side="right")

    f = section(parent, t("connection.wifi.title"))
    r = row(f, "wifi", t("connection.wifi.ip"))
    app.ip_var = tk.StringVar(value=app.settings["ip"])
    app.ip_var.trace_add("write", lambda *_: app._changed("ip", app.ip_var.get(), None))
    ttk.Entry(r, textvariable=app.ip_var, width=18).pack(side="right")

    r = row(f, "link", t("connection.wifi.code"), t("connection.wifi.code_hint"))
    app.pair_var = tk.StringVar(value=app.settings.get("pair_code", ""))
    app.pair_var.trace_add("write", lambda *_: app._pair_code_edited())
    ttk.Entry(r, textvariable=app.pair_var, width=8, justify="center",
              font=theme.font(14, "bold")).pack(side="right")


def _build_run_control(app, parent):
    f = section(parent, t("connection.run.title"))
    r = row(f, "play", t("connection.run.mode"), t("connection.run.mode_hint"))
    app.run_mode_var = tk.StringVar(value=app._value_to_label(RUN_MODE_LABELS, app.settings["run_mode"]))
    cb = ttk.Combobox(r, textvariable=app.run_mode_var, values=[l for _, l in RUN_MODE_LABELS],
                      state="readonly", width=24)
    cb.pack(side="right")
    cb.bind("<<ComboboxSelected>>", lambda e: app._run_mode_changed())

    r = row(f, "clock", t("connection.run.idle"))
    app.idle_var = tk.StringVar(value=app._value_to_label(IDLE_STOP_LABELS, app.settings["idle_stop_sec"]))
    cb = ttk.Combobox(r, textvariable=app.idle_var, values=[l for _, l in IDLE_STOP_LABELS],
                      state="readonly", width=10)
    cb.pack(side="right")
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed(
        "idle_stop_sec", app._label_to_value(IDLE_STOP_LABELS, app.idle_var.get()), None))
    app.idle_cb = cb

    r = row(f, "camera", t("connection.run.camera"))
    app.camera_state_var = tk.StringVar(value="-")
    ttk.Label(r, textvariable=app.camera_state_var).pack(side="right")
    app.run_note_var = tk.StringVar(value="")
    _dim(ttk.Label(f, textvariable=app.run_note_var, wraplength=px(480), justify="left")).pack(anchor="w")


def _run_mode_changed(app):
    app._changed("run_mode", app._label_to_value(RUN_MODE_LABELS, app.run_mode_var.get()), None)
    app.stream.mode_changed()
    app._stream_tick_once()


def _stream_button(app):
    app.stream.press_button(app.state.snapshot()["connected"], time.monotonic())
    app._stream_tick_once()


def _stream_tick(app):
    """0.5秒ごと: 待機フレーム送出 → 使用中の検出 → iPhoneのカメラの起動/停止指示 → 表示。"""
    try:
        app._stream_tick_once()
        app.watcher.last_error = ""
    except Exception as e:
        # 握りつぶすと「使用中の検出」が止まったまま原因が分からないので、診断表示に出す
        app.watcher.last_error = "%s: %s" % (type(e).__name__, e)
    if not app._closing:
        app.root.after(WATCH_INTERVAL_MS, app._stream_tick)


def _stream_tick_once(app):
    now = time.monotonic()
    vc = app.link.vcam
    phonecam = app.settings.get("backend") == "phonecam"
    if phonecam:
        vc.standby(canvas_size(app.settings), now)
        if app._vcam_gen != vc.generation:
            app.watcher.reset()
            app._vcam_gen = vc.generation
        app.watcher.poll(now)
        app.stream.note_in_use(app.watcher.in_use(now), now)
        app.watch_var.set(watch_text(app.watcher, now))
    else:
        if app._vcam_gen != -2:
            app.watcher.reset()
            app._vcam_gen = -2
        app.stream.note_in_use(False, now)
        app.watch_var.set(t("connection.watch.na"))
    snap = app.state.snapshot()
    connected = snap["connected"]
    want = app.stream.pending(connected, now)
    if want is not None and app.link.send_control({"stream": {"active": want}}):
        app.stream.mark_sent(want)
    st = snap["phone_status"] if connected else {}
    app.stream_state_var.set(app.stream.state_text(connected, st, now))
    app.camera_state_var.set(camera_state_text(connected, st))
    app.stream_btn.config(text=t("connection.button.pause") if app.stream.desired(connected, now) else t("connection.button.start"))
    app._set_enabled(app.stream_btn, connected)
    app._set_enabled(app.idle_cb, app.stream.mode() == "auto")
    app.run_note_var.set(AUTO_UNSUPPORTED_NOTE if app.stream.auto_unsupported() else "")


def _combo(r, var, values, width, on_select):
    cb = ttk.Combobox(r, textvariable=var, values=values, state="readonly", width=width)
    cb.pack(side="right")
    cb.bind("<<ComboboxSelected>>", lambda e: on_select())
    return cb


def _switch(r, var, command):
    cb = ttk.Checkbutton(r, variable=var, command=command)
    try:
        cb.configure(style="Switch.TCheckbutton")
    except Exception:
        pass
    cb.pack(side="right")
    return cb


def _build_video(app, parent):
    """映像ページの設定一式。変数名は従来のまま。"""
    f = section(parent, t("video.send.title"))
    r = row(f, "lens", t("video.lens.label"))
    app.lens_var = tk.StringVar(value=app._value_to_label(LENS_LABELS, app.settings["lens"]))
    app.lens_combo = _combo(r, app.lens_var, [l for _, l in LENS_LABELS], 14, lambda: app._lens_changed())

    r = row(f, "video", t("video.resolution.label"))
    app.res_sel_var = tk.StringVar(value=app.settings["resolution"])
    _combo(r, app.res_sel_var, RES_LABELS, 14, lambda: app._changed("resolution", app.res_sel_var.get(), "video"))

    r = row(f, "gauge", t("video.fps.label"))
    app.fps_sel_var = tk.StringVar(value=str(app.settings["fps"]))
    _combo(r, app.fps_sel_var, [str(x) for x in FPS_LABELS], 14,
           lambda: app._changed("fps", int(app.fps_sel_var.get()), "video"))

    r = row(f, "download", t("video.bitrate.label"))
    sl = ttk.Frame(r)
    sl.pack(side="right")
    scale, _lbl, _var = app._slider(sl, 0, "", "bitrate", 5, 50, "bitrate", lambda v: "%d Mbps" % round(v), 1, as_int=True)
    try:
        scale.configure(length=px(160))
    except Exception:
        pass

    r = row(f, "video", t("video.codec.label"), t("video.codec.hint"))
    app.codec_var = tk.StringVar(value=app._value_to_label(CODEC_LABELS, app.settings["codec"]))
    _combo(r, app.codec_var, [l for _, l in CODEC_LABELS], 14, lambda: app._changed(
        "codec", app._label_to_value(CODEC_LABELS, app.codec_var.get()), "codec"))
    app.codec_now_var = tk.StringVar(value=t("video.now.none"))
    _dim(ttk.Label(r, textvariable=app.codec_now_var)).pack(side="right", padx=(0, 10))

    r = row(f, "gear", t("video.hw.label"), t("video.hw.hint"))
    app.hw_var = tk.BooleanVar(value=bool(app.settings["hw_decode"]))
    _switch(r, app.hw_var, app._hw_changed)
    app.decoder_now_var = tk.StringVar(value=t("video.now.none"))
    _dim(ttk.Label(r, textvariable=app.decoder_now_var)).pack(side="right", padx=(0, 10))

    f = section(parent, t("video.orient.title"))
    r = row(f, "rotate", t("video.orient.label"))
    app.orient_var = tk.StringVar(value=app._value_to_label(ORIENT_LABELS, app.settings.get("orientation", "auto")))
    _combo(r, app.orient_var, [l for _, l in ORIENT_LABELS], 14, lambda: app._changed(
        "orientation", app._label_to_value(ORIENT_LABELS, app.orient_var.get()), "video"))

    r = row(f, "flip", t("video.mirror.label"))
    app.mirror_var = tk.BooleanVar(value=bool(app.settings[mirror_key(app.settings)]))
    _switch(r, app.mirror_var, lambda: app._changed(mirror_key(app.settings), bool(app.mirror_var.get()), None))

    r = row(f, "rotate", t("video.rotate180.label"))
    app.rot_var = tk.BooleanVar(value=bool(app.settings["rotate180"]))
    _switch(r, app.rot_var, lambda: app._changed("rotate180", bool(app.rot_var.get()), None))

    f = section(parent, t("video.output.title"))
    r = row(f, "camera", t("video.backend.label"))
    app.backend_var = tk.StringVar(value=app._value_to_label(BACKEND_LABELS, app.settings["backend"]))
    _combo(r, app.backend_var, [l for _, l in BACKEND_LABELS], 24, lambda: app._on_backend())

    r = row(f, "aspect", t("video.outsize.label"), t("video.outsize.hint"))
    app.out_size_var = tk.StringVar(value=app._value_to_label(OUTPUT_SIZE_LABELS, app.settings["output_size"]))
    _combo(r, app.out_size_var, [l for _, l in OUTPUT_SIZE_LABELS], 24, lambda: app._changed(
        "output_size", app._label_to_value(OUTPUT_SIZE_LABELS, app.out_size_var.get()), "canvas"))

    r = row(f, "aspect", t("video.canvas.label"))
    app.canvas_var = tk.StringVar(value=app._value_to_label(CANVAS_LABELS, app.settings["canvas"]))
    _combo(r, app.canvas_var, [l for _, l in CANVAS_LABELS], 24, lambda: app._changed(
        "canvas", app._label_to_value(CANVAS_LABELS, app.canvas_var.get()), "canvas"))

    r = row(f, "aspect", t("video.fit.label"))
    app.fit_var = tk.StringVar(value=app._value_to_label(FIT_LABELS, app.settings["fit"]))
    _combo(r, app.fit_var, [l for _, l in FIT_LABELS], 24, lambda: app._changed(
        "fit", app._label_to_value(FIT_LABELS, app.fit_var.get()), "canvas"))

    f = section(parent, t("video.vcam.title"))
    app._build_cam_registration(f)


def _hw_changed(app):
    app.link.hw_disabled = False  # 前回失敗していても、入れ直したら試す
    app._changed("hw_decode", bool(app.hw_var.get()), None)


def _build_power(app, parent):
    f = section(parent, t("video.power.title"))
    r = row(f, "sun", t("video.power.black"), t("video.power.hint"))
    app.screen_black_var = tk.BooleanVar(value=bool(app.settings["screen_black"]))
    _switch(r, app.screen_black_var, lambda: app._screen_changed("screen_black", bool(app.screen_black_var.get())))

    r = row(f, "clock", t("video.power.auto"))
    app.screen_auto_var = tk.StringVar(value=app._value_to_label(SCREEN_AUTO_LABELS, app.settings["screen_auto"]))
    _combo(r, app.screen_auto_var, [l for _, l in SCREEN_AUTO_LABELS], 10, lambda: app._screen_changed(
        "screen_auto", app._label_to_value(SCREEN_AUTO_LABELS, app.screen_auto_var.get())))
    app.screen_state_var = tk.StringVar(value="")
    _dim(ttk.Label(f, textvariable=app.screen_state_var)).pack(anchor="w", pady=(2, 0))


def _screen_changed(app, key, value):
    app._screen_user_t = time.monotonic()
    app._changed(key, value, "screen")


def _on_backend(app):
    app._changed("backend", app._label_to_value(BACKEND_LABELS, app.backend_var.get()), None)
    app.link.vcam.request_reset()
    app.stream.note_in_use(False, time.monotonic())
    app._stream_tick_once()


def _build_cam_registration(app, f, row_index=None):
    """仮想カメラ「PhoneCam」の登録状態と 登録/解除ボタン (f はカード枠)。"""
    r = row(f, "camera", t("video.vcam.label"), t("video.vcam.hint_exe") if _is_frozen() else t("video.vcam.hint_src"))
    app.cam_unreg_btn = ttk.Button(r, text=t("video.vcam.unregister"), command=lambda: app._start_elevated("unregister"))
    app.cam_unreg_btn.pack(side="right", padx=(6, 0))
    app.cam_reg_btn = ttk.Button(r, text=t("video.vcam.register"), command=lambda: app._start_elevated("register"))
    app.cam_reg_btn.pack(side="right")
    app.cam_status_var = tk.StringVar(value="")
    _dim(ttk.Label(r, textvariable=app.cam_status_var)).pack(side="right", padx=(0, 10))
    app.cam_note_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.cam_note_var, foreground=theme.colors()["live"], wraplength=px(480),
              justify="left").pack(anchor="w")
    if not _is_frozen():
        app.cam_status_var.set(t("video.vcam.src_only"))
        app._set_enabled(app.cam_reg_btn, False)
        app._set_enabled(app.cam_unreg_btn, False)
    else:
        app._refresh_cam_status()


def _refresh_cam_status(app):
    if not _is_frozen():
        return "ok"
    try:
        st = registration_status()
    except Exception:
        st = "unavailable"
    app.cam_status_var.set(t("video.vcam.status." + st) if st in ("ok", "missing", "outdated", "unavailable") else st)
    app._set_enabled(app.cam_reg_btn, st != "unavailable" and not app._elev_busy)
    app._set_enabled(app.cam_unreg_btn, st in ("ok", "outdated") and not app._elev_busy)
    return st


def _start_elevated(app, action):
    if app._elev_busy or not _is_frozen():
        return
    app._elev_busy = True
    app._elev_result = None
    app.cam_note_var.set(t("video.vcam.elev_wait"))
    app._refresh_cam_status()
    param = "--register" if action == "register" else "--unregister"

    def work():
        try:
            app._elev_result = run_elevated(param)
        except Exception as e:
            app._elev_result = ("error", str(e))

    threading.Thread(target=work, name="elevated-" + action, daemon=True).start()
    app.root.after(200, lambda: app._elev_poll(action))


def _elev_poll(app, action):
    if app._closing:
        return
    res = app._elev_result
    if res is None:
        app.root.after(200, lambda: app._elev_poll(action))
        return
    app._elev_busy = False
    app._elev_result = None
    kind = res[0]
    st = app._refresh_cam_status()
    app.link.vcam.request_reset()
    word = t("video.vcam.word_register") if action == "register" else t("video.vcam.word_unregister")
    if kind == "cancelled":
        app.cam_note_var.set(t("video.vcam.cancelled", word=word))
    elif kind == "ok":
        if action == "register" and st == "ok":
            app.cam_note_var.set("")
            messagebox.showinfo("PhoneCam", t("video.vcam.registered"))
        elif action == "unregister" and st in ("missing", "unavailable"):
            app.cam_note_var.set("")
            messagebox.showinfo("PhoneCam", t("video.vcam.unregistered"))
        else:
            app.cam_note_var.set(t("video.vcam.unchanged", word=word))
    elif kind == "timeout":
        app.cam_note_var.set(t("video.vcam.timeout", word=word))
    else:
        app.cam_note_var.set(t("video.vcam.failed", word=word))


# -- 接続 ---------------------------------------------------------------
def toggle_connect(app):
    if not app.wanted:
        if app.settings["mode"] == "wifi" and not app.settings["ip"].strip():
            messagebox.showerror("PhoneCam", t("connection.error.ip"))
            return
        if app.settings["mode"] == "wifi" and not valid_pair_code(app.settings.get("pair_code", "")):
            messagebox.showerror("PhoneCam", t("connection.error.code"))
            return
        need_pv = app.settings.get("backend") != "phonecam"
        if av is None or np is None or (need_pv and pyvirtualcam is None):
            messagebox.showerror("PhoneCam", t("connection.error.libs"))
            return
        app.wanted = True
        app.connect_btn.config(text=t("connection.button.disconnect"))
        app.link.start()  # 認証失敗で止まっていた場合も、新しく始めるので再試行になる
    else:
        app.wanted = False
        app.connect_btn.config(text=t("connection.button.connect"))
        app.link.stop()
        app.stream.reset_user()


def build_connection(app, parent):
    """接続ページ: 状態 + 開始/接続ボタン → プレビュー → 数値 → 接続方法 → Wi-Fi → カメラの動作。"""
    _init_vars(app)
    parent = page(parent)   # 他のページと同じ「最大幅・中央寄せ・スクロール」の内容列に積む
    _build_header(app, parent)
    build_preview(app, parent)
    _build_numbers(app, parent)
    app._build_connection(parent)
    app._build_run_control(parent)
    app.root.after(250, lambda: _header_tick(app))


def build_video(app, parent):
    """映像ページ: 送信・向き・出力・仮想カメラ・省電力。"""
    app._build_video(parent)
    app._build_power(parent)


build = build_connection  # 旧名 (接続ページ)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_mode_changed',
    '_pair_code_edited',
    '_lens_changed',
    '_build_connection',
    '_build_run_control',
    '_run_mode_changed',
    '_stream_button',
    '_stream_tick',
    '_stream_tick_once',
    '_build_video',
    '_hw_changed',
    '_build_power',
    '_screen_changed',
    '_on_backend',
    '_build_cam_registration',
    '_refresh_cam_status',
    '_start_elevated',
    '_elev_poll',
    'toggle_connect',
)
