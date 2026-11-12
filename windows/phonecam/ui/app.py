# -*- coding: utf-8 -*-
"""App 本体: ウィンドウ・タブ作成・ポーリング・トースト・共通ヘルパ (_slider/_mode_combo/_hint/_changed)・起動 (main)
"""

import os
import sys
import time
from phonecam.core.audio_engine import AudioEngine
from phonecam.core.canvas import build_control
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
from phonecam.ui.dpi import apply_ui_scale, enable_dpi_awareness, px
from phonecam.ui.labels import HINT_FG, METER_MIN_DB, MODE3_LABELS, PHONE_NOTE_SECONDS, THROTTLE_MS
from phonecam.ui.tkdeps import messagebox, tk, ttk
from phonecam.ui import dshow_ui, phone_sync, update_ui
from phonecam.ui.pages import audio, connection_video, effects, quality, recording, setup, shortcuts, status


class App:
    def __init__(self, root):
        self.root = root
        migrate_legacy_settings()
        self._setup_key_present = settings_has_key("setup_done")  # None=設定ファイルなし(新規) / False=旧バージョンの設定
        self.settings = load_settings()
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

        root.title("PhoneCam 受信 v%s" % APP_VERSION)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.minsize(px(560), px(560))
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d" % (min(px(640), int(sw * 0.92)), min(px(720), int(sh * 0.9))))
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
            self.link.send_control(build_control(self.settings, group, self.link.last_frame_size))

        self._pending[group] = self.root.after(THROTTLE_MS, fire)

    def _changed(self, key, value, group):
        self.settings[key] = value
        self._schedule_save()
        if group:
            self._schedule_send(group)

    # -- UI 構築 ----------------------------------------------------------
    def _section(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(10, 6))
        frame.pack(fill="x", padx=10, pady=5)
        frame.columnconfigure(1, weight=1)
        return frame

    def _build_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # 熱警告バナー (どのタブを開いていても見えるよう下部に表示)
        self.warn_var = tk.StringVar(value="")
        self.warn_label = ttk.Label(outer, textvariable=self.warn_var, foreground="#c0392b",
                                    font=("", 9, "bold"), wraplength=px(560), justify="left", padding=(14, 6))
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True, padx=4, pady=(6, 4))
        self.upd_check_var = tk.BooleanVar(value=bool(self.settings.get("update_check", True)))
        self.upd_status_var = tk.StringVar(value="最終確認: まだ確認していません")
        try:
            self._build_update_banner(outer)
        except Exception:  # 更新バナーの不具合でアプリ本体を止めない
            self._upd_banner = None
        self._canvases = []
        self.root.bind_all("<MouseWheel>", self._on_wheel)

        p1 = self._page("接続・映像")
        connection_video.build(self, p1)
        p2 = self._page("画質調整")
        quality.build(self, p2)
        p3 = self._page("エフェクト")
        effects.build(self, p3)
        p_rec = self._page("録画")
        recording.build(self, p_rec)
        p_aud = self._page("音声")
        audio.build(self, p_aud)
        p4 = self._page("状態")
        status.build(self, p4)
        p_keys = self._page("ショートカット")
        try:
            shortcuts.build(self, p_keys)
        except Exception as e:  # ショートカット画面の不具合でアプリ本体を止めない
            ttk.Label(p_keys, text="ショートカットの画面を表示できませんでした (%s)" % e).pack(padx=14, pady=14, anchor="w")
        p_setup = self._page("セットアップ")
        self._setup_tab = p_setup
        try:
            setup.build(self, p_setup)
        except Exception as e:  # セットアップ画面の不具合でアプリ本体を止めない
            self.setup = None
            ttk.Label(p_setup, text="セットアップ画面を表示できませんでした (%s)" % e).pack(padx=14, pady=14, anchor="w")
        if self.setup is None:
            try:
                self._build_update_section(p_setup, None)
            except Exception:
                pass
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._toast_label = None
        self._toast_job = None

    def _page(self, title):
        """スクロール可能なタブページを作り、中身を載せるフレームを返す。"""
        page = ttk.Frame(self.nb)
        self.nb.add(page, text=title)
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self._canvases.append(canvas)
        return body

    def _on_wheel(self, e):
        try:
            canvas = self._canvases[self.nb.index("current")]
            canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    def _hint(self, parent, row, text, **grid):
        lbl = ttk.Label(parent, text=text, foreground=HINT_FG, wraplength=px(480), justify="left")
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

    CAM_STATUS_TEXT = {"ok": "登録済み", "missing": "未登録", "outdated": "更新が必要", "unavailable": "利用できません"}

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
        ttk.Label(parent, text="モード").grid(row=row, column=0, sticky="w", pady=2)
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

    HOTKEY_TAKEN_TEXT = "このキーは他のアプリが使っています"

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
                self._toast_label = tk.Label(self.root, bg="#1d1d1f", fg="#ffffff", font=("Yu Gothic UI", 11, "bold"),
                                             padx=18, pady=8, borderwidth=0)
            self._toast_label.config(text=text)
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
        self.phone_mute_var.set("iPhoneでミュート中" if connected_now and st.get("micMuted") else "")
        self.fps_var.set("%.1f" % snap["recv_fps"] if snap["connected"] else "-")
        self.dec_var.set("%.1f ms" % snap["decode_ms"] if snap["connected"] else "-")
        self.res_var.set(snap["resolution"])
        self.error_var.set(snap["error_text"])
        self._update_pc_status(snap)

        if st:
            self._apply_ranges(st)
            parts = []
            if "iso" in st:
                parts.append("現在のISO %d" % round(st["iso"]))
            if st.get("exposureSeconds"):
                parts.append("シャッター 1/%d" % round(1.0 / st["exposureSeconds"]))
            self.cur_exp_var.set(" / ".join(parts))
            if "temperature" in st:
                self.cur_wb_var.set("現在の色温度 %dK / 色かぶり %d" % (round(st["temperature"]), round(st.get("tint", 0))))
            if "lensPosition" in st:
                self.cur_focus_var.set("現在の位置 %.2f" % st["lensPosition"])
        connected = snap["connected"]
        self._update_phone_status(st if connected else {})
        now = time.monotonic()
        if connected and not self._was_connected:
            self._screen_user_t = now  # 接続直後は送った設定が反映される前なので、iPhone側の値で上書きしない
        self._was_connected = connected
        self._update_v13(st if connected else {}, connected, snap, now)
        self.root.after(100, self._poll)

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
        save_settings(self.settings)
        self.root.destroy()


# 各ページ/機能モジュールの関数を App のメソッドとして取り付ける (呼び出し側は従来どおり self._xxx)
for _mod in (connection_video, quality, effects, recording, audio, status, shortcuts, setup,
             phone_sync, dshow_ui, update_ui):
    for _name in _mod.METHODS:
        setattr(App, _name, getattr(_mod, _name))
del _mod, _name


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
    helper = helper_main(sys.argv[1:])
    if helper is not None:
        return helper
    if tk is None:
        print("tkinter が使用できません。Python を公式インストーラーで再インストールしてください。")
        return 1
    enable_dpi_awareness()  # Tk を作る前に (作ったあとでは効かない)
    root = tk.Tk()
    apply_ui_scale(root)
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    _APP_REF.append(App(root))
    root.mainloop()
    return 0


def _show_fatal(exc):
    """windowed の exe ではコンソールが無いので、起動時エラーをダイアログで表示する。"""
    import traceback
    detail = traceback.format_exc()
    try:
        r = tk.Tk() if tk is not None else None
        if r is not None:
            r.withdraw()
            messagebox.showerror("PhoneCam", "起動中にエラーが発生しました。\n\n%s" % detail[-1500:])
            r.destroy()
            return
    except Exception:
        pass
    try:
        sys.stderr.write(detail)
    except Exception:
        pass
