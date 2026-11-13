# -*- coding: utf-8 -*-
"""ショートカットページ (グローバルホットキー)
"""

import time
from phonecam.core.deps import phonecam_hotkeys
from phonecam.core.settings import LENSES
from phonecam.ui.dpi import px
from phonecam.ui.labels import BG_MODE_LABELS, LENS_LABELS
from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.widgets import page, row, section


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
        text = t("shortcuts.toast.failed", err=e)
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
            return t("shortcuts.toast.no_lens")
        app._lens_changed(nxt)
        return t("shortcuts.toast.camera", name=dict(LENS_LABELS)[nxt])
    if action == "mic":
        muted = not bool(s.get("mic_muted", False))
        app.mic_muted_var.set(muted)
        app._changed("mic_muted", muted, "micMuted")
        return t("shortcuts.toast.mic_muted") if muted else t("shortcuts.toast.mic_unmuted")
    if action == "blur":
        on = s.get("bg_mode") == "off"
        mode = "blur" if on else "off"
        app.bg_mode_var.set(app._value_to_label(BG_MODE_LABELS, mode))
        app._changed("bg_mode", mode, "background")
        app._update_enabled()
        return t("shortcuts.toast.blur_on") if on else t("shortcuts.toast.blur_off")
    if action == "pause":
        connected = app.state.snapshot()["connected"]
        if not connected:
            return t("shortcuts.toast.not_connected")
        running = app.stream.desired(connected, time.monotonic())
        app._stream_button()
        return t("shortcuts.toast.paused") if running else t("shortcuts.toast.started")
    if action == "rec":
        snap = app.state.snapshot()
        if not snap["connected"]:
            return t("shortcuts.toast.rec_not_connected")
        rec = snap["phone_status"].get("recording")
        active = bool(rec.get("active")) if isinstance(rec, dict) else False
        app._rec_clicked()
        return t("shortcuts.toast.rec_stop") if active else t("shortcuts.toast.rec_start")
    if action in ("zoom_in", "zoom_out"):
        step = app.ZOOM_STEP if action == "zoom_in" else -app.ZOOM_STEP
        cur = float(s.get("zoom", 1.0))
        new = round(min(app.ZOOM_MAX, max(app.ZOOM_MIN, round((cur + step) / app.ZOOM_STEP) * app.ZOOM_STEP)), 4)
        if new != cur:
            app.zoom_var.set(new)
            if s.get("zoom") != new:
                app._changed("zoom", new, "zoom")
        return t("shortcuts.toast.zoom", v=new)
    return None


_ICONS = {"camera": "camera", "mic": "mic", "blur": "sparkles", "pause": "pause",
          "rec": "record", "zoom_in": "zoom", "zoom_out": "zoom"}


def _action_label(action):
    """動作名の表示。ロケールに無ければ phonecam_hotkeys の原文を使う。"""
    key = "shortcuts.action." + action
    text = t(key)
    if text == key:
        text = (phonecam_hotkeys.ACTION_LABELS.get(action, action) if phonecam_hotkeys else action)
    return text


def _build_hotkeys(app, parent):
    hk = phonecam_hotkeys
    pg = page(parent)
    f = section(pg, t("shortcuts.title"))
    app.hk_enabled_var = tk.BooleanVar(value=bool(app.settings.get("hotkeys_enabled", True)))
    r = row(f, "keyboard", t("shortcuts.enable.label"), t("shortcuts.enable.hint"))
    ttk.Checkbutton(r, variable=app.hk_enabled_var, style="Switch.TCheckbutton",
                    command=app._hotkey_master_toggled).pack(side="right")
    app.hk_key_vars = {}
    app.hk_note_vars = {}
    app.hk_buttons = {}
    keys = section(pg, t("shortcuts.list.title"))
    for action, _label, _d in hk.ACTIONS:
        r = row(keys, _ICONS.get(action, "keyboard"), _action_label(action))
        ttk.Button(r, text=t("shortcuts.key.none"), width=6,
                   command=lambda a=action: app._hotkey_set(a, "")).pack(side="right", padx=(4, 0))
        b = ttk.Button(r, text=t("shortcuts.key.change"), width=6,
                       command=lambda a=action: app._hotkey_capture_start(a))
        b.pack(side="right", padx=(6, 0))
        app.hk_buttons[action] = b
        kv = tk.StringVar(value="")
        app.hk_key_vars[action] = kv
        ttk.Label(r, textvariable=kv, font=theme.font("strong", "bold"), width=22, anchor="e").pack(side="right")
        nv = tk.StringVar(value="")
        app.hk_note_vars[action] = nv
        ttk.Label(keys, textvariable=nv, foreground=theme.colors()["live"]).pack(anchor="e")
    bar = ttk.Frame(keys)
    bar.pack(fill="x", pady=(10, 0))
    ttk.Button(bar, text=t("shortcuts.reset"), command=app._hotkey_reset).pack(side="left")
    app.hk_msg_var = tk.StringVar(value="")
    ttk.Label(keys, textvariable=app.hk_msg_var, foreground=theme.colors()["text2"], wraplength=px(480),
              justify="left").pack(anchor="w", pady=(6, 0))
    ttk.Label(keys, text=t("shortcuts.hint"), foreground=theme.colors()["text2"], wraplength=px(480),
              justify="left").pack(anchor="w", pady=(6, 0))
    if not hk.is_windows():
        ttk.Label(keys, text=t("shortcuts.windows_only"), foreground=theme.colors()["text2"]).pack(anchor="w", pady=(4, 0))
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
            app.hk_key_vars[action].set(t("shortcuts.key.waiting"))
            app.hk_buttons[action].config(text=t("shortcuts.key.cancel"))
        else:
            app.hk_key_vars[action].set(hk.combo_text(combo))
            app.hk_buttons[action].config(text=t("shortcuts.key.change"))
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
                app.hk_msg_var.set(t("shortcuts.msg.duplicate", key=hk.combo_text(combo),
                                         action=_action_label(other)))
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
    app.hk_msg_var.set(t("shortcuts.msg.capture"))
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
        app.hk_msg_var.set(t("shortcuts.msg.need_modifier"))
        return "break"
    app._hotkey_set(action, combo)
    return "break"


def build(app, parent):
    """このページの中身を parent に作る (App._build_ui から呼ばれる)。"""
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
