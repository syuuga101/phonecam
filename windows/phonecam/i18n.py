# -*- coding: utf-8 -*-
"""多言語化 (文言は windows/locales/<言語>.json)。

t("page.section.item", name=...) は、選択中の言語の文言を返す。
見つからなければ ja、それも無ければキーそのものを返す。
言語は設定 language ("system" なら Windows の表示言語: 日本語なら ja、それ以外は en)。
最初の t() 呼び出し時に設定ファイルから自動で決める (set_language() で上書き可)。
"""

import json
import os
import sys

NO_VALUE = "—"   # 値がないときの表示 (言語に依らない記号。画面の「-」はすべてこれに統一する)

_LOCALES = {}
_LANG = None


def _locales_dir():
    try:
        from phonecam.core.paths import WINDOWS_DIR
        base = getattr(sys, "_MEIPASS", None) or WINDOWS_DIR
    except Exception:
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "locales")


def _load(lang):
    if lang not in _LOCALES:
        data = {}
        try:
            with open(os.path.join(_locales_dir(), lang + ".json"), encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
        _LOCALES[lang] = data
    return _LOCALES[lang]


def system_language():
    """Windows の表示言語: 日本語なら "ja"、それ以外は "en"。"""
    try:
        import ctypes
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lcid & 0x3FF) == 0x11:  # LANG_JAPANESE
            return "ja"
        return "en"
    except Exception:
        pass
    try:
        import locale
        loc = (locale.getdefaultlocale()[0] or "")
        return "ja" if loc.lower().startswith("ja") else "en"
    except Exception:
        return "ja"


def resolve_language(lang):
    if not lang or lang == "system":
        return system_language()
    return str(lang)


def _setting_language():
    try:
        from phonecam.core.settings import SETTINGS_PATH
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("language", "system")
    except Exception:
        return "system"


def set_language(lang):
    """言語を切り替える ("system"/"ja"/"en"…)。"""
    global _LANG
    _LANG = resolve_language(lang)


def get_language():
    if _LANG is None:
        set_language(_setting_language())
    return _LANG


def t(key, /, **kw):
    text = _load(get_language()).get(key)
    if text is None:
        text = _load("ja").get(key)
    if text is None:
        text = key
    if kw:
        try:
            text = text.format(**kw)
        except Exception:
            pass
    return text
