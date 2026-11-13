# -*- coding: utf-8 -*-
"""settings ページ (Qt)。担当者がここを埋める: build(ctrl) -> Page。
使ってよいのは Controller (ctrl) と phonecam.qt.widgets だけ (docs: scratchpad/qt_contract.md)。
"""

from phonecam.i18n import t
from phonecam.qt.widgets import Page


def build(ctrl):
    page = Page()
    sec = page.add_section(t("nav.settings"))
    sec.add_row("info", t("shell.placeholder"))
    page.finish()
    return page
