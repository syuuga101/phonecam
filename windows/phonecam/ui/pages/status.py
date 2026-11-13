# -*- coding: utf-8 -*-
"""状態ページ (iPhone 状態・映像・PC・遅延・温度グラフ・診断)
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
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import page, row, section


def _value_label(r, var, **kw):
    """row() の右側に値ラベルを置く。"""
    lbl = ttk.Label(r, textvariable=var, justify="right", anchor="e", **kw)
    lbl.pack(side="right")
    return lbl


def _value_rows(app, card, specs, store, labels=None):
    for key, icon, name in specs:
        r = row(card, icon, t(name))
        var = tk.StringVar(value="-")
        lbl = _value_label(r, var, wraplength=px(300))
        store[key] = var
        if labels is not None:
            labels[key] = lbl


def _build_phone_status(app, parent):
    pg = page(parent)
    app._status_page = pg
    app._default_fg = theme.colors()["text"]
    app.st_vars = {}
    app.st_labels = {}
    f = section(pg, t("status.iphone.title"))
    _value_rows(app, f, [
        ("thermal", "thermometer", "status.iphone.thermal"),
        ("pressure", "gauge", "status.iphone.pressure"),
        ("factors", "info", "status.iphone.factors"),
        ("hot", "clock", "status.iphone.hot"),
        ("battery", "battery", "status.iphone.battery"),
        ("lowpower", "battery", "status.iphone.lowpower"),
    ], app.st_vars, app.st_labels)
    app.ver_note_var = tk.StringVar(value="")
    app._ver_note_label = ttk.Label(f, textvariable=app.ver_note_var, foreground=theme.colors()["text2"],
                                    wraplength=px(480), justify="left")
    app._ver_note_label.pack(anchor="w", pady=(6, 0))
    # 映像 (iPhone から届く送信側の数字)
    v = section(pg, t("status.video.title"))
    _value_rows(app, v, [
        ("sentfps", "video", "status.video.sentfps"),
        ("dropped", "video", "status.video.dropped"),
        ("dropreasons", "info", "status.video.dropreasons"),
        ("recdropped", "record", "status.video.recdropped"),
        ("encode", "clock", "status.video.encode"),
        ("processing", "sparkles", "status.video.processing"),
    ], app.st_vars, app.st_labels)
    ttk.Label(v, text=t("status.video.hint"), foreground=theme.colors()["text2"]).pack(anchor="w", pady=(6, 0))


def _build_pc_status(app, parent):
    pg = app._status_page
    f = section(pg, t("status.pc.title"))
    app.pc_vars = {}
    _value_rows(app, f, [
        ("codec", "video", "status.pc.codec"),
        ("decoder", "gear", "status.pc.decoder"),
        ("out", "aspect", "status.pc.out"),
        ("watch", "camera", "status.pc.watch"),
    ], app.pc_vars)
    app.watch_var = app.pc_vars["watch"]
    app.dshow_var = tk.StringVar(value="")
    r = row(f, "camera", t("status.pc.dshow"))
    _value_label(r, app.dshow_var, wraplength=px(300))
    app.thin_var = tk.StringVar(value="")
    app._thin_label = ttk.Label(f, textvariable=app.thin_var, foreground=theme.colors()["live"],
                                wraplength=px(480), justify="left")
    app._thin_label.pack(anchor="w", pady=(6, 0))
    ttk.Label(f, text=t("status.pc.watch_hint"), foreground=theme.colors()["text2"], wraplength=px(480),
              justify="left").pack(anchor="w", pady=(6, 0))


def _update_pc_status(app, snap):
    connected = snap["connected"]
    name = CODEC_NAMES.get(snap["codec_in_use"]) if connected else None
    if connected and not name:
        name = CODEC_NAMES.get(snap["phone_status"].get("codec"))  # まだ映像が来ていないときは iPhone の申告値
    app.codec_now_var.set(t("status.pc.now", v=name or "-"))
    app.pc_vars["codec"].set(name or "-")
    dec = DECODER_LABELS.get(snap["decoder"]) if connected else None
    app.decoder_now_var.set(t("status.pc.now", v=dec or "-") if app.settings.get("hw_decode", True)
                             else t("status.pc.now", v=dec or t("status.pc.software")))
    text = dec or "-"
    if dec and snap.get("decoder_note"):
        text += t("status.pc.note", v=snap["decoder_note"])
    app.pc_vars["decoder"].set(text)
    size = canvas_size(app.settings)
    app.pc_vars["out"].set("%dx%d" % size if size else t("status.pc.out_native"))
    thin = snap.get("thin_total", 0)
    if connected and thin and time.monotonic() - snap.get("thin_last", 0.0) < THIN_NOTICE_SEC:
        app.thin_var.set(t("status.pc.thin", text=THIN_TEXT, n=thin))
    else:
        app.thin_var.set("")


def _build_latency(app, parent):
    pg = app._status_page
    f = section(pg, t("status.latency.title"))
    app.lat_vars = {}
    for key, icon, name in [("iphone", "camera", "status.latency.iphone"), ("comm", "wifi", "status.latency.comm"),
                            ("pc", "gear", "status.latency.pc"), ("total", "clock", "status.latency.total"),
                            ("rtt", "clock", "status.latency.rtt")]:
        r = row(f, icon, t(name))
        var = tk.StringVar(value="-")
        if key == "total":
            _value_label(r, var, font=theme.font("strong", "bold"))
        else:
            _value_label(r, var)
        app.lat_vars[key] = var
    ttk.Label(f, text=t("status.latency.hint"), foreground=theme.colors()["text2"]).pack(anchor="w", pady=(6, 0))
    # 診断
    dg = section(pg, t("status.diag.title"))
    r = row(dg, "clock", t("status.diag.timer"), t("status.diag.timer_hint"))
    ttk.Button(r, text=t("status.diag.timer_open"), command=app.open_timer).pack(side="right")


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
    win.title(t("status.timer.title"))
    win.attributes("-topmost", True)
    col = theme.colors()
    win.configure(background=col["bg"])
    win.bind("<Escape>", lambda e: win.destroy())
    t0 = time.perf_counter()
    label = tk.Label(win, text="00:00.000", font=("Consolas", 72, "bold"), background=col["bg"],
                     foreground=col["text"], padx=30, pady=16)
    label.pack()
    tk.Label(win, text=t("status.timer.help"),
             font=("", 10), background=col["bg"], foreground=col["text2"], wraplength=px(560),
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
    f = section(app._status_page, t("status.graph.title"))
    app._graph_range = 5
    bar = ttk.Frame(f)
    bar.pack(anchor="e", pady=(0, 4))
    app.graph_range_var = tk.StringVar(value=str(app._graph_range))
    for m in GRAPH_RANGES:
        ttk.Radiobutton(bar, text=t("status.graph.minutes", n=m), value=str(m), variable=app.graph_range_var,
                        style="Toolbutton", command=app._graph_range_changed).pack(side="left", padx=1)
    app.graph = tk.Canvas(f, height=px(214), highlightthickness=0, borderwidth=0, background=theme.colors()["card"])
    app.graph.pack(fill="x")
    app.graph.bind("<Configure>", lambda e: setattr(app, "_graph_dirty", True))

    def recolor():
        c = theme.colors()
        try:
            app.graph.config(background=c["card"])
            app._default_fg = c["text"]
            app._thin_label.config(foreground=c["live"])
            app._graph_dirty = True
            app._graph_drawn = 0.0
        except Exception:
            pass

    theme.on_change(recolor)


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
    c.config(background=theme.colors()["card"])
    W = c.winfo_width()
    if W < px(200):
        return
    minutes = getattr(app, "_graph_range", 5)
    N = minutes * 60
    window = graph_window(app.thermal_log.samples, N)
    L, R = px(40), px(10)
    pw = W - L - R
    col = theme.colors()
    grey, dark, font_s, font_b = col["text2"], col["text"], ("", 8), ("", 9, "bold")

    def X(i):
        return L + pw * i / float(N)

    # --- 温度 ---
    cur_lv = window[-1][0]
    cur_text = THERMAL_LABELS[THERMAL_LEVELS[cur_lv]] if cur_lv is not None else "-"
    y = px(4)
    c.create_text(L, y, text=t("status.graph.thermal"), anchor="nw", fill=dark, font=font_b)
    c.create_text(W - R, y, anchor="ne", fill=grey, font=font_s,
                  text=t("status.graph.current", level=cur_text, hot=format_mmss(app.thermal_log.hot_seconds)))
    sy0, sy1 = px(24), px(24) + px(28)
    c.create_rectangle(L, sy0, L + pw, sy1, fill=col['separator'], width=0)
    for start, length, lv in level_runs(window):
        if lv is None:
            continue
        x0, x1 = X(start), X(start + length)
        c.create_rectangle(x0, sy0, max(x1, x0 + 1), sy1, fill=THERMAL_BLOCK_COLORS[THERMAL_LEVELS[lv]], width=0)
    lx = L
    ly = sy1 + px(10)
    chip = px(10)
    for name, key in ((t("status.graph.nominal"), "nominal"), (t("status.graph.fair"), "fair"),
                      (t("status.graph.serious"), "serious"), (t("status.graph.critical"), "critical")):
        c.create_rectangle(lx, ly - chip // 2, lx + chip, ly + chip // 2, fill=THERMAL_BLOCK_COLORS[key], width=0)
        tid = c.create_text(lx + chip + px(4), ly, text=name, anchor="w", fill=grey, font=font_s)
        bb = c.bbox(tid)
        lx = (bb[2] if bb else lx + px(60)) + px(14)
    c.create_rectangle(lx, ly - chip // 2, lx + chip, ly + chip // 2, fill=col['separator'], width=0)
    c.create_text(lx + chip + px(4), ly, text=t("status.graph.nodata"), anchor="w", fill=grey, font=font_s)

    # --- 受信fps ---
    y = px(96)
    samples = list(app.thermal_log.samples)
    cur_fps = samples[-1][1] if samples and samples[-1][0] is not None and samples[-1][1] >= 0.5 else None
    c.create_text(L, y, text=t("status.graph.fps"), anchor="nw", fill=dark, font=font_b)
    c.create_text(W - R, y, anchor="ne", fill=dark if cur_fps is not None else grey, font=font_b,
                  text=t("status.graph.fps_value", v=cur_fps) if cur_fps is not None else "-")
    top, bottom = px(118), px(118) + px(70)
    ph = bottom - top

    def Y(v):
        return bottom - ph * min(max(v, 0.0), GRAPH_FPS_MAX) / GRAPH_FPS_MAX

    for v in (0, 30, 60):
        yy = Y(v)
        c.create_line(L, yy, L + pw, yy, fill=col["separator"])
        c.create_text(L - px(6), yy, text=str(v), anchor="e", fill=grey, font=font_s)
    lw = max(1, px(1.5))
    for run in fps_runs(window):
        if len(run) == 1:
            x, v = run[0]
            r = lw
            c.create_oval(X(x + 0.5) - r, Y(v) - r, X(x + 0.5) + r, Y(v) + r, fill=col["accent"], width=0)
            continue
        pts = []
        for i, v in run:
            pts += [X(i + 0.5), Y(v)]
        c.create_line(*pts, fill=col["accent"], width=lw, capstyle="round", joinstyle="round")
    ty = bottom + px(6)
    c.create_text(L, ty, text=range_label(minutes), anchor="nw", fill=grey, font=font_s)
    c.create_text(L + pw / 2.0, ty, text=range_label(minutes, 0.5), anchor="n", fill=grey, font=font_s)
    c.create_text(L + pw, ty, text=t("status.graph.now"), anchor="ne", fill=grey, font=font_s)


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
        t("status.video.dropped_fmt", cam=cam_d, net=net_d)
        if isinstance(cam_d, (int, float)) and isinstance(net_d, (int, float)) else "-")
    app.st_vars["dropreasons"].set(format_drop_reasons(st.get("cameraDropReasons")))
    rec = st.get("recording")
    rd = rec.get("droppedFrames") if isinstance(rec, dict) else None
    app.st_vars["recdropped"].set("%d" % rd if isinstance(rd, (int, float)) and not isinstance(rd, bool) else "-")
    enc = st.get("encodeMs")
    app.st_vars["encode"].set("%.1f ms" % enc if isinstance(enc, (int, float)) else "-")
    color = {"fair": "#b7791f", "serious": "#d9730d", "critical": theme.colors()["live"]}.get(thermal, app._default_fg)
    try:
        app.st_labels["thermal"].config(
            foreground=color, font=("Segoe UI Semibold", 9) if thermal == "critical" else "TkDefaultFont")
    except Exception:
        pass
    if "batteryLevel" in st or "batteryState" in st:
        app.st_vars["battery"].set(format_battery(st.get("batteryLevel", -1), st.get("batteryState", "unknown")))
    else:
        app.st_vars["battery"].set("-")
    lp = st.get("lowPowerMode")
    app.st_vars["lowpower"].set("-" if lp is None else (t("status.iphone.on") if lp else t("status.iphone.off")))
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
    """このページの中身を parent に作る (App._build_ui から呼ばれる)。"""
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
