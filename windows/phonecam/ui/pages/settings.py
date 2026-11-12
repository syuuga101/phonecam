# -*- coding: utf-8 -*-
"""設定ページ (一般・更新・セットアップ・アプリについて)。iPhone の設定画面と同じ構成。"""

import locale

from phonecam import i18n
from phonecam.i18n import t
from phonecam.ui.dpi import px
from phonecam.ui.tkdeps import tk, ttk
from phonecam.ui.update_ui import page, row, section
from phonecam.ui.util import webbrowser_open

AUTHOR = "syuuga101"
REPO_URL = "https://github.com/syuuga101/phonecam"
GUIDE_URL = "https://github.com/syuuga101/phonecam/tree/main/docs/guide"
TRANSLATE_URL = "https://crowdin.com/project/phonecam"  # 仮 (Crowdin プロジェクト作成後に差し替える)
ISSUES_URL = REPO_URL + "/issues"
LICENSE_TEXT = "GPL-2.0-or-later"

LANGUAGE_VALUES = ("system", "ja", "en")
THEME_VALUES = ("system", "light", "dark")


def resolve_language(value):
    """設定値 (system/ja/en…) から実際に使う言語コードを決める。"""
    fn = getattr(i18n, "resolve_language", None)
    if fn is not None:
        try:
            return fn(value)
        except Exception:
            pass
    if value in (None, "", "system"):
        try:
            lang = (locale.getdefaultlocale()[0] or "")
        except Exception:
            lang = ""
        return "ja" if lang.lower().startswith("ja") else "en"
    return value


def _choice(card, icon, title, hint_text, values, key_prefix, current, on_pick):
    """「ラベル｜右にドロップダウン」の行。戻り値: (行, StringVar)。"""
    r = row(card, icon, title, hint_text)
    labels = [t("%s.%s" % (key_prefix, v)) for v in values]
    var = tk.StringVar(value=labels[values.index(current)] if current in values else labels[0])
    cb = ttk.Combobox(r, textvariable=var, values=labels, state="readonly", width=16)
    cb.pack(side="right")

    def picked(_e=None):
        try:
            on_pick(values[labels.index(var.get())])
        except (ValueError, IndexError):
            pass

    cb.bind("<<ComboboxSelected>>", picked)
    return r, var


def _link_row(card, icon, title, value_text, url):
    r = row(card, icon, title)
    ttk.Button(r, text=value_text, command=lambda: webbrowser_open(url), style="Toolbutton").pack(side="right")
    return r


def _language_picked(app, value):
    app._changed("language", value, None)
    try:
        i18n.set_language(resolve_language(value))
    except Exception:
        pass


def _theme_picked(app, value):
    app._changed("theme", value, None)
    try:
        from phonecam.ui import theme
        if hasattr(theme, "apply"):
            theme.apply(getattr(app, "root", None) or app, value)
    except Exception:
        pass


def _open_setup(app):
    fn = getattr(app, "open_setup", None)
    if fn is None:
        fn = lambda: app.open_setup_wizard(rerun=True)
    fn()


def build(app, parent):
    body = page(parent)
    s = app.settings

    # 一般
    card = section(body, t("settings.general.title"))
    _choice(card, "globe", t("settings.language.label"), t("settings.language.hint"), LANGUAGE_VALUES,
            "settings.language", s.get("language", "system"), lambda v: _language_picked(app, v))
    _choice(card, "palette", t("settings.theme.label"), None, THEME_VALUES,
            "settings.theme", s.get("theme", "system"), lambda v: _theme_picked(app, v))

    # 更新
    card = section(body, t("update.section"))
    try:
        app._build_update_rows(card)
    except Exception:
        pass

    # セットアップ
    card = section(body, t("settings.setup.title"))
    r = row(card, "play", t("settings.setup.open.label"))
    ttk.Button(r, text=t("settings.setup.open.button"), command=lambda: _open_setup(app)).pack(side="right")
    r = row(card, "wrench", t("setup.diagnose.label"), t("setup.diagnose.hint"))
    ttk.Button(r, text=t("setup.diagnose.button"), command=app._setup_diagnose).pack(side="right")

    # アプリについて
    card = section(body, t("settings.about.title"))
    r = row(card, "person", t("settings.about.author"))
    ttk.Label(r, text=AUTHOR).pack(side="right")
    _link_row(card, "download", t("settings.about.repo"), "GitHub", REPO_URL)
    _link_row(card, "document", t("settings.about.guide"), t("settings.about.guide.link"), GUIDE_URL)
    _link_row(card, "heart", t("settings.about.translate"), "Crowdin", TRANSLATE_URL)
    r = row(card, "document", t("settings.about.license"))
    ttk.Label(r, text=LICENSE_TEXT).pack(side="right")
    _link_row(card, "info", t("settings.about.issues"), "GitHub Issues", ISSUES_URL)


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = ()
