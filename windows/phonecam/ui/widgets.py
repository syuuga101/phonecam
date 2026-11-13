# -*- coding: utf-8 -*-
"""共通部品 (Windows 11 設定アプリ風): page / section / row / hint / icon_label

各ページの build(app, parent) は、まず page(parent) で内容列を作ってから積む。
色は theme.colors()、テーマ切替で再描画される。
"""

from phonecam.core.deps import tk, ttk
from phonecam.ui import icons, theme
from phonecam.ui.dpi import px

_WHEEL_REGISTRY = {}   # str(スクロール対象の中身 or canvas) -> canvas
_wheel_installed = []
_NO_WHEEL_CLASSES = ("Text", "Listbox", "Treeview", "TCombobox")


def _wheel(e):
    try:
        w = e.widget.winfo_containing(e.x_root, e.y_root)
    except Exception:
        w = e.widget
    while w is not None:
        try:
            if w.winfo_class() in _NO_WHEEL_CLASSES:
                return
        except Exception:
            return
        cv = _WHEEL_REGISTRY.get(str(w))
        if cv is not None:
            try:
                if cv.bbox("all") and cv.bbox("all")[3] > cv.winfo_height():
                    cv.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
            except Exception:
                pass
            return
        try:
            w = w.master
        except Exception:
            return


def _install_wheel(widget):
    if _wheel_installed:
        return
    _wheel_installed.append(True)
    try:
        widget.winfo_toplevel().bind_all("<MouseWheel>", _wheel, add="+")
    except Exception:
        pass


def _style_ok(style):
    try:
        return bool(ttk.Style().layout(style))
    except Exception:
        return False


MAX_CONTENT_W = 760   # 内容列の最大幅 (論理px。Windows 11 設定アプリ相当)。実際は px() で DPI 倍する
PAGE_PAD_X = 24


def page(parent):
    """スクロール可能な内容列を parent に作り、積む先の frame を返す。
    内容列は最大 MAX_CONTENT_W (論理px x DPI) で中央寄せ、それより窓が狭ければ窓幅に縮む (左右余白24px)。
    返り値の属性/メソッド:
      .canvas (Canvas), .host (parent)
      .content_width() -> int: 実際の内容幅 (実px、左右余白を除く。まだ配置前は 0)
      <<ContentWidth>>: 内容幅が変わるたびに返り値の frame に飛ぶ仮想イベント (bind して .content_width() を読む)
    """
    canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0, background=theme.colors()["bg"])
    vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    padx = px(PAGE_PAD_X)
    body = ttk.Frame(canvas, padding=(padx, px(8), padx, px(24)))
    win = canvas.create_window((0, 0), window=body, anchor="nw")
    state = {"w": 0}
    body.bind("<Configure>", lambda e: canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), canvas.bbox("all")[3] if canvas.bbox("all") else 0)))

    def on_canvas(e):
        col = min(e.width, px(MAX_CONTENT_W) + 2 * padx)
        canvas.coords(win, max(0, (e.width - col) // 2), 0)
        canvas.itemconfigure(win, width=col, anchor="nw")
        inner = max(0, col - 2 * padx)
        canvas.configure(scrollregion=(0, 0, e.width, canvas.bbox("all")[3] if canvas.bbox("all") else 0))
        if inner != state["w"]:
            state["w"] = inner
            try:
                body.event_generate("<<ContentWidth>>")
            except Exception:
                pass
    canvas.bind("<Configure>", on_canvas)
    canvas.configure(yscrollincrement=px(20))

    def recolor():
        canvas.configure(background=theme.colors()["bg"])
    theme.on_change(recolor)
    _WHEEL_REGISTRY[str(canvas)] = canvas
    _WHEEL_REGISTRY[str(body)] = canvas
    _install_wheel(canvas)
    body.canvas = canvas
    body.host = parent
    body.content_width = lambda: state["w"]
    return body


def section(parent, title):
    """小見出し (Semibold 10pt) + カード枠。カード frame を返す (row() を積む先)。"""
    outer = ttk.Frame(parent)
    outer.pack(fill="x", pady=(px(14), 0))
    if title:
        ttk.Label(outer, text=title, font=theme.font("strong", "bold")).pack(anchor="w", pady=(0, px(6)), padx=px(2))
    style = "Card.TFrame" if _style_ok("Card.TFrame") else "TFrame"
    card = ttk.Frame(outer, style=style, padding=px(12))
    card.pack(fill="x")
    card._row_count = 0
    card.section_frame = outer
    return card


def hint(parent, text, **pack):
    """二次色の1行説明。ttk.Label を返す (pack 済み。grid/pack_forget は呼び出し側で)。"""
    lbl = ttk.Label(parent, text=text, foreground=theme.colors()["text2"], wraplength=px(480), justify="left")
    if "grid" not in pack:
        opts = {"anchor": "w", "pady": (px(2), 0)}
        opts.update({k: v for k, v in pack.items()})
        lbl.pack(**opts)

    def recolor():
        lbl.configure(foreground=theme.colors()["text2"])
    theme.on_change(recolor)

    def fit(event, _l=lbl):
        try:
            _l.configure(wraplength=max(px(160), event.width - px(16)))
        except Exception:
            pass
    try:
        parent.bind("<Configure>", fit, add="+")
    except Exception:
        pass
    return lbl


def icon_label(parent, name, size=16):
    """16px の線アイコンを出す ttk.Label。名前が無ければ空白で幅だけ確保する。"""
    lbl = ttk.Label(parent)

    def apply():
        img = icons.get(name, size)
        if img is not None:
            lbl.configure(image=img, text="", width=0)
            lbl._img = img
        else:
            lbl.configure(image="", text=" ", width=2)
    apply()
    theme.on_change(apply)
    return lbl


def row(card, icon, title, hint=None):
    """「アイコン | ラベル(+補足)」左、返り値の frame に右側の操作を pack(side="right") する。
    2行目以降は上に1pxの区切り線を入れる。"""
    n = getattr(card, "_row_count", 0)
    if n > 0:
        sep = ttk.Separator(card, orient="horizontal")
        sep.pack(fill="x", pady=(px(2), 0))
    card._row_count = n + 1
    fr = ttk.Frame(card)
    fr.pack(fill="x", pady=px(6))
    left = ttk.Frame(fr)
    left.pack(side="left", fill="x", expand=True)
    icon_label(left, icon).pack(side="left", padx=(0, px(12)))
    texts = ttk.Frame(left)
    texts.pack(side="left", fill="x", expand=True)
    title_lbl = ttk.Label(texts, text=title)
    title_lbl.pack(anchor="w")
    if hint:
        h = ttk.Label(texts, text=hint, foreground=theme.colors()["text2"], font=theme.font("caption"))
        h.pack(anchor="w")
        theme.on_change(lambda: h.configure(foreground=theme.colors()["text2"]))
    fr.title_label = title_lbl
    return fr
