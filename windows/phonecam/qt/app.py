# -*- coding: utf-8 -*-
"""Qt UI の起動: QApplication の設定 (スタイル・フォント・ライト/ダーク) と main()。

    python windows/phonecam_qt.py [--setup-wizard | --setup-diagnose]
セットアップ (ウィザード・診断) も Qt 版。Tk 版 (phonecam_receiver.py) はもう配布の入口ではない。
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


def configure_basic(app, theme="system"):
    """スタイル (Windows 11 / Fusion)・フォント・テーマを設定する (Controller なしで使える)。"""
    from PySide6.QtWidgets import QStyleFactory
    from phonecam.i18n import get_language
    keys = {k.lower(): k for k in QStyleFactory.keys()}
    app.setStyle(keys.get("windows11") or keys.get("fusion") or "Fusion")
    apply_font(app, get_language())
    apply_theme(app, theme)


def configure(app, ctrl):
    """スタイル・フォント・テーマを設定し、theme の変更に追従させる。"""
    configure_basic(app, ctrl.get("theme", "system"))
    ctrl.settings_changed.connect(lambda k, v: apply_theme(app, v) if k == "theme" else None)


def _smoke_hook(result, name, close):
    """PHONECAM_SMOKE=1 (CI の起動テスト): 数秒後に、セットアップの画面を作っても tkinter が読み込まれていないことを
    APPDATA/PhoneCam/setup_smoke_<name>.txt に書いて閉じる。読み込まれていたら result["tk"] = True (終了コード 3)。"""
    if os.environ.get("PHONECAM_SMOKE") != "1":
        return
    from PySide6.QtCore import QTimer

    def done():
        result["tk"] = "tkinter" in sys.modules
        try:
            from phonecam.core.paths import _appdata_dir
            folder = _appdata_dir()
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "setup_smoke_%s.txt" % name), "w", encoding="utf-8") as f:
                f.write("tkinter loaded: %s\n" % result["tk"])
        except Exception:
            pass
        close()

    QTimer.singleShot(3000, done)


def run_setup_window(args):
    """--setup-wizard / --setup-diagnose: アプリ本体を開かずに、セットアップだけを単体のウィンドウで開く
    (アプリ本体からは別プロセスではなく、同じプロセスのダイアログで開く。こちらは手動の起動用)。"""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        _message_box("PySide6 が見つかりません。\n\npython -m pip install -r windows/requirements-qt.txt を実行してください。\n\n%s" % e)
        return 1
    from phonecam.core.settings import load_settings, migrate_legacy_settings, save_settings
    from phonecam.i18n import set_language
    migrate_legacy_settings()
    settings = load_settings()
    set_language(settings.get("language", "system"))   # phonecam_setup / qt.setup_* は import 時に t() を評価するので、その前に
    set_app_user_model_id()
    from phonecam.core import crashlog
    crashlog.install()
    app = QApplication(sys.argv)
    app.setApplicationName("PhoneCam")
    app.setQuitOnLastWindowClosed(True)
    configure_basic(app, settings.get("theme", "system"))
    from phonecam.qt.main_window import window_icon
    app.setWindowIcon(window_icon())
    from phonecam.core.softcam_reg import bind_setup_host
    bind_setup_host()
    smoke = {"tk": False}
    if "--setup-diagnose" in args:
        from phonecam.qt.setup_diag import DiagnosticsDialog
        dialog = DiagnosticsDialog(None)
        _smoke_hook(smoke, "diagnose", dialog.close)
        dialog.exec()
        return 3 if smoke["tk"] else 0
    import phonecam_setup
    from phonecam.qt.setup_common import QtRoot
    from phonecam.qt.setup_wizard import SetupWizardDialog, make_audio_guard

    def audio_get(key):
        return load_settings().get(key)

    def audio_set(key, value):
        current = load_settings()
        current[key] = value
        save_settings(current)

    def finish_setup():
        current = load_settings()
        current["setup_done"] = True
        save_settings(current)

    after_update = args[args.index("--after-update") + 1] if "--after-update" in args and args.index("--after-update") + 1 < len(args) else None
    controller = phonecam_setup.SetupController(QtRoot(), audio_guard=make_audio_guard(audio_get, audio_set))
    dialog = SetupWizardDialog(controller, None, on_finish=finish_setup, on_skip=finish_setup, update_version=after_update)
    _smoke_hook(smoke, "wizard", lambda: dialog._end(lambda: None))
    dialog.exec()
    controller.close()
    return 3 if smoke["tk"] else 0


def main():
    if sys.argv[1:2] in (["--setup-wizard"], ["--setup-diagnose"]):
        return run_setup_window(sys.argv[1:])
    helper = helper_main(sys.argv[1:])
    if helper is not None:
        return helper
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        _message_box("PySide6 が見つかりません。\n\npython -m pip install -r windows/requirements-qt.txt を実行してください。\n\n%s" % e)
        return 1
    from phonecam.core.settings import load_settings, migrate_legacy_settings
    from phonecam.i18n import set_language
    migrate_legacy_settings()
    settings = load_settings()
    set_language(settings.get("language", "system"))
    from phonecam.controller import Controller
    from phonecam.qt.main_window import MainWindow
    set_app_user_model_id()
    from phonecam.core import crashlog
    crashlog.install()  # Qt のスロット内の例外・ネイティブのクラッシュを %APPDATA%/PhoneCam に記録する
    ctrl = Controller(settings=settings)
    app = QApplication(sys.argv)
    app.setApplicationName("PhoneCam")
    app.setQuitOnLastWindowClosed(True)
    configure(app, ctrl)
    win = MainWindow(ctrl)
    win.restore_state()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(600, ctrl.first_run_setup)
    app.aboutToQuit.connect(win.cameras.close)
    return app.exec()
