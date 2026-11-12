# -*- coding: utf-8 -*-
"""ショートカットタブ (グローバルホットキー)
"""

import time
from phonecam.core.deps import phonecam_hotkeys
from phonecam.core.settings import LENSES
from phonecam.ui.dpi import px
from phonecam.ui.labels import BG_MODE_LABELS, LENS_LABELS
from phonecam.ui.tkdeps import tk, ttk


def _hotkey_init(app):
    """起動時: ショートカットを登録して、押された動作を取り出すポーリングを始める。"""
    app.hotkeys = None
    app._hk_capture = None      # キー入力待ちの動作名
    app._hk_taken = {}          # 動作名 → True (他のアプリが使用中)
    if phonecam_hotkeys is None or not phonecam_hotkeys.is_windows():
        return
    try:
        app.hotkeys = phonecam_hotkeys.HotkeyManager()
        app._hotkey_apply()
    except Exception:
        app.hotkeys = None
        return
    app.root.after(app.HOTKEY_POLL_MS, app._hotkey_tick)


def _hotkey_bindings(app):
    if not app.settings.get("hotkeys_enabled", True):
        return {}
    return dict(app.settings.get("hotkeys") or {})


def _hotkey_apply(app):
    """設定どおりに登録し直す。登録できなかった動作は「他のアプリが使っています」を出す。"""
    res = {}
    if app.hotkeys is not None:
        try:
            res = app.hotkeys.apply(app._hotkey_bindings())
        except Exception:
            res = {}
    app._hk_taken = {a: True for a, ok in res.items() if ok is False}
    app._hotkey_refresh_ui()


def _hotkey_tick(app):
    if app._closing or app.hotkeys is None:
        return
    try:
        while True:
            action = app.hotkeys.poll()
            if action is None:
                break
            app._hotkey_fire(action)
    except Exception:
        pass
    app.root.after(app.HOTKEY_POLL_MS, app._hotkey_tick)


def _hotkey_fire(app, action):
    """ショートカットが押された。動作を実行して、短いお知らせを出す。"""
    try:
        text = app._hotkey_do(action)
    except Exception as e:
        text = "ショートカットを実行できませんでした (%s)" % e
    if text:
        app._toast(text)


def _hotkey_do(app, action):
    s = app.settings
    if action == "camera":
        st = app.state.snapshot()["phone_status"] if app.state.snapshot()["connected"] else {}
        avail = [l for l in (st.get("lenses") or []) if l in LENSES] or list(LENSES)
        cur = s.get("lens")
        nxt = avail[(avail.index(cur) + 1) % len(avail)] if cur in avail else avail[0]
        if nxt == cur:
            return "カメラ: 切り替えできるレンズがありません"
        app._lens_changed(nxt)
        return "カメラ: %s" % dict(LENS_LABELS)[nxt]
    if action == "mic":
        muted = not bool(s.get("mic_muted", False))
        app.mic_muted_var.set(muted)
        app._changed("mic_muted", muted, "micMuted")
        return "マイク: ミュート" if muted else "マイク: ミュート解除"
    if action == "blur":
        on = s.get("bg_mode") == "off"
        mode = "blur" if on else "off"
        app.bg_mode_var.set(app._value_to_label(BG_MODE_LABELS, mode))
        app._changed("bg_mode", mode, "background")
        app._update_enabled()
        return "背景ぼかし: オン" if on else "背景ぼかし: オフ"
    if action == "pause":
        connected = app.state.snapshot()["connected"]
        if not connected:
            return "カメラ: iPhoneにつながっていません"
        running = app.stream.desired(connected, time.monotonic())
        app._stream_button()
        return "カメラ: 一時停止" if running else "カメラ: 開始"
    if action == "rec":
        snap = app.state.snapshot()
        if not snap["connected"]:
            return "録画: iPhoneにつながっていません"
        rec = snap["phone_status"].get("recording")
        active = bool(rec.get("active")) if isinstance(rec, dict) else False
        app._rec_clicked()
        return "録画: 停止" if active else "録画: 開始"
    if action in ("zoom_in", "zoom_out"):
        step = app.ZOOM_STEP if action == "zoom_in" else -app.ZOOM_STEP
        cur = float(s.get("zoom", 1.0))
        new = round(min(app.ZOOM_MAX, max(app.ZOOM_MIN, round((cur + step) / app.ZOOM_STEP) * app.ZOOM_STEP)), 4)
        if new != cur:
            app.zoom_var.set(new)
            if s.get("zoom") != new:
                app._changed("zoom", new, "zoom")
        return "ズーム: %.2fx" % new
    return None


def _build_hotkeys(app, parent):
    hk = phonecam_hotkeys
    f = app._section(parent, "ショートカットキー")
    app.hk_enabled_var = tk.BooleanVar(value=bool(app.settings.get("hotkeys_enabled", True)))
    ttk.Checkbutton(f, text="ショートカットキーを使う", variable=app.hk_enabled_var,
                    command=app._hotkey_master_toggled).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
    app._hint(f, 1, "OBSやゲームを操作しているときでも、PhoneCamを操作できます。"
                     "「変更」を押してから、使いたいキーを押してください（Ctrl・Alt・Shift・Winのどれかと一緒に押します）")
    app.hk_key_vars = {}
    app.hk_note_vars = {}
    app.hk_buttons = {}
    grid = ttk.Frame(f)
    grid.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    grid.columnconfigure(0, weight=1)
    for i, (action, label, _d) in enumerate(hk.ACTIONS):
        ttk.Label(grid, text=label).grid(row=i * 2, column=0, sticky="w", pady=(6, 0))
        kv = tk.StringVar(value="")
        app.hk_key_vars[action] = kv
        ttk.Label(grid, textvariable=kv, font=("", 10, "bold"), width=22, anchor="center").grid(
            row=i * 2, column=1, padx=6, pady=(6, 0))
        b = ttk.Button(grid, text="変更", width=6, command=lambda a=action: app._hotkey_capture_start(a))
        b.grid(row=i * 2, column=2, pady=(6, 0))
        app.hk_buttons[action] = b
        ttk.Button(grid, text="なし", width=6, command=lambda a=action: app._hotkey_set(a, "")).grid(
            row=i * 2, column=3, padx=(4, 0), pady=(6, 0))
        nv = tk.StringVar(value="")
        app.hk_note_vars[action] = nv
        ttk.Label(grid, textvariable=nv, foreground="#c0392b").grid(row=i * 2 + 1, column=0, columnspan=4, sticky="w")
    row = len(hk.ACTIONS) * 2
    ttk.Button(grid, text="すべて初期設定に戻す", command=app._hotkey_reset).grid(
        row=row, column=0, columnspan=4, sticky="w", pady=(10, 0))
    app.hk_msg_var = tk.StringVar(value="")
    ttk.Label(f, textvariable=app.hk_msg_var, foreground="#b7791f", wraplength=px(480), justify="left").grid(
        row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
    if not hk.is_windows():
        app._hint(f, 4, "ショートカットキーはWindowsでだけ使えます", pady=(4, 0))
    app._hotkey_refresh_ui()


def _hotkey_refresh_ui(app):
    hk = phonecam_hotkeys
    if hk is None or not hasattr(app, "hk_key_vars"):
        return
    capturing = getattr(app, "_hk_capture", None)
    enabled = bool(app.settings.get("hotkeys_enabled", True))
    for action in hk.ACTION_NAMES:
        combo = (app.settings.get("hotkeys") or {}).get(action, "")
        if capturing == action:
            app.hk_key_vars[action].set("キーを押してください…")
            app.hk_buttons[action].config(text="取消")
        else:
            app.hk_key_vars[action].set(hk.combo_text(combo))
            app.hk_buttons[action].config(text="変更")
        taken = enabled and combo and getattr(app, "_hk_taken", {}).get(action)
        app.hk_note_vars[action].set(app.HOTKEY_TAKEN_TEXT if taken else "")


def _hotkey_master_toggled(app):
    app._changed("hotkeys_enabled", bool(app.hk_enabled_var.get()), None)
    app._hotkey_capture_stop()
    app._hotkey_apply()


def _hotkey_set(app, action, combo):
    """動作のキーを変える ("" でなし)。他の動作と同じキーは断る。"""
    hk = phonecam_hotkeys
    keys = dict(app.settings.get("hotkeys") or {})
    if combo:
        for other, c in keys.items():
            if other != action and c == combo:
                app.hk_msg_var.set("%s は「%s」で使っています。先にそちらを変えてください" % (
                    hk.combo_text(combo), hk.ACTION_LABELS[other]))
                app._hotkey_capture_stop()
                return False
    keys[action] = combo
    app._changed("hotkeys", keys, None)
    app.hk_msg_var.set("")
    app._hotkey_capture_stop()
    app._hotkey_apply()
    return True


def _hotkey_reset(app):
    app._changed("hotkeys", dict(phonecam_hotkeys.DEFAULT_HOTKEYS), None)
    app.hk_msg_var.set("")
    app._hotkey_capture_stop()
    app._hotkey_apply()


def _hotkey_capture_start(app, action):
    if getattr(app, "_hk_capture", None) == action:
        app._hotkey_capture_stop()
        return
    app._hk_capture = action
    app.hk_msg_var.set("使いたいキーを押してください（Ctrl・Alt・Shift・Winのどれかと一緒に押します）。Escで取り消します")
    try:
        app.root.bind("<KeyPress>", app._hotkey_on_key)
        app.root.focus_force()
    except Exception:
        pass
    # 入力待ちの間は登録を外す (今のキーを押し直したときに、反応せず取り込めるように)
    if app.hotkeys is not None:
        try:
            app.hotkeys.apply({})
        except Exception:
            pass
    app._hotkey_refresh_ui()


def _hotkey_capture_stop(app):
    was = getattr(app, "_hk_capture", None)
    app._hk_capture = None
    try:
        app.root.unbind("<KeyPress>")
    except Exception:
        pass
    if was is not None:
        app._hotkey_apply()
    app._hotkey_refresh_ui()


def _hotkey_on_key(app, event):
    action = getattr(app, "_hk_capture", None)
    if action is None:
        return None
    hk = phonecam_hotkeys
    if event.keysym == "Escape":
        app.hk_msg_var.set("")
        app._hotkey_capture_stop()
        return "break"
    if event.keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
                        "Win_L", "Win_R", "Super_L", "Super_R", "Meta_L", "Meta_R"):
        return "break"  # 修飾キーだけでは決めない。続けて押すのを待つ
    combo = hk.combo_from_tk_event(event.state, event.keysym, getattr(event, "keycode", None), hk.win_key_down())
    if combo is None:
        app.hk_msg_var.set("Ctrl・Alt・Shift・Winのどれかと、文字・数字・F1〜F12・矢印キーなどを一緒に押してください")
        return "break"
    app._hotkey_set(action, combo)
    return "break"


def build(app, parent):
    """このタブの中身を parent に作る (App._build_ui から呼ばれる)。"""
    app._build_hotkeys(parent)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_hotkey_init',
    '_hotkey_bindings',
    '_hotkey_apply',
    '_hotkey_tick',
    '_hotkey_fire',
    '_hotkey_do',
    '_build_hotkeys',
    '_hotkey_refresh_ui',
    '_hotkey_master_toggled',
    '_hotkey_set',
    '_hotkey_reset',
    '_hotkey_capture_start',
    '_hotkey_capture_stop',
    '_hotkey_on_key',

)
