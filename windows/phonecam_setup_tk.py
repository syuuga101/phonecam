# -*- coding: utf-8 -*-
"""旧 Tk 版のセットアップ画面 (ウィザード・診断・コンポーネント一覧)。

Qt 版 (phonecam/qt/setup_wizard.py) に置き換え済み。Tk 版の本体 (phonecam_receiver.py / phonecam/ui) を
消すときに、このファイルもいっしょに消す。ロジック (検出・ジョブ・GuidedFlow・SetupController など) は
phonecam_setup.py にあり、ここはその名前を取り込んで画面だけを作る。
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import phonecam_setup as _logic

globals().update({k: v for k, v in vars(_logic).items() if not k.startswith("__")})

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# 色はテーマ (phonecam.ui.theme.colors()) から取る。取れないとき (単体実行・テスト) は下のライトの値。
_FALLBACK_COLORS = {"bg": "#fafafa", "card": "#ffffff", "text": "#1c1c1c", "text2": "#616161",
                    "separator": "#e5e5e5", "accent": "#005fb8", "live": "#c42b1c", "ok": "#0f7b0f"}

_CHECK_STYLE = "PCSetup.TCheckbutton"


def _sync_check_style():
    """チェックボックスの背景をウィザードの背景色 (BG) に固定する。
    sv-ttk の既定の背景/状態ごとの背景 (hover 等) が BG と違うと、インジケータの周りに四角い下地が見えるため。"""
    try:
        st = ttk.Style()
        st.configure(_CHECK_STYLE, background=BG, foreground=FG, focuscolor=BG)
        states = ("active", "pressed", "disabled", "selected", "focus", "!disabled")
        st.map(_CHECK_STYLE,
               background=[(x, BG) for x in states],
               foreground=[("disabled", DISABLED_FG), ("!disabled", FG)])
    except Exception:
        pass


def _checkbutton(parent, **kw):
    """ttk.Checkbutton (theme.py の DPI 対応インジケータが効く)。ttk が無い環境だけ tk.Checkbutton。"""
    if ttk is not None:
        _sync_check_style()
        return ttk.Checkbutton(parent, style=_CHECK_STYLE, **kw)
    return tk.Checkbutton(parent, **kw)


BG = PANEL = FG = SUB = LINE = ACCENT = GOOD = WARN = BAD = ""
ACCENT_FG = ACCENT_HOVER = PANEL_HOVER = DISABLED_FG = ""
_COLOR_NAMES = ("BG", "PANEL", "FG", "SUB", "LINE", "ACCENT", "GOOD", "WARN", "BAD",
                "ACCENT_FG", "ACCENT_HOVER", "PANEL_HOVER", "DISABLED_FG")


def _rgb(c):
    try:
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return 255, 255, 255


def _lum(c):
    r, g, b = _rgb(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _blend(c1, c2, f):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def _theme_colors():
    try:
        from phonecam.ui import theme as _theme
        c = dict(_theme.colors())
        for k, v in _FALLBACK_COLORS.items():
            c.setdefault(k, v)
        return c
    except Exception:
        return dict(_FALLBACK_COLORS)


def _apply_palette():
    """色の定数 (BG など) を現在のテーマの色にする。変更前の値の dict を返す。"""
    g = globals()
    old = {n: g.get(n, "") for n in _COLOR_NAMES}
    c = _theme_colors()
    dark = _lum(c["bg"]) < 0.5
    g["BG"] = c["bg"]
    g["PANEL"] = c["card"]
    g["FG"] = c["text"]
    g["SUB"] = c["text2"]
    g["LINE"] = c["separator"]
    g["ACCENT"] = c["accent"]
    g["GOOD"] = c["ok"]
    g["BAD"] = c["live"]
    g["WARN"] = "#e0a030" if dark else "#b25000"  # 警告のくすんだ琥珀色
    g["ACCENT_FG"] = "#1c1c1c" if _lum(c["accent"]) > 0.6 else "#ffffff"
    g["ACCENT_HOVER"] = _blend(c["accent"], c["text"], 0.12)
    g["PANEL_HOVER"] = _blend(c["card"], c["text"], 0.08)
    g["DISABLED_FG"] = _blend(c["bg"], c["text"], 0.35)
    try:  # 状態アイコンの色表も更新する (定義より前に呼ばれたときは何もしない)
        g["ICONS"][STATE_OK] = ("✓", GOOD, g["ICONS"][STATE_OK][2])
        g["ICONS"][STATE_MISSING] = ("●", SUB, g["ICONS"][STATE_MISSING][2])
        g["ICONS"][STATE_WARN] = ("!", WARN, g["ICONS"][STATE_WARN][2])
        g["DIAG_ICONS"][DIAG_OK] = ("✓", GOOD)
        g["DIAG_ICONS"][DIAG_NG] = ("✗", BAD)
        g["DIAG_ICONS"][DIAG_WARN] = ("!", WARN)
    except Exception:
        pass
    return old


_apply_palette()

FONT = "Yu Gothic UI"

ICONS = {STATE_OK: ("✓", GOOD, t("setup.wizard.icons_1")), STATE_MISSING: ("●", SUB, t("setup.wizard.icons_2")), STATE_WARN: ("!", WARN, t("setup.wizard.icons_3"))}

# 画面の拡大率 (DPI / 96)。フォントは tk の scaling でそろうが、ピクセル指定の余白・幅は px() で拡大する
SCALE = 1.0


def set_scale(f):
    global SCALE
    try:
        SCALE = max(1.0, min(3.0, float(f)))
    except Exception:
        SCALE = 1.0


def px(n):
    return max(1, int(round(n * SCALE)))


def detect_scale(root):
    """tk の実際の DPI から拡大率を求める (取れなければ 1.0)。"""
    try:
        return float(root.winfo_fpixels("1i")) / 96.0
    except Exception:
        return 1.0


_TOPS = []
_COLOR_OPTS = ("bg", "fg", "background", "foreground", "activebackground", "activeforeground", "disabledforeground",
               "readonlybackground", "highlightbackground", "highlightcolor", "selectbackground", "selectforeground",
               "insertbackground")


def _recolor(widget, cmap):
    """widget 以下のウィジェット (tk 版) の色を cmap (旧→新) で置き換える。"""
    if getattr(widget, "_pc_keep", False):
        return
    for opt in _COLOR_OPTS:
        try:
            cur = str(widget.cget(opt)).lower()
        except Exception:
            continue
        if cur in cmap:
            try:
                widget.configure(**{opt: cmap[cur]})
            except Exception:
                pass
    try:
        if widget.winfo_class() == "Canvas":
            for item in widget.find_all():
                for opt in ("fill", "outline"):
                    try:
                        cur = str(widget.itemcget(item, opt)).lower()
                    except Exception:
                        continue
                    if cur in cmap:
                        widget.itemconfigure(item, **{opt: cmap[cur]})
    except Exception:
        pass
    try:
        kids = widget.winfo_children()
    except Exception:
        kids = []
    for k in kids:
        _recolor(k, cmap)


def _on_theme_change():
    old = _apply_palette()
    _sync_check_style()
    cmap = {}
    for n in _COLOR_NAMES:
        if old.get(n) and old[n] != globals()[n]:
            cmap[old[n].lower()] = globals()[n]
    if not cmap:
        return
    for top in list(_TOPS):
        try:
            if not top.winfo_exists():
                _TOPS.remove(top)
                continue
            _recolor(top, cmap)
        except Exception:
            try:
                _TOPS.remove(top)
            except ValueError:
                pass


def _track_top(top):
    """テーマ (ライト/ダーク) が切り替わったとき top の色を付け替える。"""
    _TOPS.append(top)
    try:
        from phonecam.ui import theme as _theme
        _theme.on_change(_on_theme_change)
    except Exception:
        pass


def make_button(parent, text, command, primary=False, state="normal", big=False):
    """角のない平らなボタン。big は押す場所をはっきり見せたいときの大きいボタン。"""
    if primary:
        b = tk.Button(parent, text=text, command=command, bg=ACCENT, fg=ACCENT_FG, activebackground=ACCENT_HOVER,
                      activeforeground=ACCENT_FG, relief="flat", bd=0, padx=px(40 if big else 22), pady=px(12 if big else 7),
                      cursor="hand2", font=(FONT, 12 if big else 10, "bold"), disabledforeground=DISABLED_FG, state=state)
    else:
        b = tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG, activebackground=PANEL_HOVER,
                      activeforeground=FG, relief="flat", bd=0, padx=px(16), pady=px(6), cursor="hand2",
                      font=(FONT, 10), disabledforeground=DISABLED_FG, state=state)
    return b


def make_link(parent, text, command, bg=None):
    if bg is None:
        bg = BG
    b = tk.Button(parent, text=text, command=command, bg=bg, fg=SUB, activebackground=bg, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2", font=(FONT, 9), padx=px(4), pady=px(2))
    return b


def bind_wrap(label, container, reserve=0, minimum=140):
    """label の wraplength を container の実際の幅に合わせる (固定ピクセルにしない)。
    reserve はほかの列が使う幅 (数値、または呼ぶと数値を返す関数)。"""
    def fit(_e=None):
        try:
            w = container.winfo_width()
            if w > 1:
                r = reserve() if callable(reserve) else reserve
                label.configure(wraplength=max(px(minimum), int(w - r)))
        except Exception:
            pass
    container.bind("<Configure>", fit, add="+")
    return fit


def wrapped_label(parent, text, container=None, reserve=0, **kw):
    """折り返すラベル。初期の wraplength を持たせ、container の幅に追従させる。"""
    kw.setdefault("bg", BG)
    kw.setdefault("fg", SUB)
    kw.setdefault("font", (FONT, 10))
    kw.setdefault("justify", "left")
    kw.setdefault("anchor", "w")
    lbl = tk.Label(parent, text=text, wraplength=px(440), **kw)
    if container is not None:
        bind_wrap(lbl, container, reserve)
    return lbl


class ThinProgress:
    """細い直線のプログレスバー。"""

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, height=px(4), bg=PANEL, highlightthickness=0, bd=0)
        self.value = 0.0
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def set(self, v):
        self.value = max(0.0, min(1.0, v))
        self._draw()

    def _draw(self):
        try:
            w = self.canvas.winfo_width()
            self.canvas.delete("all")
            self.canvas.create_rectangle(0, 0, int(w * self.value), px(4), fill=ACCENT, width=0)
        except Exception:
            pass


class ScrollFrame:
    """縦にスクロールできる領域 (Canvas + 内側の Frame)。中身が収まるときはスクロールバーを隠す。"""

    def __init__(self, parent, bg=None):
        if bg is None:
            bg = BG
        self.outer = tk.Frame(parent, bg=bg)
        self.canvas = tk.Canvas(self.outer, bg=bg, highlightthickness=0, bd=0, yscrollincrement=px(24))
        self.vsb = tk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar_visible = False
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _e=None):
        try:
            self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        except Exception:
            pass

    def _on_canvas(self, e):
        try:
            self.canvas.itemconfigure(self._win, width=e.width)
        except Exception:
            pass

    def _on_yscroll(self, lo, hi):
        try:
            self.vsb.set(lo, hi)
            need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
            if need and not self.bar_visible:
                self.vsb.pack(side="right", fill="y")
                self.bar_visible = True
            elif not need and self.bar_visible:
                self.vsb.pack_forget()
                self.bar_visible = False
        except Exception:
            pass

    def wheel(self, e):
        try:
            if self.bar_visible:
                self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        except Exception:
            pass

    def to_top(self):
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass


class SetupPanel:
    """コンポーネント一覧。mode = "wizard" (チェックボックス) / "tab" (行ごとのインストールボタン) / "result" (結果表示)。"""

    def __init__(self, parent, controller, mode="wizard", on_install_one=None, results=None):
        self.controller = controller
        self.mode = mode
        self.on_install_one = on_install_one
        self.results = results or {}
        self.frame = tk.Frame(parent, bg=BG)
        self.vars = {}
        self.install_buttons = {}

    def pack(self, **kw):
        self.frame.pack(**kw)

    def render(self):
        for w in list(self.frame.winfo_children()):
            w.destroy()
        self.vars = {}
        self.install_buttons = {}
        c = self.controller
        if not c.checked:
            tk.Label(self.frame, text=t("setup.wizard.setuppanel_render_1"), bg=BG, fg=SUB, font=(FONT, 10)).pack(anchor="w", pady=px(20))
            return
        for comp in COMPONENTS:
            self._row(comp)

    def _row(self, comp):
        c = self.controller
        cid = comp["id"]
        st = c.statuses.get(cid, {"state": STATE_WARN, "detail": ""})
        state = st.get("state", STATE_WARN)
        res = self.results.get(cid)
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x")
        inner = tk.Frame(row, bg=BG)
        inner.pack(fill="x", padx=px(2), pady=px(9))
        # 列: 0 = 状態アイコン (固定) / 1 = 文章 (伸びる) / 2 = チェックやボタン (固定)
        inner.columnconfigure(0, minsize=px(34))
        inner.columnconfigure(1, weight=1)

        if res is not None:
            icon, color, word = ("✓", GOOD, t("setup.wizard.setupengine__run_elevated_7")) if res.get("ok") else ("✗", BAD, t("setup.wizard.setuppanel__row_1"))
        else:
            icon, color, word = ICONS.get(state, ICONS[STATE_WARN])
        icon_lbl = tk.Label(inner, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold"))
        icon_lbl.grid(row=0, column=0, sticky="nw")
        action = tk.Frame(inner, bg=BG)
        action.grid(row=0, column=2, sticky="ne", padx=(px(10), 0))
        text_col = tk.Frame(inner, bg=BG)
        text_col.grid(row=0, column=1, sticky="ew")
        head = tk.Frame(text_col, bg=BG)
        head.pack(anchor="w")
        tk.Label(head, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(head, text="  " + word, fg=color, bg=BG, font=(FONT, 9)).pack(side="left")
        detail = (res or {}).get("detail") if res is not None else st.get("detail", "")

        def reserve():
            return px(34) + action.winfo_reqwidth() + px(24)

        wrapped_label(text_col, comp["purpose"], inner, reserve, font=(FONT, 9)).pack(fill="x")
        if detail:
            wrapped_label(text_col, detail, inner, reserve, font=(FONT, 9),
                          fg=(BAD if res is not None else WARN)).pack(fill="x")
        if cid in GUIDED_IDS and state != STATE_OK and res is None and self.mode in ("wizard", "tab"):
            wrapped_label(text_col, t("setup.wizard.setuppanel__row_2"), inner, reserve, font=(FONT, 9),
                          fg=ACCENT).pack(fill="x")
        if self.mode == "wizard" and res is None:
            self._wizard_action(action, comp, state)
        elif self.mode == "tab" and res is None:
            self._tab_action(action, comp, state)
        tk.Frame(self.frame, bg=LINE, height=1).pack(fill="x")

    def _wizard_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK:
            tk.Label(action, text=t("setup.wizard.setuppanel__wizard_action_1"), fg=SUB, bg=BG, font=(FONT, 9)).pack()
            return
        if not is_selectable(cid, c.statuses):
            return
        var = tk.BooleanVar(value=bool(c.selection.get(cid)))
        self.vars[cid] = var
        locked = comp["required"] and state == STATE_MISSING
        if locked:
            var.set(True)
        cb = _checkbutton(action, variable=var,
                             command=lambda cid=cid, v=var: c.set_selected(cid, v.get()))
        if locked:
            cb.config(state="disabled")
        cb.pack()

    def _tab_action(self, action, comp, state):
        c = self.controller
        cid = comp["id"]
        if state == STATE_OK or not is_selectable(cid, c.statuses):
            return
        label = t("setup.wizard.setuppanel__tab_action_1") if cid == "camera" and c.statuses.get(cid, {}).get("detail") else t("setup.wizard.setuppanel__tab_action_2")
        b = make_button(action, label, lambda cid=cid: self.on_install_one and self.on_install_one(cid),
                        state=("disabled" if c.busy else "normal"))
        b.pack()
        self.install_buttons[cid] = b


def text_section(parent, title, lines, container, numbered=False, intro=""):
    """見出し + 箇条書き (または番号つき) の文章ブロック。title が空なら見出しなし。
    番号 (記号) と本文は横並びにし、折り返すのは本文だけ。"""
    if title:
        tk.Label(parent, text=title, bg=BG, fg=FG, font=(FONT, 11, "bold"), anchor="w").pack(anchor="w", pady=(px(14), 0))
    if intro:
        wrapped_label(parent, intro, container, font=(FONT, 9)).pack(fill="x", pady=(px(2), 0))
    for n, line in enumerate(lines, 1):
        mark = ("%d." % n) if numbered else "•"
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=(px(3), 0))
        mk = tk.Label(row, text=mark, bg=BG, fg=FG, font=(FONT, 9), anchor="ne", justify="right", width=3 if numbered else 2)
        mk.pack(side="left", anchor="n")
        lbl = wrapped_label(row, line, row, reserve=lambda m=mk: m.winfo_reqwidth() + px(8), fg=FG, font=(FONT, 9))
        lbl.pack(side="left", fill="x", expand=True)


def collapsible(parent, title, build, on_toggle=None):
    """「▸ 見出し」をクリックで開閉する折りたたみ。build(frame) は最初に開いたときだけ呼ぶ。"""
    box = tk.Frame(parent, bg=BG)
    box.pack(fill="x", pady=(px(8), 0))
    inner = tk.Frame(box, bg=BG)
    st = {"open": False, "built": False}
    btn = tk.Button(box, text="▸ " + title, bg=BG, fg=SUB, activebackground=BG, activeforeground=FG, relief="flat", bd=0,
                    cursor="hand2", font=(FONT, 10), padx=px(2), pady=px(4), anchor="w")

    def toggle():
        st["open"] = not st["open"]
        if st["open"]:
            if not st["built"]:
                st["built"] = True
                build(inner)
            inner.pack(fill="x", padx=(px(14), 0))
        else:
            inner.pack_forget()
        btn.config(text=("▾ " if st["open"] else "▸ ") + title)
        if on_toggle:
            try:
                on_toggle()
            except Exception:
                pass

    btn.config(command=toggle)
    btn.pack(anchor="w")
    return box


def work_area(widget):
    """(left, top, right, bottom): タスクバーを除いた作業領域。Windows 以外・失敗時は画面全体。"""
    try:
        import ctypes
        from ctypes import wintypes
        rc = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):  # SPI_GETWORKAREA
            if rc.right > rc.left and rc.bottom > rc.top:
                return rc.left, rc.top, rc.right, rc.bottom
    except Exception:
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


DIAG_ICONS = {DIAG_OK: ("✓", GOOD), DIAG_NG: ("✗", BAD), DIAG_WARN: ("!", WARN)}


class DiagnosticsDialog:
    """「AltServerの診断」。チェックごとに ✓/✗ と、直し方を1行で出す。診断は別スレッドで実行する。"""

    def __init__(self, root, runner=None, open_url=None):
        self.runner = runner or run_diagnostics
        self.open_url = open_url or webbrowser.open
        self.root = root
        self.results = None
        self.running = False
        self.progress_text = ""
        self._box = []
        self._job = None
        self.closed = False
        set_scale(detect_scale(root))
        _apply_palette()
        self.top = tk.Toplevel(root)
        _track_top(self.top)
        self.top.title(t("setup.wizard.diagnosticsdialog___init___1"))
        self.top.configure(bg=BG)
        try:
            self.top.minsize(px(520), px(420))
            self.top.protocol("WM_DELETE_WINDOW", self.close)
        except Exception:
            pass
        foot = tk.Frame(self.top, bg=BG)
        foot.pack(side="bottom", fill="x", padx=px(24), pady=px(12))
        make_button(foot, t("setup.wizard.diagnosticsdialog___init___2"), self.close).pack(side="right")
        self.run_btn = make_button(foot, t("setup.wizard.diagnosticsdialog___init___3"), self.run, primary=True)
        self.run_btn.pack(side="right", padx=(0, px(10)))
        self.scroll = ScrollFrame(self.top)
        self.scroll.outer.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(24), pady=px(18))
        try:
            self.top.bind("<MouseWheel>", self.scroll.wheel)
        except Exception:
            pass
        self.render()
        self.run()

    def run(self):
        if self.running or self.closed:
            return
        self.running = True
        self.results = None
        self.progress_text = t("setup.wizard.diagnosticsdialog_run_1")
        self._box = []
        try:
            self.run_btn.config(state="disabled")
        except Exception:
            pass
        self.render()

        def prog(msg):
            if msg:
                self.progress_text = msg

        def work():
            try:
                self._box.append(self.runner(progress=prog))
            except Exception as e:
                self._box.append([_diag("error", t("setup.wizard.run_work_1"), DIAG_WARN, t("setup.wizard.run_work_2", detail=e), t("setup.wizard.run_work_3"))])

        threading.Thread(target=work, name="altserver-diag", daemon=True).start()
        self._schedule()

    def _schedule(self):
        try:
            self._job = self.top.after(300, self._poll)
        except Exception:
            self._job = None

    def _poll(self):
        self._job = None
        if self.closed:
            return
        if self._box:
            self.results = self._box[0]
            self.running = False
            try:
                self.run_btn.config(state="normal")
            except Exception:
                pass
            self.render()
            return
        if self.status_label is not None:
            try:
                self.status_label.config(text=self.progress_text)
            except Exception:
                pass
        self._schedule()

    def render(self):
        for w in list(self.body.winfo_children()):
            w.destroy()
        self.status_label = None
        tk.Label(self.body, text=t("setup.wizard.diagnosticsdialog___init___1"), bg=BG, fg=FG, font=(FONT, 18, "bold"), anchor="w").pack(anchor="w")
        if self.running or self.results is None:
            self.status_label = wrapped_label(self.body, self.progress_text or t("setup.wizard.diagnosticsdialog_run_1"), self.body, fg=SUB)
            self.status_label.pack(fill="x", pady=(px(8), 0))
        else:
            wrapped_label(self.body, diagnostics_summary(self.results), self.body, fg=FG, font=(FONT, 10)).pack(
                fill="x", pady=(px(6), px(8)))
            for r in self.results:
                icon, color = DIAG_ICONS.get(r["state"], DIAG_ICONS[DIAG_WARN])
                row = tk.Frame(self.body, bg=BG)
                row.pack(fill="x", pady=px(5))
                row.columnconfigure(1, weight=1)
                tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 12, "bold")).grid(row=0, column=0, sticky="nw",
                                                                                      padx=(0, px(10)))
                col = tk.Frame(row, bg=BG)
                col.grid(row=0, column=1, sticky="ew")
                tk.Label(col, text=r["label"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
                if r.get("detail"):
                    wrapped_label(col, r["detail"], row, px(40), fg=color, font=(FONT, 9)).pack(fill="x")
                if r.get("fix") and r["state"] != DIAG_OK:
                    wrapped_label(col, "→ " + r["fix"], row, px(40), fg=FG, font=(FONT, 9)).pack(fill="x")
        if iphone is not None:
            text_section(self.body, iphone.ALT_2003_TITLE, iphone.ALT_2003_CHECKLIST, self.body, numbered=True)
            text_section(self.body, t("setup.wizard.diagnosticsdialog_render_1"), iphone.ALTSTORE_REQUIREMENTS, self.body, numbered=True,
                         intro=t("setup.wizard.diagnosticsdialog_render_2"))
            wrapped_label(self.body, iphone.ALTSTORE_NEW_PC_NOTE, self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
        make_link(self.body, t("setup.wizard.diagnosticsdialog_render_3"), lambda: self.open_url(FAQ_URL)).pack(anchor="w", pady=(px(10), 0))
        try:
            self.top.update_idletasks()
            sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
            w = min(max(self.top.winfo_width(), px(620)), int(sw * 0.9))
            h = min(max(self.top.winfo_height(), px(560)), int(sh * 0.9))
            self.top.geometry("%dx%d" % (w, h))
        except Exception:
            pass

    def close(self):
        self.closed = True
        if self._job is not None:
            try:
                self.top.after_cancel(self._job)
            except Exception:
                pass
        try:
            self.top.destroy()
        except Exception:
            pass


def open_diagnostics(root, open_url=None, runner=None):
    """「AltServerの診断」ウィンドウを開く。開けなかったら None。"""
    try:
        return DiagnosticsDialog(root, runner=runner, open_url=open_url)
    except Exception:
        return None




class SetupWizard:
    """初回セットアップ / やり直し用のウィザード (リサイズ可能な Toplevel)。
    start="iphone" のときは iPhone の手順だけを開く (セットアップタブの「iPhoneのセットアップ」)。"""

    TITLES = [t("setup.wizard.setupwizard_1"), t("setup.wizard.setupwizard_2"), t("setup.wizard.setuppanel__tab_action_2"), t("setup.wizard.setupwizard_3")]

    def __init__(self, root, controller, on_finish, on_skip=None, open_url=None, launch=None, start="setup", update_version=None):
        self.root = root
        self.update_version = update_version
        self.controller = controller
        self.on_finish = on_finish
        self.on_skip = on_skip or on_finish
        self.open_url = open_url or webbrowser.open
        self.launch = launch or _startfile
        self.mode = start
        self.page = 0
        self.finished = False
        self._placed = False
        set_scale(detect_scale(root))
        _apply_palette()
        self.top = tk.Toplevel(root)
        _track_top(self.top)
        self.top.title(t("setup.wizard.setupwizard___init___1"))
        self.top.configure(bg=BG)
        try:
            wl, wt, wr, wb = work_area(self.top)
            self.top.minsize(min(px(520), wr - wl - px(40)), min(px(440), wb - wt - px(80)))
            self.top.resizable(True, True)
            self.top.transient(root)
            self.top.protocol("WM_DELETE_WINDOW", self._close)
        except Exception:
            pass
        # フッター (ボタン) は一番下に固定。本文だけがスクロールする
        self.footer_wrap = tk.Frame(self.top, bg=BG)
        self.footer_wrap.pack(side="bottom", fill="x")
        tk.Frame(self.footer_wrap, bg=LINE, height=1).pack(fill="x")
        self.footer = tk.Frame(self.footer_wrap, bg=BG)
        self.footer.pack(fill="x", padx=px(28), pady=px(14))
        self.scroll = ScrollFrame(self.top)
        self.scroll.outer.pack(side="top", fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(28), pady=(px(24), px(10)))
        try:
            self.top.bind("<MouseWheel>", self.scroll.wheel)
        except Exception:
            pass
        self.panel = None
        self.log_visible = False
        self.log_text = None
        self._listener = self._on_controller
        controller.listen(self._listener)
        # iPhone ページの状態
        self.snap = {}
        self.app_ok = False
        self._visited = set()
        self._first = False
        self._probing = False
        self._alt_probe = False
        self._probe_result = None
        self._next_probe = 0.0
        self._pump_job = None
        self._dl_state = None
        self.alt_running = False
        self._confirmed = set()
        self._hop_job = None
        self._just_auto = False
        self.chip_icon = None
        self.confirm_cb = None
        self.alt_btn = None
        self.show(PAGE_IPHONE if start == "iphone" else (PAGE_INSTALL if start == "guided" else PAGE_WELCOME))
        try:
            self.top.grab_set()
        except Exception:
            pass

    # -- 共通 -------------------------------------------------------------
    def _clear(self):
        for w in list(self.body.winfo_children()):
            w.destroy()
        for w in list(self.footer.winfo_children()):
            w.destroy()
        self.panel = None
        self.log_text = None
        self.stage_label = None
        self.bar = None
        self.next_btn = None
        self.retry_btn = None
        self.result_holder = None
        self.note_label = None
        self.audio_label = None
        self.chip = None
        self.chip_icon = None
        self.confirm_cb = None
        self.alt_btn = None
        if self._hop_job is not None:
            try:
                self.top.after_cancel(self._hop_job)
            except Exception:
                pass
            self._hop_job = None
        self.dl_label = None
        self.dl_btn = None
        self.guided_holder = None
        self.manual_holder = None
        self.abort_btn = None
        self.g_elapsed = None
        self.g_hint = None
        self._g_job = None

    def _title(self, text, sub=None):
        tk.Label(self.body, text=text, bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w").pack(anchor="w")
        if sub:
            wrapped_label(self.body, sub, self.body).pack(fill="x", pady=(px(6), px(14)))

    def show(self, index):
        self.page = index
        self._clear()
        if index >= PAGE_IPHONE:
            self._page_step(index - PAGE_IPHONE)
        else:
            [self._page_welcome, self._page_check, self._page_install][index]()
        self.scroll.to_top()
        self._fit_window()
        if index >= PAGE_IPHONE:
            self._start_pump()

    def _fit_window(self):
        """中身の大きさに合わせてウィンドウを大きくする。作業領域 (タスクバーを除く画面) からははみ出させない。
        中身が収まらないときは本文のスクロール領域で吸収し、下のボタン行は常に見える。"""
        try:
            self.top.update_idletasks()
            wl, wt, wr, wb = work_area(self.top)
            aw, ah = wr - wl, wb - wt
            margin = px(48)  # タイトルバーと枠の分
            pad_w = 2 * px(28) + px(24)
            need_w = max(self.body.winfo_reqwidth() + pad_w, self.footer_wrap.winfo_reqwidth(), px(560))
            need_h = (self.body.winfo_reqheight() + px(24) + px(10) + self.footer_wrap.winfo_reqheight())
            cur_w, cur_h = self.top.winfo_width(), self.top.winfo_height()
            if not self._placed:
                cur_w = cur_h = 1
            max_w = max(px(300), min(int(aw * 0.92), aw - px(24)))
            max_h = max(px(300), ah - margin - px(8))
            w = min(max(cur_w, need_w), max_w)
            h = min(max(cur_h, need_h), max_h)
            if not self._placed:
                x = wl + (aw - w) // 2
                y = wt + max(0, (ah - h - margin) // 2)
                self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
                self._placed = True
            elif (w, h) != (cur_w, cur_h):
                x = max(wl, min(self.top.winfo_x(), wr - w))
                y = max(wt, min(self.top.winfo_y(), wb - h - margin))
                self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            pass

    # -- ページ1 ----------------------------------------------------------
    def _page_welcome(self):
        tk.Label(self.body, text=t("setup.wizard.setupwizard__page_welcome_1"), bg=BG, fg=FG, font=(FONT, 22, "bold"), anchor="w").pack(
            anchor="w", pady=(px(36), 0))
        if self.update_version:
            tk.Label(self.body, text=t("setup.after_update", version=self.update_version), bg=BG, fg=SUB,
                     font=(FONT, 10), anchor="w", justify="left", wraplength=px(480)).pack(anchor="w", pady=(px(8), 0))
        wrapped_label(self.body, t("setup.wizard.setupwizard__page_welcome_2"),
                      self.body, font=(FONT, 11)).pack(fill="x", pady=(px(16), 0))
        make_link(self.footer, t("setup.wizard.setuppanel__wizard_action_1"), self._skip).pack(side="left")
        make_button(self.footer, t("setup.wizard.setupwizard__page_welcome_3"), lambda: self.show(PAGE_CHECK), primary=True).pack(side="right")

    # -- ページ2 ----------------------------------------------------------
    def _page_check(self):
        self._title(t("setup.wizard.setupwizard_2"))
        self.panel = SetupPanel(self.body, self.controller, "wizard")
        self.panel.pack(fill="x")
        self.mic_var = tk.BooleanVar(value=self.controller.mic)
        # VB-CABLE が導入済みなら選ぶ必要がないので表示しない (_sync_mic_option で出し入れする)
        self.mic_box = tk.Frame(self.body, bg=BG)
        _checkbutton(self.mic_box, text=t("setup.wizard.setupwizard__page_check_1"), variable=self.mic_var,
                     command=self._mic_changed).pack(anchor="w")
        tk.Label(self.mic_box, text=t("setup.wizard.setupwizard__page_check_2"),
                 bg=BG, fg=SUB, font=(FONT, 9), justify="left", anchor="w",
                 wraplength=px(480)).pack(anchor="w", fill="x", padx=(px(22), 0))
        self._sync_mic_option()
        make_link(self.footer, t("setup.wizard.setuppanel__wizard_action_1"), self._skip).pack(side="left")
        self.next_btn = make_button(self.footer, t("setup.wizard.setuppanel__tab_action_2"), self._start_install, primary=True)
        self.next_btn.pack(side="right")
        make_button(self.footer, t("setup.wizard.setupwizard__page_check_3"), self.controller.recheck).pack(side="right", padx=(0, px(10)))
        self.panel.render()
        self._update_next_label()
        if not self.controller.checked:
            self.controller.recheck()

    def _sync_mic_option(self):
        box = getattr(self, "mic_box", None)
        if box is None:
            return
        try:
            installed = self.controller.statuses.get("vbcable", {}).get("state") == STATE_OK
            if installed:
                box.pack_forget()
            elif not box.winfo_ismapped():
                box.pack(anchor="w", fill="x", pady=(px(12), 0))
        except Exception:
            pass

    def _mic_changed(self):
        self.controller.set_mic(self.mic_var.get())
        self.panel.render()
        self._update_next_label()
        self._fit_window()

    def _update_next_label(self):
        if self.next_btn is None:
            return
        ids = self.controller.selected_ids()
        try:
            self.next_btn.config(text=t("setup.wizard.setuppanel__tab_action_2") if ids else t("setup.wizard.setupwizard__page_welcome_3"))
        except Exception:
            pass

    def _start_install(self):
        ids = self.controller.selected_ids()
        if not ids:
            self.show(PAGE_IPHONE)
            return
        if self.controller.start(ids):
            self.show(PAGE_INSTALL)

    # -- ページ3 ----------------------------------------------------------
    def _page_install(self):
        self._title(t("setup.wizard.setuppanel__tab_action_2"))
        self.stage_label = wrapped_label(self.body, self.controller.stage, self.body, fg=FG)
        self.stage_label.pack(fill="x")
        self.bar = ThinProgress(self.body)
        self.bar.pack(fill="x", pady=(px(8), px(6)))
        self.bar.set(self.controller.progress)
        self.detail_btn = make_link(self.body, t("setup.wizard.setupwizard__page_install_1"), self._toggle_log)
        self.detail_btn.pack(anchor="w")
        self.log_frame = tk.Frame(self.body, bg=BG)
        self.log_text = tk.Text(self.log_frame, height=7, bg=PANEL, fg=SUB, relief="flat", bd=0, font=(FONT, 8),
                                wrap="word", highlightthickness=0, padx=px(8), pady=px(6))
        sb = tk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_visible = False
        self._refill_log()
        self.result_holder = tk.Frame(self.body, bg=BG)
        self.manual_holder = tk.Frame(self.body, bg=BG)
        self.note_label = wrapped_label(self.body, "", self.body, fg=WARN)
        self.guided_holder = tk.Frame(self.body, bg=BG)
        self.guided_holder.pack(fill="x", pady=(px(10), 0))
        self.audio_label = wrapped_label(self.body, "", self.body, fg=SUB, font=(FONT, 9))
        self.g_elapsed = self.g_hint = None
        last_label = t("setup.wizard.diagnosticsdialog___init___2") if self.mode == "guided" else t("setup.wizard.setupwizard__page_welcome_3")
        self.next_btn = make_button(self.footer, last_label, self._after_install, primary=True, state="disabled")
        self.next_btn.pack(side="right")
        self.retry_btn = make_button(self.footer, t("setup.wizard.setupwizard__page_install_2"), self._retry)
        self.abort_btn = make_link(self.footer, t("setup.wizard.setupwizard__page_install_3"), self._abort_guided)
        if not self.controller.busy and self.controller.outcome is not None:
            self._show_outcome()
        self._render_guided()
        self._show_audio_note()
        self._update_install_footer()
        self._schedule_guided_timer()

    def _after_install(self):
        if self.mode == "guided":
            self._end(self.on_finish)
        else:
            self.show(PAGE_IPHONE)

    def _guided_active(self):
        g = self.controller.guided
        return g is not None and not g.finished

    def _install_complete(self):
        c = self.controller
        return (not c.busy) and (not self._guided_active()) and c.outcome is not None

    def _update_install_footer(self):
        try:
            if self.next_btn is not None:
                self.next_btn.config(state="normal" if self._install_complete() else "disabled")
            ab = getattr(self, "abort_btn", None)
            if ab is not None:
                if self._guided_active():
                    ab.pack(side="left")
                else:
                    ab.pack_forget()
        except Exception:
            pass

    def _abort_guided(self):
        self.controller.guided_abort()
        self._show_outcome()

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, pady=(px(4), 0))
            self.detail_btn.config(text=t("setup.wizard.setupwizard__toggle_log_1"))
        else:
            self.log_frame.pack_forget()
            self.detail_btn.config(text=t("setup.wizard.setupwizard__page_install_1"))
        self._fit_window()

    def _refill_log(self):
        if self.log_text is None:
            return
        try:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", "\n".join(self.controller.log_lines))
            self.log_text.config(state="disabled")
            self.log_text.see("end")
        except Exception:
            pass

    def _retry(self):
        self.controller.recheck()
        self.show(PAGE_CHECK)

    def _show_audio_note(self):
        """VB-CABLE を入れたあとの、音の出力先に関する案内 (戻した / 戻せなかった)。"""
        msg = getattr(self.controller, "audio_message", "")
        if self.audio_label is None or not msg:
            return
        try:
            self.audio_label.config(text=msg)
            self.audio_label.pack(fill="x", pady=(px(8), 0))
            self._fit_window()
        except Exception:
            pass

    @staticmethod
    def overall_text(out, guided_pending=False):
        res = (out or {}).get("results", {})
        if guided_pending:
            return t("setup.wizard.setupwizard_overall_text_1")
        failed = [r for r in res.values() if not r.get("ok") and not r.get("skipped")]
        skipped = [r for r in res.values() if r.get("skipped")]
        if (out or {}).get("error") or failed:
            return t("setup.wizard.setupwizard_overall_text_2")
        if skipped:
            return t("setup.wizard.setupwizard_overall_text_3")
        return t("setup.wizard.setupwizard_overall_text_4")

    def _show_outcome(self):
        out = self.controller.outcome or {}
        pending = self._guided_active() or bool(getattr(self.controller, "_pending_guided", []))
        if self.stage_label is not None:
            self.stage_label.config(text=self.overall_text(out, pending))
        if self.bar is not None and not pending:
            self.bar.set(1.0)
        self._render_results(out)
        self.result_holder.pack(fill="x", pady=(px(8), 0), before=self.guided_holder)
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        if out.get("reboot"):
            notes.append(t("setup.wizard.setupwizard__show_outcome_1"))
        for m in out.get("manual", []):
            notes.append(t("setup.wizard.setupwizard__show_outcome_2", name=m["name"], text=m["text"]))
        for w in list(self.manual_holder.winfo_children()):
            w.destroy()
        if notes:
            self.note_label.config(text="\n".join(notes))
            self.note_label.pack(fill="x", pady=(px(8), 0), before=self.guided_holder)
        else:
            try:
                self.note_label.pack_forget()
            except Exception:
                pass
        self.manual_holder.pack(fill="x", before=self.guided_holder)
        for m in out.get("manual", []):
            url = m["url"]
            make_link(self.manual_holder, t("setup.wizard.setupwizard__show_outcome_3", name=m["name"]), lambda u=url: self.open_url(u)).pack(anchor="w")
        failed = [cid for cid, r in out.get("results", {}).items() if not r.get("ok") and not r.get("skipped")
                  and cid not in GUIDED_IDS]
        if failed and self.retry_btn is not None and not pending:
            self.retry_btn.pack(side="right", padx=(0, px(10)))
        self._update_install_footer()
        self._fit_window()

    def _render_results(self, out):
        """結果の行 (インストールした項目だけ) を描画する。"""
        for w in list(self.result_holder.winfo_children()):
            w.destroy()
        ids = out.get("ids", [])
        for cid in ids:
            comp = COMPONENT_BY_ID[cid]
            r = out["results"].get(cid, {"ok": False, "detail": ""})
            row = tk.Frame(self.result_holder, bg=BG)
            row.pack(fill="x", pady=px(3))
            row.columnconfigure(1, weight=1)
            if r.get("ok"):
                icon, color = "✓", GOOD
            elif r.get("skipped"):
                icon, color = "○", SUB
            else:
                icon, color = "✗", BAD
            tk.Label(row, text=icon, fg=color, bg=BG, font=(FONT, 11, "bold")).grid(row=0, column=0, sticky="nw",
                                                                                   padx=(0, px(10)))
            col = tk.Frame(row, bg=BG)
            col.grid(row=0, column=1, sticky="ew")
            tk.Label(col, text=comp["name"], fg=FG, bg=BG, font=(FONT, 10, "bold")).pack(anchor="w")
            if r.get("detail"):
                wrapped_label(col, r["detail"], row, px(40), fg=(BAD if not (r.get("ok") or r.get("skipped")) else WARN),
                              font=(FONT, 9)).pack(fill="x")
            tech = r.get("technical") or (t("setup.wizard.run_installer_2", code=r["code"]) if r.get("code") not in (None, 0) and not r.get("ok") else "")
            if tech:
                wrapped_label(col, t("setup.wizard.setupwizard__render_results_1", detail=tech), row, px(40), fg=SUB, font=(FONT, 8)).pack(fill="x")

    # -- 手順つきインストール (iTunes / iCloud / AltServer / VB-CABLE) ----------
    def _render_guided(self):
        holder = getattr(self, "guided_holder", None)
        if holder is None:
            return
        for w in list(holder.winfo_children()):
            w.destroy()
        self.g_elapsed = self.g_hint = None
        flow = self.controller.guided
        if flow is None:
            return
        c = self.controller
        if flow.finished:
            self._update_install_footer()
            return
        cid = flow.current
        g = GUIDE.get(cid, {"steps": [], "note": ""})
        tk.Frame(holder, bg=LINE, height=1).pack(fill="x", pady=(0, px(10)))
        tk.Label(holder, text=t("setup.wizard.setupwizard__render_guided_1", name=flow.name(cid), index=flow.index + 1, total=len(flow.ids)), bg=BG, fg=FG,
                 font=(FONT, 14, "bold"), anchor="w").pack(anchor="w")
        phase = flow.phase
        if phase == GuidedFlow.PREPARING or phase == GuidedFlow.CHECKING:
            text = t("setup.wizard.setupwizard__render_guided_2") if phase == GuidedFlow.PREPARING else t("setup.wizard.setupwizard__render_guided_3")
            wrapped_label(holder, text, self.body, fg=SUB).pack(fill="x", pady=(px(8), 0))
        else:
            if phase == GuidedFlow.FAILED:
                wrapped_label(holder, flow.message, self.body, fg=BAD).pack(fill="x", pady=(px(8), 0))
                if flow.technical:
                    wrapped_label(holder, t("setup.wizard.setupwizard__render_results_1", detail=flow.technical), self.body, fg=SUB, font=(FONT, 8)).pack(fill="x")
            else:
                for n, line in enumerate(g["steps"], 1):
                    wrapped_label(holder, "%d.  %s" % (n, line), self.body, fg=FG, font=(FONT, 10)).pack(fill="x", pady=(px(6), 0))
                if g.get("note"):
                    wrapped_label(holder, g["note"], self.body, fg=SUB, font=(FONT, 9)).pack(fill="x", pady=(px(8), 0))
                if flow.notice:
                    wrapped_label(holder, flow.notice, self.body, fg=WARN, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
                if phase == GuidedFlow.READY and flow.message:
                    wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(8), 0))
                if phase == GuidedFlow.RUNNING:
                    self.g_elapsed = wrapped_label(holder, self._elapsed_text(flow), self.body, fg=ACCENT)
                    self.g_elapsed.pack(fill="x", pady=(px(10), 0))
                    if flow.message:
                        wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(4), 0))
                    if flow.detected:
                        wrapped_label(holder, t("setup.wizard.setupwizard__render_guided_4"), self.body, fg=GOOD).pack(fill="x", pady=(px(4), 0))
                    self.g_hint = wrapped_label(holder, SLOW_HINT, self.body, fg=WARN)
                    if flow.is_slow():
                        self.g_hint.pack(fill="x", pady=(px(4), 0))
                if phase == GuidedFlow.MISSING:
                    wrapped_label(holder, flow.message, self.body, fg=WARN).pack(fill="x", pady=(px(10), 0))
            row = tk.Frame(holder, bg=BG)
            row.pack(anchor="w", pady=(px(14), 0))
            if phase == GuidedFlow.READY:
                make_button(row, t("setup.wizard.setupwizard__render_guided_5"), c.guided_open, primary=True, big=True).pack(side="left")
            elif phase == GuidedFlow.RUNNING:
                make_button(row, t("setup.wizard.setupwizard__render_guided_6"), c.guided_confirm, primary=True, big=True).pack(side="left")
                make_button(row, t("setup.wizard.setupwizard__render_guided_7"), c.guided_open).pack(side="left", padx=(px(10), 0))
            elif phase == GuidedFlow.MISSING:
                make_button(row, t("setup.wizard.setupwizard__render_guided_6"), c.guided_confirm, primary=True, big=True).pack(side="left")
                make_button(row, t("setup.wizard.setupwizard__render_guided_7"), c.guided_open).pack(side="left", padx=(px(10), 0))
            elif phase == GuidedFlow.FAILED:
                make_button(row, t("setup.wizard.setupwizard__page_install_2"), c.guided_retry, primary=True, big=True).pack(side="left")
            url = flow.page_url(cid)
            row2 = tk.Frame(holder, bg=BG)
            row2.pack(anchor="w", pady=(px(8), 0))
            if url:
                make_link(row2, t("setup.wizard.setupwizard__render_guided_8"), lambda u=url: self.open_url(u)).pack(side="left")
            make_link(row2, t("setup.wizard.setupwizard__render_guided_9"), c.guided_skip).pack(side="left", padx=(px(10), 0))
        self._update_install_footer()
        self._fit_window()

    @staticmethod
    def _elapsed_text(flow):
        sec = int(flow.elapsed())
        return t("setup.wizard.setupwizard__elapsed_text_1", minutes=sec // 60, seconds=sec % 60)

    def _schedule_guided_timer(self):
        if getattr(self, "_g_job", None) is None:
            try:
                self._g_job = self.top.after(1000, self._guided_timer)
            except Exception:
                self._g_job = None

    def _guided_timer(self):
        self._g_job = None
        if self.finished or self.page != PAGE_INSTALL:
            return
        flow = self.controller.guided
        if flow is not None and not flow.finished:
            try:
                self.controller.guided_tick()
                if self.g_elapsed is not None and flow.phase == GuidedFlow.RUNNING:
                    self.g_elapsed.config(text=self._elapsed_text(flow))
                    if self.g_hint is not None and flow.is_slow():
                        self.g_hint.pack(fill="x", pady=(px(4), 0))
            except Exception:
                pass
        self._schedule_guided_timer()

    # -- ページ4〜10: iPhone の手順 ----------------------------------------
    def _ctx(self):
        alt = bool(find_altserver())
        if not alt:
            try:
                alt = self.controller.statuses.get("altserver", {}).get("state") == STATE_OK
            except Exception:
                pass
        link = False
        fn = _host.get("link_connected")
        if fn:
            try:
                link = bool(fn())
            except Exception:
                link = False
        return {"altserver": alt, "app": self.app_ok, "link": link, "altserver_running": self.alt_running,
                "confirmed": getattr(self, "_step_id", "") in self._confirmed}

    def _page_step(self, i):
        steps = iphone.IPHONE_STEPS
        step = steps[i]
        sid = step["id"]
        self._step_id = sid
        self._step_index = i
        self._first = sid not in self._visited
        self._visited.add(sid)
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text=str(i + 1), bg=BG, fg=ACCENT, font=(FONT, 40, "bold")).pack(side="left")
        tk.Label(head, text=t("setup.wizard.setupwizard__page_step_1", total=len(steps)), bg=BG, fg=SUB, font=(FONT, 9)).pack(side="right", anchor="s")
        tk.Label(self.body, text=step["title"], bg=BG, fg=FG, font=(FONT, 18, "bold"), anchor="w").pack(anchor="w")
        for line in step["lines"]:
            wrapped_label(self.body, line, self.body, font=(FONT, 10)).pack(fill="x", pady=(px(6), 0))
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(px(16), px(12)))
        self.chip_icon = tk.Label(row, text="○", bg=BG, fg=SUB, font=(FONT, 12, "bold"))
        self.chip_icon.pack(side="left")
        self.chip = tk.Label(row, text=t("setup.wizard.setuppanel_render_1"), bg=BG, fg=SUB, font=(FONT, 10), anchor="w",
                             justify="left")
        self.chip.pack(side="left", padx=(px(8), 0))
        self.confirm_var = tk.BooleanVar(value=sid in self._confirmed)
        self.confirm_cb = None
        if sid in MANUAL_STEPS:
            self.confirm_cb = _checkbutton(row, text=t("setup.wizard.step_confirm"), variable=self.confirm_var,
                                           command=self._confirm_changed)
            self.confirm_cb.pack(side="right")
        extra = getattr(self, "_extra_" + sid, None)
        if extra:
            extra()
        # フッター
        make_link(self.footer, t("setup.wizard.setupwizard__page_step_2"), self._skip_iphone).pack(side="left")
        last = i == len(steps) - 1
        if last:
            self.next_btn = make_button(self.footer, t("setup.wizard.setupengine__run_elevated_7"), self._finish, primary=True, state="disabled")
        else:
            self.next_btn = make_button(self.footer, t("setup.wizard.setupwizard__page_welcome_3"), self._next, primary=True)
        self.next_btn.pack(side="right")
        if not (i == 0 and self.mode == "iphone"):
            make_button(self.footer, t("setup.wizard.setupwizard__page_step_3"), self._back).pack(side="right", padx=(0, px(10)))
        self._render_chip()

    def _next(self):
        self._just_auto = False
        self.show(self.page + 1)

    def _confirm_changed(self):
        sid = getattr(self, "_step_id", "")
        if self.confirm_var.get():
            self._confirmed.add(sid)
        else:
            self._confirmed.discard(sid)
        self._render_chip()

    def _back(self):
        self._just_auto = False
        if self.page == PAGE_IPHONE:
            self.controller.recheck()
            self.show(PAGE_CHECK)
        else:
            self.show(self.page - 1)

    def _btn_row(self, items):
        """ボタンを2つずつ並べる (幅が足りなくても切れない)。items = [(text, command, enabled)]"""
        row = None
        out = []
        for n, (text, cmd, enabled) in enumerate(items):
            if n % 2 == 0:
                row = tk.Frame(self.body, bg=BG)
                row.pack(anchor="w", pady=(0, px(8)))
            b = make_button(row, text, cmd, state="normal" if enabled else "disabled")
            b.pack(side="left", padx=(0, px(10)))
            out.append(b)
        return out

    def _extra_itunes(self):
        path = find_itunes()
        self._btn_row([(t("setup.wizard.setupwizard__extra_itunes_1"), lambda: self.launch(path), bool(path))])
        if not path:
            wrapped_label(self.body, t("setup.wizard.setupwizard__extra_itunes_2"), self.body,
                          font=(FONT, 9)).pack(fill="x")

    def _extra_altstore(self):
        path = find_altserver()
        btns = self._btn_row([(t("setup.wizard.setupwizard__extra_altstore_1"), lambda: self.launch(path), bool(path)),
                              (t("setup.wizard.setupwizard__extra_altstore_2"), lambda: self.open_url(FAQ_URL), True),
                              (t("setup.wizard.diagnosticsdialog___init___1"), lambda: open_diagnostics(self.top, self.open_url), True)])
        self.alt_btn = btns[0]
        self.alt_btn_next = btns[1]
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_altstore_3"),
                      self.body, font=(FONT, 9)).pack(fill="x")
        if iphone is not None:
            def req(f):
                text_section(f, "", iphone.ALTSTORE_REQUIREMENTS, self.body, numbered=True,
                             intro=t("setup.wizard.diagnosticsdialog_render_2"))
                wrapped_label(f, iphone.ALTSTORE_NEW_PC_NOTE, self.body, reserve=px(20), font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))

            def err(f):
                text_section(f, "", iphone.ALT_2003_CHECKLIST, self.body, numbered=True)
            collapsible(self.body, t("setup.wizard.fold_requirements"), req, self._fit_window)
            collapsible(self.body, iphone.ALT_2003_TITLE, err, self._fit_window)

    def _extra_phonecam(self):
        repo = APP_REPO
        url = iphone.source_url(repo)
        deeplink = iphone.source_deeplink(repo)
        if url and phonecam_qr is not None:
            box = tk.Frame(self.body, bg=BG)
            box.pack(anchor="w", pady=(0, px(8)))
            cv = tk.Canvas(box, highlightthickness=0, bd=0, bg="#ffffff")  # QRコードは明るい地が必須なのでテーマに依らず白
            cv._pc_keep = True
            try:
                phonecam_qr.draw_on_canvas(cv, phonecam_qr.make_qr(deeplink, "M"), cell=px(4), quiet=3)
                cv.pack(side="left")
            except Exception:
                cv.destroy()
            side = tk.Frame(box, bg=BG)
            side.pack(side="left", padx=(px(16), 0), anchor="n")
            wrapped_label(side, t("setup.wizard.setupwizard__extra_phonecam_1"), self.body, px(260),
                          font=(FONT, 9)).pack(fill="x")
            wrapped_label(side, t("setup.wizard.setupwizard__extra_phonecam_2"), self.body, px(260),
                          font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
            ent = tk.Entry(self.body, font=(FONT, 9), relief="flat", bg=PANEL, fg=FG, readonlybackground=PANEL,
                           highlightthickness=0, bd=0)
            ent.insert(0, url)
            ent.configure(state="readonly")
            ent.pack(fill="x", ipady=px(6), pady=(0, px(8)))
            self._btn_row([(t("setup.wizard.setupwizard__extra_phonecam_3"), lambda: self._copy(url), True)])
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_phonecam_4"),
                      self.body, font=(FONT, 9)).pack(fill="x")
        wrapped_label(self.body, t("setup.wizard.setupwizard__extra_phonecam_5"),
                      self.body, font=(FONT, 9)).pack(fill="x", pady=(px(6), 0))
        ipa = ipa_url()
        self.dl_btn = make_button(self.body, t("setup.wizard.setupwizard__extra_phonecam_6"), self._download_ipa,
                                  state="normal" if ipa else "disabled")
        self.dl_btn.pack(anchor="w", pady=(px(8), 0))
        self.dl_label = wrapped_label(self.body, "", self.body, font=(FONT, 9))
        self.dl_label.pack(fill="x", pady=(px(4), 0))
        if self._dl_state and self._dl_state[0] == "ok":
            self.dl_label.config(text=t("setup.wizard.setupwizard__extra_phonecam_7", path=self._dl_state[1]))

    def _copy(self, text):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            if self.chip is not None:
                self.chip.config(text=t("setup.wizard.setupwizard__copy_1"), fg=GOOD)
        except Exception:
            pass

    def _download_ipa(self):
        url = ipa_url()
        if not url or (self._dl_state and self._dl_state[0] == "busy"):
            return
        self._dl_state = ("busy", "")
        if self.dl_btn is not None:
            self.dl_btn.config(state="disabled")
        if self.dl_label is not None:
            self.dl_label.config(text=t("setup.wizard.setupwizard__download_ipa_1"), fg=SUB)
        dest = os.path.join(downloads_dir(), "PhoneCam.ipa")

        def work():
            try:
                download_file(url, dest, "zip")
                self._dl_state = ("ok", dest)
            except Exception as e:
                self._dl_state = ("err", str(e) if isinstance(e, DownloadError) else friendly_net_error(e))

        threading.Thread(target=work, name="ipa-download", daemon=True).start()
        self.top.after(300, self._dl_pump)

    def _dl_pump(self):
        if self.finished:
            return
        st = self._dl_state
        if st and st[0] == "busy":
            self.top.after(300, self._dl_pump)
            return
        try:
            if self.dl_btn is not None:
                self.dl_btn.config(state="normal")
            if st and st[0] == "ok":
                if self.dl_label is not None:
                    self.dl_label.config(text=t("setup.wizard.setupwizard__extra_phonecam_7", path=st[1]), fg=GOOD)
                _open_folder_select(st[1])
            elif st and self.dl_label is not None:
                self.dl_label.config(text=st[1], fg=BAD)
        except Exception:
            pass

    # -- 状態の自動更新 (2秒ごと) ------------------------------------------
    def _start_pump(self):
        self._next_probe = 0.0
        if self._pump_job is None:
            self._pump_job = self.top.after(200, self._pump)

    def _pump(self):
        self._pump_job = None
        if self.finished or self.page < PAGE_IPHONE:
            return
        try:
            if self._probe_result is not None:
                snap, app = self._probe_result
                self._probe_result = None
                self.snap, self.app_ok = snap, app
                self.alt_running = bool(self._alt_probe)
                self._next_probe = time.monotonic() + POLL_MS / 1000.0
                if self._apply():
                    return
            if not self._probing and time.monotonic() >= self._next_probe:
                self._probing = True
                want_app = getattr(self, "_step_id", "") == "test"
                want_alt = getattr(self, "_step_id", "") == "altstore"
                linked = bool(self._ctx().get("link"))

                def work():
                    try:
                        snap = iphone.query_usbmux()
                        app = False
                        if want_app and not linked:
                            app = iphone.probe_phonecam(snap)
                        if want_alt:
                            self._alt_probe = iphone.altserver_running()
                        self._probe_result = (snap, app)
                    except Exception:
                        self._probe_result = ({}, False)
                    finally:
                        self._probing = False

                threading.Thread(target=work, name="iphone-probe", daemon=True).start()
        except Exception:
            pass
        self._pump_job = self.top.after(250, self._pump)

    def _status_text(self, sid, state, text, ctx):
        """翻訳できる状態文 (自動検出の追加分)。それ以外は iphone モジュールの文言をそのまま使う。"""
        if sid == "altstore":
            if ctx.get("altserver_running"):
                return t("setup.wizard.alt_running")
            if state == iphone.ISSUE:
                return t("setup.wizard.alt_not_running") if ctx.get("altserver") else t("setup.wizard.alt_missing")
        if sid in MANUAL_STEPS:
            if state == iphone.DONE:
                return t("setup.wizard.confirmed_done")
            if state == iphone.MANUAL:
                return t("setup.wizard.manual_check")
        return text

    def _render_chip(self):
        if self.chip is None:
            return
        ctx = self._ctx()
        state, text = iphone.step_status(self._step_id, self.snap, ctx)
        text = self._status_text(self._step_id, state, text, ctx)
        if state == iphone.DONE:
            icon, color = "✓", GOOD
            if self._step_id != "test":
                text = "%s — %s" % (text, t("setup.wizard.done_hint"))
        elif state in (iphone.ISSUE, iphone.UNKNOWN):
            icon, color = "⚠", WARN
        else:
            icon, color = "○", SUB
        self.chip.config(text=text, fg=FG if state == iphone.DONE else SUB)
        if self.chip_icon is not None:
            self.chip_icon.config(text=icon, fg=color)
        detected = self._step_id == "altstore" and bool(ctx.get("altserver_running"))
        if self.confirm_cb is not None:
            try:
                if detected:
                    self.confirm_cb.pack_forget()
                elif not self.confirm_cb.winfo_manager():
                    self.confirm_cb.pack(side="right")
            except Exception:
                pass
        if self.alt_btn is not None:
            try:
                if ctx.get("altserver_running"):
                    self.alt_btn.pack_forget()
                elif not self.alt_btn.winfo_manager():
                    self.alt_btn.pack(side="left", padx=(0, px(10)), before=self.alt_btn_next)
            except Exception:
                pass
        if self.next_btn is not None:
            if self._step_id == "test":
                self.next_btn.config(state="normal" if state == iphone.DONE else "disabled")
            elif self.page < PAGE_IPHONE + len(iphone.IPHONE_STEPS) - 1:
                self._style_next(state == iphone.DONE)
        return state

    def _style_next(self, ready):
        """検出できて ✓ のときだけ「次へ」を強調する。"""
        try:
            if ready:
                self.next_btn.config(bg=ACCENT, fg=ACCENT_FG, activebackground=ACCENT_HOVER, activeforeground=ACCENT_FG,
                                     font=(FONT, 10, "bold"))
            else:
                self.next_btn.config(bg=PANEL, fg=FG, activebackground=PANEL_HOVER, activeforeground=FG, font=(FONT, 10))
        except Exception:
            pass

    def _apply(self):
        """新しい検出結果を画面へ。自動で次へ進んだら True。
        進むのは、初めて開いた手順が検出済みのときだけ。1回進んだら次の手順では自動では進まない (何段も飛ばさない)。"""
        state = self._render_chip()
        if (self._first and not self._just_auto and self._hop_job is None
                and iphone.should_auto_advance(self._step_id, state, set())):
            page = self.page
            self._hop_job = self.top.after(900, lambda: self._auto_hop(page))
        return False

    def _auto_hop(self, page):
        self._hop_job = None
        if self.finished or self.page != page:
            return
        self.show(page + 1)
        self._just_auto = True

    # -- 終了 -------------------------------------------------------------
    def _finish(self):
        self._end(self.on_finish)

    def _skip(self):
        self._end(self.on_skip)

    def _skip_iphone(self):
        self._end(self.on_finish if self.mode == "setup" else self.on_skip)

    def _close(self):
        if self.controller.busy:
            return
        if self._guided_active():
            self.controller.guided_abort()
        if self.mode == "guided":
            self._end(self.on_skip)
            return
        if self.page >= PAGE_IPHONE:
            self._skip_iphone()
        else:
            self._skip()

    def _end(self, cb):
        if self.finished:
            return
        self.finished = True
        self.controller.unlisten(self._listener)
        if self._pump_job is not None:
            try:
                self.top.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        try:
            cb()
        except Exception:
            pass

    # -- コントローラーからの通知 ------------------------------------------
    def _on_controller(self, what):
        if self.finished:
            return
        if what == "statuses" or what == "checking":
            if self.page == PAGE_CHECK and self.panel is not None:
                self.panel.render()
                self._sync_mic_option()
                self._update_next_label()
                self._fit_window()
        elif what == "stage" and self.stage_label is not None:
            self.stage_label.config(text=self.controller.stage)
        elif what == "progress" and self.bar is not None:
            self.bar.set(self.controller.progress)
        elif what == "log":
            self._refill_log()
        elif what == "audio" and self.page == PAGE_INSTALL:
            self._show_audio_note()
        elif what == "done" and self.page == PAGE_INSTALL:
            self._show_outcome()
        elif what in ("guided", "guided_done") and self.page == PAGE_INSTALL:
            self._render_guided()
            if what == "guided_done" or not self._guided_active():
                self._show_outcome()
            self._update_install_footer()


