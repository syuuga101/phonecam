# -*- coding: utf-8 -*-
"""状態タブ (iPhone 状態・PC 状態・遅延・熱グラフ)
"""

import time
from phonecam.core.canvas import canvas_size
from phonecam.core.decoder import CODEC_NAMES, THIN_NOTICE_SEC
from phonecam.core.settings import format_drop_reasons
from phonecam.core.stats import (
    GRAPH_FPS_MAX,
    GRAPH_RANGES,
    PRESSURE_LABELS,
    THERMAL_BLOCK_COLORS,
    THERMAL_LEVELS,
    THERMAL_NO_DATA_COLOR,
    format_mmss,
    format_pressure_factors,
    fps_runs,
    graph_window,
    level_runs,
    range_label,
)
from phonecam.ui.dpi import px
from phonecam.ui.labels import (
    DECODER_LABELS,
    THERMAL_LABELS,
    THERMAL_WARNING,
    THIN_TEXT,
    format_battery,
    is_thermal_warning,
)
from phonecam.ui.tkdeps import tk, ttk


def _build_phone_status(app, parent):
    f = app._section(parent, "iPhoneの状態")
    app._default_fg = ttk.Style().lookup("TLabel", "foreground") or "#1d1d1f"
    app.st_vars = {}
    app.st_labels = {}
    for row, (key, name) in enumerate([("thermal", "温度"), ("pressure", "カメラの負荷"), ("factors", "原因"),
                                       ("hot", "「高い」以上だった時間"), ("sentfps", "送信fps"),
                                       ("dropped", "落ちたフレーム"), ("dropreasons", "カメラで落ちた理由"),
                                       ("recdropped", "録画で落ちたフレーム"), ("encode", "エンコード時間"),
                                       ("battery", "バッテリー"), ("lowpower", "低電力モード"),
                                       ("processing", "エフェクト処理時間")]):
        ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value="-")
        lbl = ttk.Label(f, textvariable=var)
        lbl.grid(row=row, column=1, sticky="w", padx=6)
        app.st_vars[key] = var
        app.st_labels[key] = lbl
    app._hint(f, 12, "接続中のiPhoneから約1秒ごとに更新されます", pady=(6, 0))
    app.ver_note_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.ver_note_var, foreground="#6e6e73", wraplength=px(480),
              justify="left").grid(row=13, column=0, columnspan=3, sticky="w", pady=(4, 0))


def _build_pc_status(app, parent):
    f = app._section(parent, "PCでの受信・出力")
    app.pc_vars = {}
    for row, (key, name) in enumerate([("codec", "受信中のコーデック"), ("decoder", "デコーダー"),
                                       ("out", "出力サイズ（仮想カメラ）"), ("watch", "使用中の検出")]):
        ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value="-")
        ttk.Label(f, textvariable=var, wraplength=px(340), justify="left").grid(row=row, column=1, sticky="w", padx=6)
        app.pc_vars[key] = var
    app.watch_var = app.pc_vars["watch"]
    app.dshow_var = tk.StringVar(value="")
    ttk.Label(f, text="OBSのカメラ設定ダイアログ連携").grid(row=4, column=0, sticky="w", pady=2)
    ttk.Label(f, textvariable=app.dshow_var, wraplength=px(340), justify="left").grid(row=4, column=1, columnspan=2, sticky="w", padx=6)
    app.thin_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.thin_var, foreground="#c0392b", wraplength=px(480), justify="left").grid(
        row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
    app._hint(f, 5, "「使用中の検出」は、OBSやDiscordが専用カメラを開いているかをPhoneCamが見ている結果です。"
                     "「未使用」のままカメラが動かないときは、アプリ側でカメラを一度オフ→オンにしてください", pady=(6, 0))


def _update_pc_status(app, snap):
    connected = snap["connected"]
    name = CODEC_NAMES.get(snap["codec_in_use"]) if connected else None
    if connected and not name:
        name = CODEC_NAMES.get(snap["phone_status"].get("codec"))  # まだ映像が来ていないときは iPhone の申告値
    app.codec_now_var.set("現在: %s" % (name or "-"))
    app.pc_vars["codec"].set(name or "-")
    dec = DECODER_LABELS.get(snap["decoder"]) if connected else None
    app.decoder_now_var.set("現在: %s" % (dec or "-") if app.settings.get("hw_decode", True)
                             else "現在: %s" % (dec or "ソフトウェア"))
    text = dec or "-"
    if dec and snap.get("decoder_note"):
        text += "（%s）" % snap["decoder_note"]
    app.pc_vars["decoder"].set(text)
    size = canvas_size(app.settings)
    app.pc_vars["out"].set("%dx%d" % size if size else "映像のサイズそのまま")
    thin = snap.get("thin_total", 0)
    if connected and thin and time.monotonic() - snap.get("thin_last", 0.0) < THIN_NOTICE_SEC:
        app.thin_var.set("%s（%d コマ）" % (THIN_TEXT, thin))
    else:
        app.thin_var.set("")


def _build_latency(app, parent):
    f = app._section(parent, "遅延（直近の平均）")
    app.lat_vars = {}
    for row, (key, name) in enumerate([("iphone", "iPhone処理"), ("comm", "通信"), ("pc", "PC処理"),
                                       ("total", "合計"), ("rtt", "往復(RTT)")]):
        ttk.Label(f, text=name).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value="-")
        ttk.Label(f, textvariable=var, font=("", 9, "bold") if key == "total" else "TkDefaultFont").grid(
            row=row, column=1, sticky="w", padx=6)
        app.lat_vars[key] = var
    app._hint(f, 5, "カメラの露光やOBSの表示までの時間は含みません", pady=(6, 0))
    ttk.Button(f, text="計測用タイマーを開く", command=app.open_timer).grid(
        row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))


def open_timer(app):
    """実測用の大きなミリ秒タイマー。カメラに映して OBS のプレビューと一緒に撮ると遅延が分かる。"""
    if app._timer_win is not None:
        try:
            app._timer_win.lift()
            return
        except Exception:
            app._timer_win = None
    win = tk.Toplevel(app.root)
    app._timer_win = win
    win.title("計測用タイマー")
    win.attributes("-topmost", True)
    win.configure(background="#ffffff")
    win.bind("<Escape>", lambda e: win.destroy())
    t0 = time.perf_counter()
    label = tk.Label(win, text="00:00.000", font=("Consolas", 72, "bold"), background="#ffffff",
                     foreground="#1d1d1f", padx=30, pady=16)
    label.pack()
    tk.Label(win, text="カメラをこのタイマーに向け、OBSのプレビューに映るタイマーと実物のタイマーを"
                       "一緒にスクリーンショットすると、合計の遅延が分かります",
             font=("", 10), background="#ffffff", foreground="#666666", wraplength=px(560),
             justify="center", padx=20, pady=(0)).pack(pady=(0, 16))
    job = {"id": None}

    def tick():
        ms = int((time.perf_counter() - t0) * 1000)
        label.config(text="%02d:%02d.%03d" % (ms // 60000 % 100, ms // 1000 % 60, ms % 1000))
        job["id"] = win.after(5, tick)

    def closed(_e=None):
        if _e is not None and _e.widget is not win:
            return
        if job["id"] is not None:
            try:
                win.after_cancel(job["id"])
            except Exception:
                pass
        app._timer_win = None

    win.bind("<Destroy>", closed)
    tick()


def _build_thermal_graph(app, parent):
    f = app._section(parent, "温度と受信fpsの履歴")
    app._graph_range = 5
    bar = ttk.Frame(f)
    bar.grid(row=0, column=0, columnspan=3, sticky="e", pady=(0, 4))
    app.graph_range_var = tk.StringVar(value=str(app._graph_range))
    for m in GRAPH_RANGES:
        ttk.Radiobutton(bar, text="%d分" % m, value=str(m), variable=app.graph_range_var, style="Toolbutton",
                        command=app._graph_range_changed).pack(side="left", padx=1)
    app.graph = tk.Canvas(f, height=px(214), highlightthickness=0, borderwidth=0, background="#fbfbfd")
    app.graph.grid(row=1, column=0, columnspan=3, sticky="ew")
    f.columnconfigure(0, weight=1)
    app.graph.bind("<Configure>", lambda e: setattr(app, "_graph_dirty", True))


def _graph_range_changed(app):
    try:
        m = int(app.graph_range_var.get())
    except Exception:
        m = 5
    app._graph_range = m if m in GRAPH_RANGES else 5
    app._graph_dirty = True
    app._graph_drawn = 0.0  # 範囲の切替はすぐ描き直す


def _draw_graph(app):
    """上: 温度の帯 (1秒=1ブロック)。下: 受信fpsの折れ線。どちらも右端が「今」。"""
    c = app.graph
    c.delete("all")
    W = c.winfo_width()
    if W < px(200):
        return
    minutes = getattr(app, "_graph_range", 5)
    N = minutes * 60
    window = graph_window(app.thermal_log.samples, N)
    L, R = px(40), px(10)
    pw = W - L - R
    grey, dark, font_s, font_b = "#8e8e93", "#1d1d1f", ("", 8), ("", 9, "bold")

    def X(i):
        return L + pw * i / float(N)

    # --- 温度 ---
    cur_lv = window[-1][0]
    cur_text = THERMAL_LABELS[THERMAL_LEVELS[cur_lv]] if cur_lv is not None else "-"
    y = px(4)
    c.create_text(L, y, text="温度", anchor="nw", fill=dark, font=font_b)
    c.create_text(W - R, y, anchor="ne", fill=grey, font=font_s,
                  text="現在: %s　　高い以上の累計 %s" % (cur_text, format_mmss(app.thermal_log.hot_seconds)))
    sy0, sy1 = px(24), px(24) + px(28)
    c.create_rectangle(L, sy0, L + pw, sy1, fill=THERMAL_NO_DATA_COLOR, width=0)
    for start, length, lv in level_runs(window):
        if lv is None:
            continue
        x0, x1 = X(start), X(start + length)
        c.create_rectangle(x0, sy0, max(x1, x0 + 1), sy1, fill=THERMAL_BLOCK_COLORS[THERMAL_LEVELS[lv]], width=0)
    lx = L
    ly = sy1 + px(10)
    chip = px(10)
    for name, key in (("正常", "nominal"), ("やや高い", "fair"), ("高い", "serious"), ("危険", "critical")):
        c.create_rectangle(lx, ly - chip // 2, lx + chip, ly + chip // 2, fill=THERMAL_BLOCK_COLORS[key], width=0)
        t = c.create_text(lx + chip + px(4), ly, text=name, anchor="w", fill=grey, font=font_s)
        bb = c.bbox(t)
        lx = (bb[2] if bb else lx + px(60)) + px(14)
    c.create_rectangle(lx, ly - chip // 2, lx + chip, ly + chip // 2, fill=THERMAL_NO_DATA_COLOR, width=0)
    c.create_text(lx + chip + px(4), ly, text="データなし", anchor="w", fill=grey, font=font_s)

    # --- 受信fps ---
    y = px(96)
    samples = list(app.thermal_log.samples)
    cur_fps = samples[-1][1] if samples and samples[-1][0] is not None and samples[-1][1] >= 0.5 else None
    c.create_text(L, y, text="受信fps", anchor="nw", fill=dark, font=font_b)
    c.create_text(W - R, y, anchor="ne", fill=dark if cur_fps is not None else grey, font=font_b,
                  text="%.0f fps" % cur_fps if cur_fps is not None else "-")
    top, bottom = px(118), px(118) + px(70)
    ph = bottom - top

    def Y(v):
        return bottom - ph * min(max(v, 0.0), GRAPH_FPS_MAX) / GRAPH_FPS_MAX

    for v in (0, 30, 60):
        yy = Y(v)
        c.create_line(L, yy, L + pw, yy, fill="#e3e3e8" if v else "#c7c7cc")
        c.create_text(L - px(6), yy, text=str(v), anchor="e", fill=grey, font=font_s)
    lw = max(2, px(2))
    for run in fps_runs(window):
        if len(run) == 1:
            x, v = run[0]
            r = lw
            c.create_oval(X(x + 0.5) - r, Y(v) - r, X(x + 0.5) + r, Y(v) + r, fill="#0071e3", width=0)
            continue
        pts = []
        for i, v in run:
            pts += [X(i + 0.5), Y(v)]
        c.create_line(*pts, fill="#0071e3", width=lw, capstyle="round", joinstyle="round")
    ty = bottom + px(6)
    c.create_text(L, ty, text=range_label(minutes), anchor="nw", fill=grey, font=font_s)
    c.create_text(L + pw / 2.0, ty, text=range_label(minutes, 0.5), anchor="n", fill=grey, font=font_s)
    c.create_text(L + pw, ty, text="今", anchor="ne", fill=grey, font=font_s)


def _update_phone_status(app, st):
    app._update_note_phone_version(st.get("appVersion"))
    thermal = st.get("thermalState")
    app.st_vars["thermal"].set(THERMAL_LABELS.get(thermal, "-") if thermal else "-")
    level = st.get("pressureLevel")
    app.st_vars["pressure"].set(PRESSURE_LABELS.get(level, "-") if level else "-")
    app.st_vars["factors"].set(format_pressure_factors(st.get("pressureFactors")) if level else "-")
    sfps = st.get("sentFps")
    app.st_vars["sentfps"].set("%.1f" % sfps if isinstance(sfps, (int, float)) else "-")
    cam_d, net_d = st.get("cameraDroppedFrames"), st.get("networkDroppedFrames")
    app.st_vars["dropped"].set(
        "カメラ %d / 通信 %d" % (cam_d, net_d)
        if isinstance(cam_d, (int, float)) and isinstance(net_d, (int, float)) else "-")
    app.st_vars["dropreasons"].set(format_drop_reasons(st.get("cameraDropReasons")))
    rec = st.get("recording")
    rd = rec.get("droppedFrames") if isinstance(rec, dict) else None
    app.st_vars["recdropped"].set("%d" % rd if isinstance(rd, (int, float)) and not isinstance(rd, bool) else "-")
    enc = st.get("encodeMs")
    app.st_vars["encode"].set("%.1f ms" % enc if isinstance(enc, (int, float)) else "-")
    color = {"fair": "#b7791f", "serious": "#d9730d", "critical": "#c0392b"}.get(thermal, app._default_fg)
    try:
        app.st_labels["thermal"].config(
            foreground=color, font=("", 9, "bold") if thermal == "critical" else "TkDefaultFont")
    except Exception:
        pass
    if "batteryLevel" in st or "batteryState" in st:
        app.st_vars["battery"].set(format_battery(st.get("batteryLevel", -1), st.get("batteryState", "unknown")))
    else:
        app.st_vars["battery"].set("-")
    lp = st.get("lowPowerMode")
    app.st_vars["lowpower"].set("-" if lp is None else ("オン" if lp else "オフ"))
    pm = st.get("processingMs")
    app.st_vars["processing"].set("%.1f ms" % pm if isinstance(pm, (int, float)) else "-")

    warn = is_thermal_warning(thermal)
    if warn and not app.warn_var.get():
        app.warn_var.set(THERMAL_WARNING)
        app.warn_label.pack(side="bottom", fill="x", before=app.nb)
    elif not warn and app.warn_var.get():
        app.warn_var.set("")
        app.warn_label.pack_forget()


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_phone_status(parent)
    app._build_pc_status(parent)
    app._build_latency(parent)
    app._build_thermal_graph(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_build_phone_status',
    '_build_pc_status',
    '_update_pc_status',
    '_build_latency',
    'open_timer',
    '_build_thermal_graph',
    '_graph_range_changed',
    '_draw_graph',
    '_update_phone_status',

)
