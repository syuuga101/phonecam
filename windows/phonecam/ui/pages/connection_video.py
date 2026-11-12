# -*- coding: utf-8 -*-
"""接続・映像タブ (接続方法・実行モード・解像度/fps/コーデック・出力先・専用カメラ登録)
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
from phonecam.ui.dpi import px
from phonecam.ui.labels import (
    BACKEND_LABELS,
    CAM_REG_EXPLAIN,
    CANVAS_LABELS,
    CODEC_LABELS,
    FIT_LABELS,
    FPS_LABELS,
    HINT_FG,
    LENS_LABELS,
    ORIENT_LABELS,
    OUTPUT_SIZE_LABELS,
    RES_LABELS,
    SCREEN_AUTO_LABELS,
    watch_text,
)
from phonecam.ui.tkdeps import messagebox, tk, ttk


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


def _build_connection(app, parent):
    f = app._section(parent, "接続")
    app.mode_var = tk.StringVar(value=app.settings["mode"])
    row = ttk.Frame(f)
    row.grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Radiobutton(row, text="USB", value="usb", variable=app.mode_var,
                    command=app._mode_changed).pack(side="left")
    ttk.Radiobutton(row, text="Wi-Fi", value="wifi", variable=app.mode_var,
                    command=app._mode_changed).pack(side="left", padx=(12, 0))

    ttk.Label(f, text="iPhoneのIP").grid(row=1, column=0, sticky="w", pady=4)
    app.ip_var = tk.StringVar(value=app.settings["ip"])
    app.ip_var.trace_add("write", lambda *_: app._changed("ip", app.ip_var.get(), None))
    ttk.Entry(f, textvariable=app.ip_var, width=20).grid(row=1, column=1, sticky="w", padx=6)

    app.connect_btn = ttk.Button(f, text="接続", command=app.toggle_connect)
    app.connect_btn.grid(row=1, column=2, sticky="e")

    ttk.Label(f, text="ペアリングコード").grid(row=2, column=0, sticky="w", pady=4)
    app.pair_var = tk.StringVar(value=app.settings.get("pair_code", ""))
    app.pair_var.trace_add("write", lambda *_: app._pair_code_edited())
    ttk.Entry(f, textvariable=app.pair_var, width=10).grid(row=2, column=1, sticky="w", padx=6)
    app._hint(f, 3, "Wi-Fiで接続するときだけ必要です（USBでは不要）。iPhoneのPhoneCamアプリで"
                     "「Wi-Fiからの接続を許可」をオンにすると、6桁のコードが表示されます", pady=(0, 4))

    app.status_var = tk.StringVar(value="停止中")
    app.fps_var = tk.StringVar(value="-")
    app.dec_var = tk.StringVar(value="-")
    app.res_var = tk.StringVar(value="-")
    for i, (name, var) in enumerate([("状態", app.status_var), ("受信fps", app.fps_var),
                                     ("デコード時間", app.dec_var), ("映像サイズ", app.res_var)]):
        ttk.Label(f, text=name).grid(row=4 + i, column=0, sticky="w")
        ttk.Label(f, textvariable=var, wraplength=px(380), justify="left").grid(row=4 + i, column=1, columnspan=2, sticky="w", padx=6)
    app.error_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.error_var, foreground="#c0392b", wraplength=px(480), justify="left").grid(
        row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))


def _build_run_control(app, parent):
    f = app._section(parent, "iPhoneのカメラの動作")
    ttk.Label(f, text="カメラを動かすタイミング").grid(row=0, column=0, sticky="w", pady=2)
    app.run_mode_var = tk.StringVar(value=app._value_to_label(RUN_MODE_LABELS, app.settings["run_mode"]))
    cb = ttk.Combobox(f, textvariable=app.run_mode_var, values=[l for _, l in RUN_MODE_LABELS],
                      state="readonly", width=26)
    cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._run_mode_changed())

    ttk.Label(f, text="使われなくなってから停止まで").grid(row=1, column=0, sticky="w", pady=2)
    app.idle_var = tk.StringVar(value=app._value_to_label(IDLE_STOP_LABELS, app.settings["idle_stop_sec"]))
    cb = ttk.Combobox(f, textvariable=app.idle_var, values=[l for _, l in IDLE_STOP_LABELS],
                      state="readonly", width=10)
    cb.grid(row=1, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed(
        "idle_stop_sec", app._label_to_value(IDLE_STOP_LABELS, app.idle_var.get()), None))
    app.idle_cb = cb

    ttk.Label(f, text="状態").grid(row=2, column=0, sticky="w", pady=(6, 2))
    app.stream_state_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.stream_state_var, font=("", 9, "bold")).grid(
        row=2, column=1, sticky="w", padx=6, pady=(6, 2))
    app.stream_btn = ttk.Button(f, text="開始", width=10, command=app._stream_button)
    app.stream_btn.grid(row=2, column=2, sticky="e", pady=(6, 2))
    ttk.Label(f, text="カメラ").grid(row=3, column=0, sticky="w")
    app.camera_state_var = tk.StringVar(value="-")
    ttk.Label(f, textvariable=app.camera_state_var).grid(row=3, column=1, sticky="w", padx=6)
    app.run_note_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.run_note_var, foreground="#b7791f", wraplength=px(480), justify="left").grid(
        row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
    app._hint(f, 5, "自動: OBSやDiscordなどがPhoneCamカメラを開いている間だけ、iPhoneのカメラを動かします。"
                     "使っていないときはカメラが止まり、発熱とバッテリー消費を抑えられます", pady=(4, 0))


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
        app.watch_var.set("-（出力先が「PhoneCam（専用カメラ）」のときだけ判定します）")
    snap = app.state.snapshot()
    connected = snap["connected"]
    want = app.stream.pending(connected, now)
    if want is not None and app.link.send_control({"stream": {"active": want}}):
        app.stream.mark_sent(want)
    st = snap["phone_status"] if connected else {}
    app.stream_state_var.set(app.stream.state_text(connected, st, now))
    app.camera_state_var.set(camera_state_text(connected, st))
    app.stream_btn.config(text=app.stream.button_label(connected, now))
    app._set_enabled(app.stream_btn, connected)
    app._set_enabled(app.idle_cb, app.stream.mode() == "auto")
    app.run_note_var.set(AUTO_UNSUPPORTED_NOTE if app.stream.auto_unsupported() else "")


def _build_video(app, parent):
    f = app._section(parent, "映像")
    ttk.Label(f, text="レンズ").grid(row=0, column=0, sticky="w", pady=2)
    app.lens_var = tk.StringVar(value=app._value_to_label(LENS_LABELS, app.settings["lens"]))
    cb = ttk.Combobox(f, textvariable=app.lens_var, values=[l for _, l in LENS_LABELS], state="readonly", width=14)
    cb.grid(row=0, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._lens_changed())
    app.lens_combo = cb

    ttk.Label(f, text="iPhoneから送る解像度").grid(row=1, column=0, sticky="w", pady=2)
    app.res_sel_var = tk.StringVar(value=app.settings["resolution"])
    cb = ttk.Combobox(f, textvariable=app.res_sel_var, values=RES_LABELS, state="readonly", width=14)
    cb.grid(row=1, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("resolution", app.res_sel_var.get(), "video"))

    ttk.Label(f, text="fps").grid(row=2, column=0, sticky="w", pady=2)
    app.fps_sel_var = tk.StringVar(value=str(app.settings["fps"]))
    cb = ttk.Combobox(f, textvariable=app.fps_sel_var, values=[str(x) for x in FPS_LABELS], state="readonly", width=14)
    cb.grid(row=2, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("fps", int(app.fps_sel_var.get()), "video"))

    app._slider(f, 3, "ビットレート (Mbps)", "bitrate", 5, 50, "bitrate", lambda v: "%d Mbps" % round(v), 1, as_int=True)

    app.mirror_var = tk.BooleanVar(value=bool(app.settings[mirror_key(app.settings)]))
    ttk.Checkbutton(f, text="左右反転", variable=app.mirror_var,
                    command=lambda: app._changed(mirror_key(app.settings), bool(app.mirror_var.get()), None)).grid(row=4, column=0, columnspan=2, sticky="w")
    app.rot_var = tk.BooleanVar(value=bool(app.settings["rotate180"]))
    ttk.Checkbutton(f, text="180°回転", variable=app.rot_var,
                    command=lambda: app._changed("rotate180", bool(app.rot_var.get()), None)).grid(row=5, column=0, columnspan=2, sticky="w")

    ttk.Label(f, text="出力先").grid(row=6, column=0, sticky="w", pady=2)
    app.backend_var = tk.StringVar(value=app._value_to_label(BACKEND_LABELS, app.settings["backend"]))
    cb = ttk.Combobox(f, textvariable=app.backend_var, values=[l for _, l in BACKEND_LABELS], state="readonly", width=24)
    cb.grid(row=6, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._on_backend())

    ttk.Label(f, text="向き").grid(row=7, column=0, sticky="w", pady=2)
    app.orient_var = tk.StringVar(value=app._value_to_label(ORIENT_LABELS, app.settings.get("orientation", "auto")))
    cb = ttk.Combobox(f, textvariable=app.orient_var, values=[l for _, l in ORIENT_LABELS], state="readonly", width=14)
    cb.grid(row=7, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("orientation", app._label_to_value(ORIENT_LABELS, app.orient_var.get()), "video"))

    ttk.Label(f, text="出力サイズ（仮想カメラ）").grid(row=8, column=0, sticky="w", pady=2)
    app.out_size_var = tk.StringVar(value=app._value_to_label(OUTPUT_SIZE_LABELS, app.settings["output_size"]))
    cb = ttk.Combobox(f, textvariable=app.out_size_var, values=[l for _, l in OUTPUT_SIZE_LABELS], state="readonly", width=24)
    cb.grid(row=8, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed(
        "output_size", app._label_to_value(OUTPUT_SIZE_LABELS, app.out_size_var.get()), "canvas"))

    ttk.Label(f, text="出力の形").grid(row=9, column=0, sticky="w", pady=2)
    app.canvas_var = tk.StringVar(value=app._value_to_label(CANVAS_LABELS, app.settings["canvas"]))
    cb = ttk.Combobox(f, textvariable=app.canvas_var, values=[l for _, l in CANVAS_LABELS], state="readonly", width=24)
    cb.grid(row=9, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("canvas", app._label_to_value(CANVAS_LABELS, app.canvas_var.get()), "canvas"))
    ttk.Label(f, text="合わせ方").grid(row=10, column=0, sticky="w", pady=2)
    app.fit_var = tk.StringVar(value=app._value_to_label(FIT_LABELS, app.settings["fit"]))
    cb = ttk.Combobox(f, textvariable=app.fit_var, values=[l for _, l in FIT_LABELS], state="readonly", width=24)
    cb.grid(row=10, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("fit", app._label_to_value(FIT_LABELS, app.fit_var.get()), "canvas"))
    app._hint(f, 11, "仮想カメラのサイズは固定です。iPhoneから送る解像度や向きを変えても、OBSやDiscordの映像は止まりません。"
                      "ここを変えたときだけ、OBSはソースを非表示→表示、Discordはカメラを一度オフ→オンにしてください")

    ttk.Label(f, text="コーデック").grid(row=12, column=0, sticky="w", pady=2)
    app.codec_var = tk.StringVar(value=app._value_to_label(CODEC_LABELS, app.settings["codec"]))
    cb = ttk.Combobox(f, textvariable=app.codec_var, values=[l for _, l in CODEC_LABELS], state="readonly", width=14)
    cb.grid(row=12, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._changed("codec", app._label_to_value(CODEC_LABELS, app.codec_var.get()), "codec"))
    app.codec_now_var = tk.StringVar(value="現在: -")
    ttk.Label(f, textvariable=app.codec_now_var, foreground=HINT_FG).grid(row=12, column=2, sticky="w")
    app._hint(f, 13, "HEVCは同じ画質をより少ないデータ量で送れます。USB接続ではH.264で十分です。"
                      "Wi-Fiで映像が乱れるときにHEVCを試してください（PCのデコード負荷は少し増えます）")

    app.hw_var = tk.BooleanVar(value=bool(app.settings["hw_decode"]))
    ttk.Checkbutton(f, text="ハードウェアデコード（GPUで処理）", variable=app.hw_var,
                    command=app._hw_changed).grid(row=14, column=0, columnspan=2, sticky="w", pady=(6, 0))
    app.decoder_now_var = tk.StringVar(value="現在: -")
    ttk.Label(f, textvariable=app.decoder_now_var, foreground=HINT_FG).grid(row=14, column=2, sticky="w", pady=(6, 0))
    app._hint(f, 15, "4Kなど重い映像でPCの負荷を減らします。使えないPCでは自動でソフトウェアに切り替わります")

    app._hint(f, 16, "出力先「PhoneCam（専用カメラ）」は OBS や Discord のカメラ一覧に「PhoneCam」と表示されます。"
                      "OBSの仮想カメラと同時に使えます" +
                      ("（初回は下の「登録する」が必要です）" if _is_frozen() else "（初回は register.bat が必要）"), pady=(4, 0))
    app._build_cam_registration(f, 17)


def _hw_changed(app):
    app.link.hw_disabled = False  # 前回失敗していても、入れ直したら試す
    app._changed("hw_decode", bool(app.hw_var.get()), None)


def _build_power(app, parent):
    f = app._section(parent, "省電力")
    app.screen_black_var = tk.BooleanVar(value=bool(app.settings["screen_black"]))
    ttk.Checkbutton(f, text="iPhoneの画面を真っ黒にする", variable=app.screen_black_var,
                    command=lambda: app._screen_changed(
                        "screen_black", bool(app.screen_black_var.get()))).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=2)
    ttk.Label(f, text="操作がないと自動で黒くする").grid(row=1, column=0, sticky="w", pady=2)
    app.screen_auto_var = tk.StringVar(value=app._value_to_label(SCREEN_AUTO_LABELS, app.settings["screen_auto"]))
    cb = ttk.Combobox(f, textvariable=app.screen_auto_var, values=[l for _, l in SCREEN_AUTO_LABELS],
                      state="readonly", width=10)
    cb.grid(row=1, column=1, sticky="w", padx=6)
    cb.bind("<<ComboboxSelected>>", lambda e: app._screen_changed(
        "screen_auto", app._label_to_value(SCREEN_AUTO_LABELS, app.screen_auto_var.get())))
    app.screen_state_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.screen_state_var, foreground=HINT_FG).grid(
        row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
    app._hint(f, 3, "画面を黒くしても配信は続きます。発熱とバッテリーの消費を抑えられます")


def _screen_changed(app, key, value):
    app._screen_user_t = time.monotonic()
    app._changed(key, value, "screen")


def _on_backend(app):
    app._changed("backend", app._label_to_value(BACKEND_LABELS, app.backend_var.get()), None)
    app.link.vcam.request_reset()
    app.stream.note_in_use(False, time.monotonic())
    app._stream_tick_once()


def _build_cam_registration(app, f, row):
    ttk.Label(f, text="PhoneCamカメラ").grid(row=row, column=0, sticky="w", pady=(8, 2))
    box = ttk.Frame(f)
    box.grid(row=row, column=1, columnspan=2, sticky="w", padx=6, pady=(8, 2))
    app.cam_status_var = tk.StringVar(value="")
    ttk.Label(box, textvariable=app.cam_status_var).pack(side="left", padx=(0, 10))
    app.cam_reg_btn = ttk.Button(box, text="登録する（更新）", command=lambda: app._start_elevated("register"))
    app.cam_reg_btn.pack(side="left", padx=2)
    app.cam_unreg_btn = ttk.Button(box, text="登録を解除", command=lambda: app._start_elevated("unregister"))
    app.cam_unreg_btn.pack(side="left", padx=2)
    app.cam_note_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.cam_note_var, foreground="#c0392b", wraplength=px(480),
              justify="left").grid(row=row + 1, column=0, columnspan=3, sticky="w")
    app._hint(f, row + 2, CAM_REG_EXPLAIN, pady=(2, 0))
    if not _is_frozen():
        app.cam_status_var.set("exe 版のみ（ソース実行では register.bat を使います）")
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
    app.cam_status_var.set(app.CAM_STATUS_TEXT.get(st, st))
    app._set_enabled(app.cam_reg_btn, st != "unavailable" and not app._elev_busy)
    app._set_enabled(app.cam_unreg_btn, st in ("ok", "outdated") and not app._elev_busy)
    return st


def _start_elevated(app, action):
    if app._elev_busy or not _is_frozen():
        return
    app._elev_busy = True
    app._elev_result = None
    app.cam_note_var.set("管理者の確認が表示されたら「はい」を押してください…")
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
    word = "登録" if action == "register" else "登録解除"
    if kind == "cancelled":
        app.cam_note_var.set("管理者の確認がキャンセルされたため、%sはしませんでした。もう一度ボタンを押してください。" % word)
    elif kind == "ok":
        if action == "register" and st == "ok":
            app.cam_note_var.set("")
            messagebox.showinfo("PhoneCam", "専用カメラ「PhoneCam」を登録しました。OBS や Discord のカメラ一覧に表示されます。"
                                            "（すでにOBSで開いている場合は、ソースを開き直してください）")
        elif action == "unregister" and st in ("missing", "unavailable"):
            app.cam_note_var.set("")
            messagebox.showinfo("PhoneCam", "専用カメラ「PhoneCam」の登録を解除しました。")
        else:
            app.cam_note_var.set("%sの処理は終わりましたが、状態が変わっていません。もう一度お試しください。" % word)
    elif kind == "timeout":
        app.cam_note_var.set("%sの処理が時間内に終わりませんでした。もう一度お試しください。" % word)
    else:
        app.cam_note_var.set("%sに失敗しました。管理者の確認で「はい」を押したか、他のアプリが「PhoneCam」カメラを使用中でないか確認してください。" % word)


# -- 接続 ---------------------------------------------------------------
def toggle_connect(app):
    if not app.wanted:
        if app.settings["mode"] == "wifi" and not app.settings["ip"].strip():
            messagebox.showerror("PhoneCam", "iPhoneのIPアドレスを入力してください（iPhoneのアプリ画面に表示されています）")
            return
        if app.settings["mode"] == "wifi" and not valid_pair_code(app.settings.get("pair_code", "")):
            messagebox.showerror("PhoneCam", "ペアリングコードを入力してください。iPhoneアプリの画面に表示されている6桁の数字です"
                                             "（iPhoneで「Wi-Fiからの接続を許可」をオンにすると表示されます）")
            return
        need_pv = app.settings.get("backend") != "phonecam"
        if av is None or np is None or (need_pv and pyvirtualcam is None):
            messagebox.showerror("PhoneCam", "必要なライブラリ(av, numpy, pyvirtualcam)が見つかりません。install.bat を実行してください")
            return
        app.wanted = True
        app.connect_btn.config(text="切断")
        app.link.start()  # 認証失敗で止まっていた場合も、新しく始めるので再試行になる
    else:
        app.wanted = False
        app.connect_btn.config(text="接続")
        app.link.stop()
        app.stream.reset_user()


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_connection(parent)
    app._build_run_control(parent)
    app._build_video(parent)
    app._build_power(parent)


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
