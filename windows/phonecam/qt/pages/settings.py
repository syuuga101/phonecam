"""General preferences, release checks, setup and project links."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel

from phonecam.core.settings import PREVIEW_FPS_CHOICES
from phonecam.i18n import t
from phonecam.qt.widgets import Hint, Page, bound_combo, bound_switch, button

_REPO = "https://github.com/syuuga101/phonecam"
_TRANSLATE_URL = ""  # Crowdin プロジェクト作成後に URL を入れる (空のあいだは翻訳協力の行を出さない)


def build(ctrl):
    page = Page()
    general = page.add_section(t("settings.general.title"))
    for icon, key, choices, setter, hint in (
        ("globe", "language", ("system", "ja", "en"), ctrl.set_language, t("settings.language.hint")),
        ("palette", "theme", ("system", "light", "dark"), ctrl.set_theme, None),
    ):
        items = [(value, t(f"settings.{key}.{value}")) for value in choices]
        general.add_row(icon, t(f"settings.{key}.label"), hint).addControl(
            bound_combo(ctrl, key, items, setter=setter))

    general.add_row("gauge", t("settings.preview_fps.label"), t("settings.preview_fps.hint")).addControl(
        bound_combo(ctrl, "preview_fps", [(v, f"{v} fps") for v in PREVIEW_FPS_CHOICES]))

    updates = page.add_section(t("update.section"))
    updates.add_row("info", t("update.version.label")).addControl(QLabel(ctrl.version_text()))
    check_row = updates.add_row("download", t("update.check.label"))
    check = button(t("update.check.button"))
    check.clicked.connect(lambda: ctrl.check_updates())
    check_row.addControl(check)
    updates.add_row("clock", t("update.auto.label")).addControl(bound_switch(ctrl, "update_check"))
    status = Hint("")
    updates.add_padded(status)

    def refresh_update(text, busy):
        status.setText(text)
        check.setEnabled(not busy)

    ctrl.update_status_changed.connect(refresh_update)
    refresh_update(ctrl.update_status_text, ctrl.update_check_busy)

    setup = page.add_section(t("settings.setup.title"))
    wizard = button(t("settings.setup.open.button"))
    wizard.clicked.connect(lambda: ctrl.open_setup())
    setup.add_row("play", t("settings.setup.open.label")).addControl(wizard)
    diagnose = button(t("setup.diagnose.button"))
    diagnose.clicked.connect(lambda: ctrl.open_setup(diagnose=True))
    setup.add_row("wrench", t("setup.diagnose.label"), t("setup.diagnose.hint")).addControl(diagnose)

    about = page.add_section(t("settings.about.title"))
    about.add_row("person", t("settings.about.author")).addControl(QLabel("syuuga101"))
    for icon, name, label, url in (
        ("download", "repo", "GitHub", _REPO),
        ("document", "guide", t("settings.about.guide.link"), _REPO + "/wiki"),
        ("heart", "translate", "Crowdin", _TRANSLATE_URL),
        ("info", "issues", "GitHub Issues", _REPO + "/issues"),
    ):
        if not url:
            continue
        link = button(label)
        link.clicked.connect(lambda _, target=url: QDesktopServices.openUrl(QUrl(target)))
        about.add_row(icon, t(f"settings.about.{name}")).addControl(link)
    about.add_row("document", t("settings.about.license")).addControl(QLabel("GPL-2.0-or-later"))
    return page.finish()
