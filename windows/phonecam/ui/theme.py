# -*- coding: utf-8 -*-
"""テーマ: sv-ttk (Sun Valley) を適用し、OS のライト/ダークに追従する。

init(root, mode) / set_mode(mode) / current() / colors() / on_change(cb) / font(size, weight)
sv_ttk が無いときは vista テーマ (ライト固定) にフォールバックする。
"""

import os
import threading

from phonecam.core.deps import tk, ttk

try:
    import sv_ttk
except Exception:  # pragma: no cover
    sv_ttk = None
try:
    import darkdetect
except Exception:  # pragma: no cover
    darkdetect = None

POLL_MS = 3000

_PALETTES = {
    "light": {"bg": "#fafafa", "card": "#ffffff", "text": "#1c1c1c", "text2": "#616161",
              "separator": "#e5e5e5", "accent": "#005fb8", "live": "#c42b1c", "ok": "#0f7b0f", "select": "#ebebeb"},
    "dark": {"bg": "#1c1c1c", "card": "#2b2b2b", "text": "#fafafa", "text2": "#a8a8a8",
             "separator": "#3a3a3a", "accent": "#57c8ff", "live": "#ff6b5e", "ok": "#6ccb5f", "select": "#2d2d2d"},
}

_root = None
_mode = "system"
_current = "light"
_callbacks = []
_FONT = "Segoe UI"
_poll_job = None


# 文字サイズはすべて pt (tk scaling = DPI/72 なのでDPIに比例する)。ここが唯一のスケール表。
SIZES = {"caption": 10, "body": 11, "strong": 11, "large": 12, "subtitle": 15, "title": 22,
         "title_large": 28, "display": 42}
# sv-ttk の名前付きフォント -> (サイズ名, 太さ)
_SV_FONTS = {
    "SunValleyCaptionFont": ("caption", "normal"),
    "SunValleyBodyFont": ("body", "normal"),
    "SunValleyBodyStrongFont": ("strong", "bold"),
    "SunValleyBodyLargeFont": ("large", "normal"),
    "SunValleySubtitleFont": ("subtitle", "bold"),
    "SunValleyTitleFont": ("title", "bold"),
    "SunValleyTitleLargeFont": ("title_large", "bold"),
    "SunValleyDisplayFont": ("display", "bold"),
}
_JA_FONTS = ("Yu Gothic UI", "Meiryo UI", "Segoe UI")
_OTHER_FONTS = ("Segoe UI Variable Text", "Segoe UI")


def _ui_language():
    try:
        from phonecam.i18n import get_language, resolve_language
        return resolve_language(get_language())
    except Exception:
        return "ja"


def _pick_font():
    """UI の言語でフォントを選ぶ。日本語: Yu Gothic UI > Meiryo UI > Segoe UI (Segoe UI Variable は
    日本語グリフが無く文字ごとにフォールバックして字間が崩れるので使わない)。"""
    global _FONT
    cands = _JA_FONTS if str(_ui_language()).lower().startswith("ja") else _OTHER_FONTS
    try:
        from tkinter import font as tkfont
        fams = set(tkfont.families(root=_root))
        for name in cands:
            if name in fams:
                _FONT = name
                return
    except Exception:
        pass
    _FONT = cands[-1]


def font_family():
    return _FONT


def size(name):
    """名前 (caption/body/strong/large/subtitle/title/...) -> pt。"""
    return SIZES.get(name, SIZES["body"])


def font(size=None, weight="normal"):
    """(family, pt, weight) の Tk フォント指定。size は pt の数値か SIZES の名前 (省略で body)。"""
    if size is None:
        size = SIZES["body"]
    elif isinstance(size, str):
        size = SIZES.get(size, SIZES["body"])
    return (_FONT, int(size), weight)


def system_theme():
    """OS のテーマ ("light"/"dark")。取れなければ light。"""
    try:
        if darkdetect is not None:
            v = darkdetect.theme()
            if v and v.lower() == "dark":
                return "dark"
    except Exception:
        pass
    return "light"


def current():
    return _current


def mode():
    return _mode


def colors():
    """現在のテーマの色 (dict: bg, card, text, text2, separator, accent, live, select)。"""
    return dict(_PALETTES[_current])


def on_change(cb):
    """テーマ変更時に cb() を引数なしで呼ぶ。壊れたウィジェットで例外が出た cb は自動で外す。"""
    if cb not in _callbacks:
        _callbacks.append(cb)
    return cb


def off_change(cb):
    try:
        _callbacks.remove(cb)
    except ValueError:
        pass


def _log(msg):
    """フォント適用の失敗などを %APPDATA%/PhoneCam/phonecam_ui.log に残す (ここで例外は出さない)。"""
    try:
        import time
        from phonecam.core.paths import _appdata_dir
        d = _appdata_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "phonecam_ui.log"), "a", encoding="utf-8") as f:
            f.write("%s theme: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# 明示的に font= を設定する ttk スタイル (sv-ttk は各スタイルに font を直書きするので "." だけでは効かない)
_FONT_STYLES = (
    (".", "body", "normal"), ("TLabel", "body", "normal"), ("TButton", "body", "normal"),
    ("Accent.TButton", "body", "normal"), ("Toolbutton", "body", "normal"),
    ("TRadiobutton", "body", "normal"), ("TCheckbutton", "body", "normal"),
    ("Switch.TCheckbutton", "body", "normal"), ("ToggleButton", "body", "normal"),
    ("TEntry", "body", "normal"), ("TCombobox", "body", "normal"), ("TSpinbox", "body", "normal"),
    ("TMenubutton", "body", "normal"), ("TNotebook.Tab", "body", "normal"),
    ("Treeview", "body", "normal"), ("Treeview.Heading", "body", "bold"),
    ("TLabelframe.Label", "strong", "bold"), ("Card.TLabel", "body", "normal"),
    ("TProgressbar", "body", "normal"),
)


def _apply_fonts():
    """文字サイズをすべて pt 指定に直す。sv-ttk は名前付きフォントを負のピクセル値で作り、
    各 ttk スタイルにも font を直書きするため、(1) 名前付きフォント (2) 各スタイル (3) option DB を
    それぞれ独立に (1 つ失敗しても残りを実行して) 設定し直す。set_theme のたびに呼ぶ。"""
    try:
        from tkinter import font as tkfont
    except Exception as e:
        _log("tkinter.font import failed: %r" % (e,))
        return
    body = SIZES["body"]
    named = [(n, "body", "normal") for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                                             "TkTooltipFont", "TkIconFont")]
    named.append(("TkCaptionFont", "caption", "normal"))
    named += [(n, sz, w) for n, (sz, w) in _SV_FONTS.items()]
    for name, sz, weight in named:
        try:
            tkfont.Font(root=_root, name=name, exists=True).configure(family=_FONT, size=SIZES[sz], weight=weight)
        except Exception as e:
            _log("named font %s: %r" % (name, e))
    try:
        tkfont.Font(root=_root, name="TkFixedFont", exists=True).configure(size=body)
    except Exception as e:
        _log("named font TkFixedFont: %r" % (e,))
    try:
        st = ttk.Style(_root)
    except Exception as e:
        _log("ttk.Style failed: %r" % (e,))
        st = None
    if st is not None:
        for style, sz, weight in _FONT_STYLES:
            try:
                st.configure(style, font=font(sz, weight))
            except Exception as e:
                _log("style %s: %r" % (style, e))
        try:
            ls = tkfont.Font(root=_root, font=font("body")).metrics("linespace")
            st.configure("Treeview", rowheight=int(ls + ls * 0.6))
        except Exception as e:
            _log("treeview rowheight: %r" % (e,))
    if _root is not None:
        bf = font("body")
        for pat, f in (("*Font", bf), ("*TEntry.font", bf), ("*Entry.font", bf), ("*TCombobox*Listbox.font", bf),
                       ("*Listbox.font", bf), ("*Menu.font", bf), ("*Text.font", bf), ("*Spinbox.font", bf)):
            try:
                _root.option_add(pat, f)
            except Exception as e:
                _log("option_add %s: %r" % (pat, e))


# ---- DPI 対応のチェック/ラジオ/スイッチのインジケータ (sv-ttk の画像は固定ピクセルで 200% だと小さい) ----
_ind_photos = []   # PhotoImage を GC させない
_ind_serial = [0]
_IND = {
    "light": {"line": "#5e5e5e", "hover": "#efefef", "accent": "#005fb8", "accent_h": "#1a6fc0", "mark": "#ffffff",
              "dis_line": "#bdbdbd", "dis_fill": "#d0d0d0", "knob_on": "#ffffff"},
    "dark": {"line": "#a0a0a0", "hover": "#3a3a3a", "accent": "#57c8ff", "accent_h": "#78d3ff", "mark": "#1c1c1c",
             "dis_line": "#5a5a5a", "dis_fill": "#4a4a4a", "knob_on": "#1c1c1c"},
}


def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def _draw_indicator(kind, w, h, gap, state, pal):
    """kind: check/radio/switch, state: dict(on, hover, disabled)。w x h の RGBA (右に gap px の透明余白)。"""
    from PIL import Image, ImageDraw
    ss = 4
    W, H = (w + gap) * ss, h * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    on, hov, dis = state["on"], state["hover"], state["disabled"]
    lw = max(1, int(round(h / 12.0 * ss)))
    line = _hex(pal["dis_line"] if dis else pal["line"])
    fill_on = _hex(pal["dis_fill"] if dis else (pal["accent_h"] if hov else pal["accent"]))
    mark = _hex(pal["mark"])
    bw, bh = w * ss, h * ss
    if kind == "check":
        r = bh * 0.22
        if on:
            d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=r, fill=fill_on)
            pts = [(bw * 0.25, bh * 0.52), (bw * 0.43, bh * 0.69), (bw * 0.76, bh * 0.31)]
            mw = max(2, int(bh * 0.12))
            d.line(pts, fill=mark, width=mw, joint="curve")
            for x, y in (pts[0], pts[2]):
                d.ellipse([x - mw / 2, y - mw / 2, x + mw / 2, y + mw / 2], fill=mark)
        else:
            d.rounded_rectangle([lw / 2, lw / 2, bw - 1 - lw / 2, bh - 1 - lw / 2], radius=r,
                                fill=_hex(pal["hover"]) if hov and not dis else None, outline=line, width=lw)
    elif kind == "radio":
        if on:
            d.ellipse([0, 0, bw - 1, bh - 1], fill=fill_on)
            c = bw / 2.0
            r = bw * 0.2
            d.ellipse([c - r, c - r, c + r, c + r], fill=mark)
        else:
            d.ellipse([lw / 2, lw / 2, bw - 1 - lw / 2, bh - 1 - lw / 2],
                      fill=_hex(pal["hover"]) if hov and not dis else None, outline=line, width=lw)
    else:  # switch
        rr = bh / 2.0
        if on:
            d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=rr, fill=fill_on)
            kc = bw - rr
            kcol = _hex(pal["dis_line"] if dis else pal["knob_on"]) if dis else _hex(pal["knob_on"])
        else:
            d.rounded_rectangle([lw / 2, lw / 2, bw - 1 - lw / 2, bh - 1 - lw / 2], radius=rr, outline=line, width=lw)
            kc = rr
            kcol = line
        kr = bh * (0.26 if not (hov and not dis) else 0.3)
        d.ellipse([kc - kr, rr - kr, kc + kr, rr + kr], fill=kcol)
    return img.resize((w + gap, h), Image.LANCZOS)


def _replace_indicator(layout, new_name):
    """st.layout() の入れ子リストから ".indicator" の要素名を new_name に差し替えた新しいレイアウト。"""
    out = []
    for name, opts in layout:
        opts = dict(opts)
        if name.endswith(".indicator"):
            name = new_name
        if "children" in opts:
            opts["children"] = _replace_indicator(opts["children"], new_name)
        out.append((name, opts))
    return out


def _apply_indicators():
    if sv_ttk is None or _root is None:
        return
    try:
        from PIL import Image  # noqa: F401
        from phonecam.ui.dpi import px
        from phonecam.ui.icons import _to_photo
        st = ttk.Style(_root)
    except Exception as e:
        _log("indicators unavailable: %r" % (e,))
        return
    pal = _IND[_current]
    specs = (("TCheckbutton", "check", px(18), px(18), px(8)),
             ("TRadiobutton", "radio", px(18), px(18), px(8)),
             ("Switch.TCheckbutton", "switch", px(40), px(20), px(8)))
    for style, kind, w, h, gap in specs:
        try:
            lay = st.layout(style)
            if not lay:
                continue
            _ind_serial[0] += 1
            ename = "PCInd%d.%s.indicator" % (_ind_serial[0], kind)
            combos = [(dict(on=True, hover=False, disabled=True), ("disabled", "selected")),
                      (dict(on=False, hover=False, disabled=True), ("disabled",)),
                      (dict(on=True, hover=True, disabled=False), ("active", "selected")),
                      (dict(on=True, hover=False, disabled=False), ("selected",)),
                      (dict(on=False, hover=True, disabled=False), ("active",))]
            base = _to_photo(_draw_indicator(kind, w, h, gap, dict(on=False, hover=False, disabled=False), pal))
            _ind_photos.append(base)
            maps = []
            for stt, spec in combos:
                ph = _to_photo(_draw_indicator(kind, w, h, gap, stt, pal))
                _ind_photos.append(ph)
                maps.append(tuple(spec) + (str(ph),))
            st.element_create(ename, "image", str(base), *maps, sticky="w")
            new = _replace_indicator(lay, ename)
            if new == lay:
                _log("indicator element not found in layout %s: %r" % (style, lay))
                continue
            st.layout(style, new)
        except Exception as e:
            _log("indicator %s: %r" % (style, e))


def reapply_fonts():
    """UI 構築後などに呼べるフォント再適用 (テーマは変えない)。"""
    try:
        _apply_fonts()
    except Exception as e:
        _log("reapply_fonts: %r" % (e,))


def _apply(resolved):
    global _current
    first = _root is not None
    _current = resolved
    if sv_ttk is not None and _root is not None:
        try:
            try:
                sv_ttk.set_theme(resolved, _root)
            except TypeError:
                sv_ttk.set_theme(resolved)
        except Exception:
            _fallback_vista()
    else:
        _fallback_vista()
    _apply_fonts()
    try:
        _apply_indicators()
    except Exception as e:
        _log("indicators: %r" % (e,))
    try:
        _root.configure(background=_PALETTES[_current]["bg"])
    except Exception:
        pass
    return first


def _fallback_vista():
    global _current
    _current = "light"
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass


def _fire():
    for cb in list(_callbacks):
        try:
            cb()
        except Exception:
            off_change(cb)


def _resolve():
    if _mode in ("light", "dark"):
        return _mode
    return system_theme()


def _poll():
    global _poll_job
    _poll_job = None
    if _root is None:
        return
    try:
        if _mode == "system":
            r = system_theme()
            if r != _current and sv_ttk is not None:
                _apply(r)
                _fire()
        _poll_job = _root.after(POLL_MS, _poll)
    except Exception:
        pass


def init(root, mode_setting="system"):
    """起動時に1回。テーマを適用し、system のときは3秒ごとに OS の変化を見る。"""
    global _root, _mode
    _root = root
    _mode = mode_setting if mode_setting in ("system", "light", "dark") else "system"
    _pick_font()
    _apply(_resolve())
    try:
        root.after_idle(reapply_fonts)
    except Exception:
        pass
    global _poll_job
    if _poll_job is None:
        _poll_job = root.after(POLL_MS, _poll)
    return _current


def set_mode(mode_setting):
    """設定の theme ("system"/"light"/"dark") を切り替えて即反映する。"""
    global _mode
    _mode = mode_setting if mode_setting in ("system", "light", "dark") else "system"
    if _root is None:
        return
    r = _resolve()
    if r != _current or sv_ttk is None:
        _apply(r)
        _fire()


def is_sv():
    return sv_ttk is not None
