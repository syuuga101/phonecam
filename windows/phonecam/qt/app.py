# -*- coding: utf-8 -*-
"""Qt UI の起動: QApplication の設定 (スタイル・フォント・ライト/ダーク) と main()。

    python windows/phonecam_qt.py
Tk 版 (phonecam_receiver.py) は全ページの移植が終わるまで配布の入口のまま。
"""

import os
import sys

from phonecam.core.softcam_reg import helper_main


def _message_box(text):
    """PySide6 が無いときなど、Qt を使えない起動エラーの表示 (windowed の exe にはコンソールが無い)。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "PhoneCam", 0x10)
        return
    except Exception:
        pass
    try:
        sys.stderr.write(text + "\n")
    except Exception:
        pass


def set_app_user_model_id(app_id="PhoneCam.Receiver"):
    """タスクバーで python.exe ではなく PhoneCam として扱わせる。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _light_palette():
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    R = QPalette.ColorRole
    for role, col in ((R.Window, "#f3f3f3"), (R.WindowText, "#1a1a1a"), (R.Base, "#ffffff"),
                      (R.AlternateBase, "#f9f9f9"), (R.Text, "#1a1a1a"), (R.Button, "#fbfbfb"),
                      (R.ButtonText, "#1a1a1a"), (R.Highlight, "#0067c0"), (R.HighlightedText, "#ffffff"),
                      (R.ToolTipBase, "#ffffff"), (R.ToolTipText, "#1a1a1a"), (R.PlaceholderText, "#6b6b6b"),
                      (R.Link, "#0067c0")):
        p.setColor(role, QColor(col))
    return p


def _dark_palette():
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    R = QPalette.ColorRole
    for role, col in ((R.Window, "#202020"), (R.WindowText, "#ffffff"), (R.Base, "#2b2b2b"),
                      (R.AlternateBase, "#323232"), (R.Text, "#ffffff"), (R.Button, "#2d2d2d"),
                      (R.ButtonText, "#ffffff"), (R.Highlight, "#60cdff"), (R.HighlightedText, "#000000"),
                      (R.ToolTipBase, "#2b2b2b"), (R.ToolTipText, "#ffffff"), (R.PlaceholderText, "#9d9d9d"),
                      (R.Link, "#60cdff")):
        p.setColor(role, QColor(col))
    return p


_forced = [False]


def apply_theme(app, mode):
    """設定 theme ("system"/"light"/"dark") を反映する。Qt 6.8 の setColorScheme で切り替え、
    それでパレットが変わらない環境 (offscreen など) では明示のパレットで補う。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    scheme_cls = getattr(Qt, "ColorScheme", None)
    hints = app.styleHints()
    if scheme_cls is not None and hasattr(hints, "setColorScheme"):
        scheme = {"light": scheme_cls.Light, "dark": scheme_cls.Dark}.get(mode, scheme_cls.Unknown)
        hints.setColorScheme(scheme)
    want_dark = {"dark": True, "light": False}.get(mode)
    if want_dark is None:
        if _forced[0]:
            _forced[0] = False
            app.setPalette(app.style().standardPalette())
        return
    cur_dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if cur_dark != want_dark or _forced[0]:
        app.setPalette(_dark_palette() if want_dark else _light_palette())
        _forced[0] = True


def apply_font(app, language):
    from PySide6.QtGui import QFont
    family = "Yu Gothic UI" if language == "ja" else "Segoe UI Variable Text"
    f = QFont()
    f.setFamilies([family, "Segoe UI", "Yu Gothic UI", "Meiryo UI"])
    f.setPointSizeF(10.0)
    app.setFont(f)


def configure(app, ctrl):
    """スタイル・フォント・テーマを設定し、theme の変更に追従させる。"""
    from PySide6.QtWidgets import QStyleFactory
    from phonecam.i18n import get_language
    keys = {k.lower(): k for k in QStyleFactory.keys()}
    app.setStyle(keys.get("windows11") or keys.get("fusion") or "Fusion")
    apply_font(app, get_language())
    apply_theme(app, ctrl.get("theme", "system"))
    ctrl.settings_changed.connect(lambda k, v: apply_theme(app, v) if k == "theme" else None)


def main():
    helper = helper_main(sys.argv[1:])
    if helper is not None:
        return helper
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        _message_box("PySide6 が見つかりません。\n\npython -m pip install -r windows/requirements-qt.txt を実行してください。\n\n%s" % e)
        return 1
    from phonecam.controller import Controller
    from phonecam.qt.main_window import MainWindow
    set_app_user_model_id()
    ctrl = Controller()
    app = QApplication(sys.argv)
    app.setApplicationName("PhoneCam")
    app.setQuitOnLastWindowClosed(True)
    configure(app, ctrl)
    win = MainWindow(ctrl)
    win.restore_state()
    app.aboutToQuit.connect(ctrl.close)
    return app.exec()
