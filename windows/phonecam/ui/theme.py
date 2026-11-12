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
SIZES = {"caption": 9, "body": 10, "strong": 10, "large": 11, "subtitle": 14, "title": 20,
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


def _apply_fonts():
    """sv-ttk は名前付きフォントを負のピクセル値で定義し DPI で拡大されない。set_theme のたびに pt へ直す。"""
    try:
        from tkinter import font as tkfont
    except Exception:
        return
    body = SIZES["body"]
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont", "TkIconFont"):
        try:
            tkfont.nametofont(name, root=_root).configure(family=_FONT, size=body)
        except Exception:
            pass
    try:
        tkfont.nametofont("TkCaptionFont", root=_root).configure(family=_FONT, size=SIZES["caption"])
    except Exception:
        pass
    try:
        f = tkfont.nametofont("TkFixedFont", root=_root)
        f.configure(size=body)
    except Exception:
        pass
    for name, (sz, weight) in _SV_FONTS.items():
        try:
            tkfont.nametofont(name, root=_root).configure(family=_FONT, size=SIZES[sz], weight=weight)
        except Exception:
            pass
    try:
        st = ttk.Style(_root)
        st.configure(".", font=font("body"))
    except Exception:
        pass


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
