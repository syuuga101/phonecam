# -*- coding: utf-8 -*-
"""App 本体: ウィンドウ・タブ作成・ポーリング・トースト・共通ヘルパ (_slider/_mode_combo/_hint/_changed)・起動 (main)
"""

import os
import re
import sys
import time
from phonecam.core.audio_engine import AudioEngine
from phonecam.core.canvas import build_control
from phonecam.core import crashlog
from phonecam.core.crashlog import log_error as _log_error
from phonecam.core.deps import phonecam_setup
from phonecam.core.elevate import ELEVATED_TIMEOUT_MS, run_elevated
from phonecam.core.link import Link
from phonecam.core.paths import WINDOWS_DIR, _is_frozen
from phonecam.core.settings import (
    AUDIO_AVAILABLE,
    load_settings,
    migrate_legacy_settings,
    save_settings,
    settings_has_key,
)
from phonecam.core.softcam_reg import helper_main, helper_register, registration_status
from phonecam.core.softcam_shmem import ConsumerWatcher, SoftcamMemoryReader
from phonecam.core.state import SharedState
from phonecam.core.stats import ThermalLog
from phonecam.core.stream import StreamController
from phonecam.core.vcam import CAM_RELEASE_HOLD_SEC
from phonecam.core.version import APP_VERSION
from phonecam.ui.dpi import apply_ui_scale, enable_dpi_awareness, px, set_app_user_model_id
from phonecam.i18n import set_language, t
from phonecam.ui import icons, theme
from phonecam.ui.labels import METER_MIN_DB, MODE3_LABELS, PHONE_NOTE_SECONDS, THROTTLE_MS
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui.util import webbrowser_open
from phonecam.ui import dshow_ui, phone_sync, update_ui
from phonecam.ui.pages import audio, connection_video, effects, quality, recording, setup, shortcuts, status


# 左ナビ: (キー, アイコン名)。順番が nb のページ番号 (STATUS_TAB_INDEX = 6 もこれに合わせる)
NAV_ITEMS = [("connection", "link"), ("video", "video"), ("quality", "sun"), ("effects", "sparkles"),
             ("audio", "mic"), ("recording", "record"), ("status", "gauge"),
             ("shortcuts", "keyboard"), ("settings", "gear")]
SETUP_INDEX = len(NAV_ITEMS)  # セットアップはナビに出さない (設定から open_setup() で開く)


class _FrameWrapper:
    """ttk.Frame を継承せず包む (tk の無い環境/テストの差し替えでも import・構築できるように)。
    pack/pack_forget などは中の frame に委譲し、str() は frame のパス (pack(before=...) 用)。"""

    def __getattr__(self, name):
        if name == "frame":
            raise AttributeError(name)
        return getattr(self.frame, name)

    def __str__(self):
        return str(self.frame)


class _ContentArea(_FrameWrapper):
    """右の内容欄。一度に1ページだけ表示する。旧 ttk.Notebook の index()/select() の互換 shim。"""

    def __init__(self, master, on_select):
        self.frame = ttk.Frame(master)
        self._pages = []
        self._cur = -1
        self._on_select = on_select

    def add_page(self):
        host = ttk.Frame(self.frame)
        self._pages.append(host)
        return host

    def tabs(self):
        return [str(p) for p in self._pages]

    def index(self, what):
        if what == "current":
            return self._cur
        for i, p in enumerate(self._pages):
            if what is p or str(what) == str(p):
                return i
        raise tk.TclError("tab not found")

    def select(self, what=None):
        if what is None:
            return str(self._pages[self._cur]) if self._cur >= 0 else ""
        i = what if isinstance(what, int) else self.index(what)
        if i == self._cur or not (0 <= i < len(self._pages)):
            return
        if self._cur >= 0:
            self._pages[self._cur].pack_forget()
        self._cur = i
        self._pages[i].pack(fill="both", expand=True)
        self._on_select(i)


class _Nav(_FrameWrapper):
    """Windows 11 設定アプリ風の左ナビ (アイコン + ラベル)。選択行は薄い面 + 左のアクセントバー。"""

    def __init__(self, master, items, on_click):
        self.frame = ttk.Frame(master, width=px(200))
        self.frame.pack_propagate(False)
        self._items = items
        self._on_click = on_click
        self._rows = []
        self._sel = -1
        self.icon_lbl = ttk.Label(self.frame)
        self.icon_lbl.pack(anchor="w", padx=px(16), pady=(px(14), px(10)))
        self._set_app_icon()
        for i, (key, icon) in enumerate(items):
            row = tk.Frame(self.frame, cursor="hand2", takefocus=1)
            bar = tk.Frame(row, width=px(3))
            ic = tk.Label(row, borderwidth=0)
            tx = tk.Label(row, text=t("nav." + key), anchor="w", borderwidth=0, font=theme.font("body"))
            row.pack(fill="x", padx=px(8), pady=px(1))
            bar.pack(side="left", fill="y", pady=px(8))
            ic.pack(side="left", padx=(px(10), px(12)), pady=px(8))
            tx.pack(side="left", fill="x", expand=True)
            for w in (row, bar, ic, tx):
                w.bind("<Button-1>", lambda e, n=i: self._on_click(n))
                w.bind("<Enter>", lambda e, n=i: self._hover(n, True))
                w.bind("<Leave>", lambda e, n=i: self._hover(n, False))
            row.bind("<Up>", lambda e, n=i: self._on_click(max(0, n - 1)))
            row.bind("<Down>", lambda e, n=i: self._on_click(min(len(items) - 1, n + 1)))
            row.bind("<Return>", lambda e, n=i: self._on_click(n))
            self._rows.append((row, bar, ic, tx, icon))
        theme.on_change(self.restyle)
        self.restyle()

    def _set_app_icon(self):
        img = icons.app_icon(20)
        if img is not None:
            self.icon_lbl.configure(image=img)
            self.icon_lbl._img = img

    def _hover(self, n, on):
        if n != self._sel:
            c = theme.colors()
            self._paint(n, c["select"] if on else c["bg"], False)

    def _paint(self, n, bg, selected):
        c = theme.colors()
        row, bar, ic, tx, icon = self._rows[n]
        for w in (row, ic, tx):
            w.configure(bg=bg)
        bar.configure(bg=c["accent"] if selected else bg)
        tx.configure(fg=c["text"])
        img = icons.get(icon, 16, c["accent"] if selected else None)
        if img is not None:
            ic.configure(image=img)
            ic._img = img

    def restyle(self):
        self._set_app_icon()
        c = theme.colors()
        for n in range(len(self._rows)):
            self._paint(n, c["select"] if n == self._sel else c["bg"], n == self._sel)

    def highlight(self, n):
        self._sel = n
        self.restyle()


class App:
    def __init__(self, root):
        self.root = root
        migrate_legacy_settings()
        self._setup_key_present = settings_has_key("setup_done")  # None=設定ファイルなし(新規) / False=旧バージョンの設定
        self.settings = load_settings()
        set_language(self.settings.get("language", "system"))
        theme.init(root, self.settings.get("theme", "system"))
        self.state = SharedState()
        self.audio = AudioEngine(lambda: (self.settings["mic_volume"], self.settings["mic_mute"],
                                          self.settings["mic_delay_ms"]))
        self.stream = StreamController(self.settings)
        self.link = Link(self.settings, self.state, self._get_mode_ip, audio=self.audio, stream=self.stream)
        self.watcher = ConsumerWatcher(SoftcamMemoryReader())
        # カメラを作り直す直前に共有メモリの監視を手放す (掴んだままだと同じ名前で作り直せない)
        self.link.vcam.release_hooks.append(lambda: self.watcher.reader.close(hold=CAM_RELEASE_HOLD_SEC))
        self._vcam_gen = -1
        self._closing = False
        self.wanted = False
        self.thermal_log = ThermalLog()
        self._last_sample = 0.0
        self._graph_dirty = True
        self._was_connected = False
        self._screen_user_t = 0.0
        self._rec_lock_until = 0.0
        self._audio_retry_t = 0.0
        self._meter_db = METER_MIN_DB
        self._timer_win = None
        self._pending = {}
        self._slider_vars = {}
        self._mode_vars = {}
        self._ui_sync = False
        self.dshowctl = None
        self._dshow_reason = ""
        self._dshow_retry_t = 0.0
        self._save_job = None
        self.focus_widgets = []
        self.manual_widgets = {}
        self._range_cache = {}
        self._elev_busy = False
        self._elev_result = None
        self.setup = None
        self._wizard = None
        self._setup_last_check = 0.0
        self._upd_release = None        # 最後に取得した最新リリース (phonecam_update.parse_release)
        self._upd_checking = False
        self._upd_box = []
        self._upd_dismissed = set()     # 「あとで」にした版 (この起動中だけ)
        self._upd_pending_wizard = False
        self._upd_dl = None             # ダウンロード中の状態
        self._upd_notes_win = None
        self._phone_ver = ""
        if phonecam_setup is not None:
            try:
                self.setup = phonecam_setup.SetupController(
                    root, can_start=lambda: not self._elev_busy, on_busy=self._setup_busy,
                    on_changed=self._setup_changed, audio_guard=self._make_audio_guard())
            except Exception:
                self.setup = None

        root.title("PhoneCam")
        icons.set_window_icon(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.minsize(px(560), px(560))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d" % (min(px(640), int(sw * 0.92)), min(px(720), int(sh * 0.9))))
        self._restore_window_state()
        self._build_ui()
        self._update_enabled()
        self._apply_audio()
        self._hotkey_init()
        self._dshow_init()
        self._poll()
        self._meter_tick()
        self._stream_tick()
        root.after(600, self._first_run_check)
        root.after(2500, self._audio_startup_check)
        self._update_init()
        root.after(0, theme.reapply_fonts)   # UI 構築後にもう一度 (sv-ttk のフォント直書きに負けないように)
        root.after(300, theme.reapply_fonts)

    def _restore_window_state(self):
        root = self.root
        try:
            if self.settings.get("window_maximized", True):
                root.state("zoomed")
                return
            g = self.settings.get("tk_window_geometry", "")
            m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", g or "")
            if not m:
                return
            w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
            root.update_idletasks()
            vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
            vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
            if vw <= 1 or vh <= 1:
                vx, vy, vw, vh = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
            if x < vx + vw and x + w > vx and y < vy + vh and y + h > vy:
                root.geometry(g)
        except Exception:
            pass

    # -- 補助 -------------------------------------------------------------
    def _get_mode_ip(self):
        return self.settings["mode"], self.settings["ip"].strip()

    def _label_to_value(self, pairs, label):
        for v, l in pairs:
            if l == label:
                return v
        return pairs[0][0]

    def _value_to_label(self, pairs, value):
        for v, l in pairs:
            if v == value:
                return l
        return pairs[0][1]

    def _schedule_save(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._do_save)

    def _do_save(self):
        self._save_job = None
        save_settings(self.settings)

    def _schedule_send(self, group):
        """同じグループの送信は THROTTLE_MS ごとに最新値で1回だけ送る。"""
        if group in self._pending:
            return

        def fire():
            self._pending.pop(group, None)
            try:
                self.link.send_control(build_control(self.settings, group, self.link.last_frame_size))
            except Exception:
                pass  # 送れなくても画面は止めない (次の変更でまた送る)

        self._pending[group] = self.root.after(THROTTLE_MS, fire)

    def _changed(self, key, value, group):
        self.settings[key] = value
        self._schedule_save()
        if group:
            self._schedule_send(group)

    # -- UI 構築 ----------------------------------------------------------
    def set_theme(self, mode):
        """設定 theme ("system"/"light"/"dark") を保存して即反映する。"""
        self._changed("theme", mode, None)
        theme.set_mode(mode)

    def set_language(self, lang):
        """設定 language を保存する (文言は次回起動で切り替わる)。"""
        self._changed("language", lang, None)
        set_language(lang)

    def open_setup(self):
        """セットアップ画面を内容欄に出す (ナビでは「設定」を選択状態のままにする)。"""
        self.nb.select(SETUP_INDEX)

    def _section(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
        frame.pack(fill="x", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        self.nav = _Nav(outer, NAV_ITEMS, lambda n: self.nb.select(n))
        self.nav.pack(side="left", fill="y")
        right = ttk.Frame(outer)
        right.pack(side="left", fill="both", expand=True)
        self._right = right

        # 熱警告バナー (どのページを開いていても見えるよう下部に表示)
        self.warn_var = tk.StringVar(value="")
        self.warn_label = ttk.Label(right, textvariable=self.warn_var, foreground=theme.colors()["live"],
                                    font=theme.font("caption", "bold"), wraplength=px(560), justify="left", padding=(14, 6))
        theme.on_change(lambda: self.warn_label.configure(foreground=theme.colors()["live"]))
        self.nb = _ContentArea(right, self._page_selected)
        self.nb.pack(fill="both", expand=True)
        self.upd_check_var = tk.BooleanVar(value=bool(self.settings.get("update_check", True)))
        self.upd_status_var = tk.StringVar(value=t("update.status.never"))
        try:
            self._build_update_banner(right)
        except Exception:  # 更新バナーの不具合でアプリ本体を止めない
            self._upd_banner = None
        self._canvases = []  # 旧互換 (スクロールは widgets.page が管理)

        # ページをまたいで使う変数は、ビルド順に依存しないよう先に作っておく
        # (各ページが同名で作り直しても、読み書きする側は常に何か持っている)
        self.codec_now_var = tk.StringVar(value=t("video.now.none"))
        self.decoder_now_var = tk.StringVar(value="")
        self.watch_var = tk.StringVar(value="")
        self.dshow_var = tk.StringVar(value="")
        self.cam_note_var = tk.StringVar(value="")
        self.ver_note_var = tk.StringVar(value="")

        hosts = [self._page(key) for key, _icon in NAV_ITEMS]
        p_setup = self._page("setup")
        self._setup_tab = p_setup

        def guarded(host, builder, what):
            try:
                builder()
            except Exception as e:  # 1ページの不具合でアプリ本体を止めない
                ttk.Label(host, text=t("shell.page_error", name=what, err=e), wraplength=px(480),
                          justify="left").pack(padx=14, pady=14, anchor="w")

        def placeholder(host):
            ttk.Label(host, text=t("shell.placeholder"), foreground=theme.colors()["text2"]).pack(padx=24, pady=24, anchor="w")

        # 接続 / 映像 (connection_video 担当が build_connection / build_video を公開する。無ければ build / 仮表示)
        if hasattr(connection_video, "build_connection"):
            connection_video.build_connection(self, hosts[0])
        else:
            connection_video.build(self, hosts[0])
        if hasattr(connection_video, "build_video"):
            connection_video.build_video(self, hosts[1])
        else:
            placeholder(hosts[1])
        quality.build(self, hosts[2])
        effects.build(self, hosts[3])
        audio.build(self, hosts[4])
        recording.build(self, hosts[5])
        status.build(self, hosts[6])
        guarded(hosts[7], lambda: shortcuts.build(self, hosts[7]), t("nav.shortcuts"))
        try:
            from phonecam.ui.pages import settings as settings_page
        except ImportError:
            settings_page = None
        if settings_page is not None and hasattr(settings_page, "build"):
            guarded(hosts[8], lambda: settings_page.build(self, hosts[8]), t("nav.settings"))
        else:
            placeholder(hosts[8])

        def build_setup():
            try:
                setup.build(self, p_setup)
            except Exception:
                self.setup = None
                raise
        guarded(p_setup, build_setup, t("shell.setup"))
        if self.setup is None:
            try:
                self._build_update_section(p_setup, None)
            except Exception:
                pass
        self._toast_label = None
        self._toast_job = None
        self.nb.select(0)

    def _page(self, key):
        """内容欄にページ用のホスト frame を1つ足して返す (中身は各ページの build が page() で作る)。"""
        return self.nb.add_page()

    def _page_selected(self, index):
        """内容欄のページが切り替わった (旧 <<NotebookTabChanged>> 相当)。"""
        try:
            self.nav.highlight(index if index < len(NAV_ITEMS) else len(NAV_ITEMS) - 1)
        except Exception:
            pass
        self._tab_changed()

    def _on_wheel(self, e):
        """旧互換 (スクロールは widgets の共通ハンドラが行う)。"""
        return None

    def _hint(self, parent, row, text, **grid):
        lbl = ttk.Label(parent, text=text, foreground=theme.colors()["text2"], wraplength=px(480), justify="left")
        theme.on_change(lambda: lbl.configure(foreground=theme.colors()["text2"]))
        lbl.grid(row=row, column=0, columnspan=3, sticky="w", **grid)

        # 画面の狭いノートPCでも見切れないよう、折り返し幅を親の実際の幅に合わせる
        def fit(event, _lbl=lbl):
            try:
                _lbl.configure(wraplength=max(px(160), event.width - px(16)))
            except Exception:
                pass
        try:
            parent.bind("<Configure>", fit, add="+")
        except Exception:
            pass
        return lbl

    def _slider(self, parent, row, label, key, lo, hi, group, fmt, round_to, as_int=False, var_init=None):
        """ラベル + スライダー + 値表示。戻り値: (scale, value_label)。"""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.DoubleVar(value=float(self.settings[key]))
        val_label = ttk.Label(parent, width=9, anchor="e")
        val_label.grid(row=row, column=2, padx=(6, 0))

        def show(v):
            val_label.config(text=fmt(v))

        show(var.get())
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal")
        scale.grid(row=row, column=1, sticky="ew", padx=6)

        def on_write(*_):
            try:
                v = float(var.get())
            except Exception:
                return
            v = round(v / round_to) * round_to
            if as_int:
                v = int(round(v))
            else:
                v = round(v, 4)
            show(v)
            if self._ui_sync:  # 設定からUIを合わせているだけのときは、保存も送信もしない
                return
            self._changed(key, v, group)

        var.trace_add("write", on_write)
        self._slider_vars[key] = var
        return scale, val_label, var

    @property
    def CAM_STATUS_TEXT(self):
        return {k: t("shell.cam." + k) for k in ("ok", "missing", "outdated", "unavailable")}

    def _tab_changed(self, _e=None):
        self._graph_dirty = True
        try:
            if self.setup is not None and self.nb.index("current") == self.nb.index(self._setup_tab):
                now = time.monotonic()
                if not self.setup.busy and now - self._setup_last_check > 3.0:
                    self._setup_last_check = now
                    self.setup.recheck()
        except Exception:
            pass

    def _do_save_now(self):
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        save_settings(self.settings)

    def _mode_combo(self, parent, row, key, group, on_change):
        ttk.Label(parent, text=t("shell.mode")).grid(row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=self._value_to_label(MODE3_LABELS, self.settings[key]))
        cb = ttk.Combobox(parent, textvariable=var, values=[l for _, l in MODE3_LABELS], state="readonly", width=14)
        cb.grid(row=row, column=1, sticky="w", padx=6)
        self._mode_vars[key] = var

        def sel(_e=None):
            self._changed(key, self._label_to_value(MODE3_LABELS, var.get()), group)
            on_change()

        cb.bind("<<ComboboxSelected>>", sel)
        return cb

    # -- 有効/無効 -----------------------------------------------------------
    @staticmethod
    def _set_enabled(widget, enabled):
        try:
            if enabled:
                widget.state(["!disabled"])
            else:
                widget.state(["disabled"])
        except Exception:
            pass

    def _update_enabled(self):
        s = self.settings
        self._set_enabled(self.bias_scale, s["exp_mode"] == "auto")
        manual_exp = s["exp_mode"] == "manual"
        self._set_enabled(self.iso_scale, manual_exp)
        self._set_enabled(self.shutter_cb, manual_exp)
        manual_wb = s["wb_mode"] == "manual"
        self._set_enabled(self.temp_scale, manual_wb)
        self._set_enabled(self.tint_scale, manual_wb)
        status = self.state.snapshot()["phone_status"]
        focus_ok = status.get("focusSupported", True)
        self._set_enabled(self.focus_mode_cb, focus_ok)
        self._set_enabled(self.focus_scale, focus_ok and s["focus_mode"] == "manual")
        self._set_enabled(self.torch_cb, status.get("torchAvailable", True))
        bg_on = s["bg_mode"] != "off"
        self._set_enabled(self.bg_strength_scale, bg_on)
        self._set_enabled(self.bg_quality_cb, bg_on)
        show_hint = s["bg_mode"] == "green"
        if show_hint != self.bg_hint_visible:
            self.bg_hint_visible = show_hint
            if show_hint:
                self.bg_hint.grid()
            else:
                self.bg_hint.grid_remove()

    HOTKEY_POLL_MS = 50

    @property
    def HOTKEY_TAKEN_TEXT(self):
        return t("shortcuts.taken")

    ZOOM_STEP = 0.25

    ZOOM_MIN, ZOOM_MAX = 1.0, 10.0

    def _toast(self, text):
        """画面の下に短いお知らせを出し、状態欄にも出す。"""
        self._phone_note = (text, time.monotonic() + 2.5)
        try:
            self.status_var.set(text)
        except Exception:
            pass
        try:
            if self._toast_label is None:
                self._toast_label = tk.Label(self.root, font=theme.font("large", "bold"),
                                             padx=18, pady=8, borderwidth=0)
            _c = theme.colors()  # 反転配色 (ライト=暗い帯 / ダーク=明るい帯)
            self._toast_label.config(text=text, bg=_c["text"], fg=_c["bg"])
            self._toast_label.place(relx=0.5, rely=1.0, y=-px(24), anchor="s")
            self._toast_label.lift()
            if self._toast_job is not None:
                self.root.after_cancel(self._toast_job)
            self._toast_job = self.root.after(2000, self._toast_hide)
        except Exception:
            pass

    def _toast_hide(self):
        self._toast_job = None
        try:
            if self._toast_label is not None:
                self._toast_label.place_forget()
        except Exception:
            pass

    DSHOW_POLL_MS = 200

    DSHOW_RETRY_SEC = 5.0

    # -- 状態表示のポーリング (メインスレッド) -----------------------------------
    def _poll(self):
        try:
            self._poll_once()
        except Exception:
            _log_error("poll")  # 1回の失敗でポーリングを止めない
        if not self._closing:
            self.root.after(100, self._poll)

    def _poll_once(self):
        snap = self.state.snapshot()
        st = snap["phone_status"]
        self.status_var.set(snap["status_text"])
        connected_now = snap["connected"]
        note = self._apply_phone_edit(st if connected_now else None, time.monotonic())
        if note:
            self._phone_note = (note, time.monotonic() + PHONE_NOTE_SECONDS)
        pn = getattr(self, "_phone_note", None)
        if pn and time.monotonic() < pn[1]:
            self.status_var.set(pn[0])
        self.phone_mute_var.set(t("shell.phone_muted") if connected_now and st.get("micMuted") else "")
        self.fps_var.set("%.1f" % snap["recv_fps"] if snap["connected"] else "-")
        self.dec_var.set("%.1f ms" % snap["decode_ms"] if snap["connected"] else "-")
        self.res_var.set(snap["resolution"])
        self.error_var.set(snap["error_text"])
        self._update_pc_status(snap)

        if st:
            self._apply_ranges(st)
            parts = []
            if "iso" in st:
                parts.append(t("shell.cur.iso", v=round(st["iso"])))
            if st.get("exposureSeconds"):
                parts.append(t("shell.cur.shutter", v=round(1.0 / st["exposureSeconds"])))
            self.cur_exp_var.set(" / ".join(parts))
            if "temperature" in st:
                self.cur_wb_var.set(t("shell.cur.wb", k=round(st["temperature"]), tint=round(st.get("tint", 0))))
            if "lensPosition" in st:
                self.cur_focus_var.set(t("shell.cur.focus", v="%.2f" % st["lensPosition"]))
        connected = snap["connected"]
        self._update_phone_status(st if connected else {})
        now = time.monotonic()
        if connected and not self._was_connected:
            self._screen_user_t = now  # 接続直後は送った設定が反映される前なので、iPhone側の値で上書きしない
        self._was_connected = connected
        self._update_v13(st if connected else {}, connected, snap, now)

    def _housekeeping(self):
        """1秒ごと: 音声ストリームが止まっていたら、3秒以上あけて開き直す。"""
        if AUDIO_AVAILABLE and self.settings["mic_enabled"] and not self.audio.running:
            if time.monotonic() - self._audio_retry_t >= 3.0:
                self._apply_audio()

    def on_close(self):
        self._closing = True
        try:
            if self.dshowctl is not None:
                self.dshowctl.close()
        except Exception:
            pass
        try:
            if self.hotkeys is not None:
                self.hotkeys.stop()
        except Exception:
            pass
        try:
            if self._upd_dl is not None:
                self._upd_dl["cancel"].set()
        except Exception:
            pass
        try:
            if self.setup is not None:
                self.setup.close()
        except Exception:
            pass
        try:
            self.link.stop()
            self.link.vcam.close()
            self.watcher.reset()
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        try:
            zoomed = self.root.state() == "zoomed"
            self.settings["window_maximized"] = zoomed
            if not zoomed:
                self.settings["tk_window_geometry"] = self.root.geometry()
        except Exception:
            pass
        save_settings(self.settings)
        self.root.destroy()


# 各ページ/機能モジュールの関数を App のメソッドとして取り付ける (呼び出し側は従来どおり self._xxx)
for _mod in (connection_video, quality, effects, recording, audio, status, shortcuts, setup,
             phone_sync, dshow_ui, update_ui):
    for _name in _mod.METHODS:
        setattr(App, _name, getattr(_mod, _name))
del _mod, _name
try:
    from phonecam.ui.pages import settings as _settings_page
    for _name in getattr(_settings_page, "METHODS", ()):
        setattr(App, _name, getattr(_settings_page, _name))
except ImportError:
    pass


def _bind_setup():
    """phonecam_setup へ、本体の関数を渡す (呼び出し時に本体側の最新の定義を参照する)。"""
    if phonecam_setup is None:
        return
    phonecam_setup.bind(
        is_frozen=lambda: _is_frozen(),
        registration_status=lambda: registration_status(),
        register_camera=lambda: helper_register(),
        run_elevated=lambda params, timeout_ms=ELEVATED_TIMEOUT_MS: run_elevated(params, timeout_ms),
        script_path=lambda: os.path.join(WINDOWS_DIR, "phonecam_receiver.py"),
        link_connected=lambda: _link_connected(),
    )


_APP_REF = []  # 起動中の App (セットアップ画面から接続状態を参照するため)


def _link_connected():
    try:
        return bool(_APP_REF and _APP_REF[0].state.connected)
    except Exception:
        return False


_bind_setup()


def main():
    args = sys.argv[1:]
    setup_window = args[:1] in (["--setup-wizard"], ["--setup-diagnose"])
    helper = None if setup_window else helper_main(args)
    if helper is not None:
        return helper
    if tk is None:
        print(t("shell.no_tk"))
        return 1
    set_app_user_model_id()  # タスクバーで python.exe ではなく PhoneCam として扱わせる
    enable_dpi_awareness()  # Tk を作る前に (作ったあとでは効かない)
    root = tk.Tk()
    crashlog.install(root)
    apply_ui_scale(root)
    if setup_window:
        settings = load_settings()
        set_language(settings.get("language", "system"))
        theme.init(root, settings.get("theme", "system"))
        root.update()
        if phonecam_setup is None:
            messagebox.showerror("PhoneCam", t("setup.unavailable"), parent=root)
            root.destroy()
            return 1
        if "--setup-diagnose" in args:
            dialog = phonecam_setup.open_diagnostics(root, open_url=webbrowser_open)
        else:
            controller = phonecam_setup.SetupController(root)

            def finish_setup():
                current = load_settings()
                current["setup_done"] = True
                save_settings(current)

            after_update = args[args.index("--after-update") + 1] if "--after-update" in args else None
            dialog = phonecam_setup.SetupWizard(root, controller, on_finish=finish_setup,
                                                open_url=webbrowser_open, update_version=after_update)
        if dialog is not None:
            dialog.top.transient("")
            dialog.top.deiconify()
            dialog.top.update()
            root.withdraw()
            root.wait_window(dialog.top)
        root.destroy()
        return 0 if dialog is not None else 1
    _APP_REF.append(App(root))
    root.mainloop()
    return 0


def _show_fatal(exc):
    """windowed の exe ではコンソールが無いので、起動時エラーをダイアログで表示する。"""
    import traceback
    detail = traceback.format_exc()
    _log_error("fatal", detail)
    try:
        r = tk.Tk() if tk is not None else None
        if r is not None:
            r.withdraw()
            messagebox.showerror("PhoneCam", t("shell.fatal", detail=detail[-1500:]))
            r.destroy()
            return
    except Exception:
        pass
    try:
        sys.stderr.write(detail)
    except Exception:
        pass
