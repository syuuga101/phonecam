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


def _pick_font():
    global _FONT
    try:
        from tkinter import font as tkfont
        fams = set(tkfont.families())
        for name in ("Segoe UI Variable Text", "Segoe UI"):
            if name in fams:
                _FONT = name
                return
    except Exception:
        pass
    _FONT = "Segoe UI"


def font_family():
    return _FONT


def font(size=9, weight="normal"):
    """(family, size, weight) の Tk フォント指定。"""
    return (_FONT, size, weight)


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
    try:
        from tkinter import font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                     "TkTooltipFont", "SunValleyBodyFont", "SunValleyCaptionFont"):
            try:
                tkfont.nametofont(name, root=_root).configure(family=_FONT)
            except Exception:
                pass
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
