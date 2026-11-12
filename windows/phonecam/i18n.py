# -*- coding: utf-8 -*-
"""多言語化 (文言は windows/locales/<言語>.json)。

t("page.section.item", name=...) は、選択中の言語 (既定は ja) の文言を返す。
見つからなければ ja、それも無ければキーそのものを返す。
"""

import json
import os
import sys

_LOCALES = {}
_LANG = "ja"


def _locales_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def set_language(lang):
    global _LANG
    _LANG = lang or "ja"


def t(key, **kw):
    text = _load(_LANG).get(key)
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
