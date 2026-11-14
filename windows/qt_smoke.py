# -*- coding: utf-8 -*-
"""Qt UI のスクリーンショット・スモークテスト (CI 用)。

    python windows/qt_smoke.py [--out qt-screens]

接続中のサンプル値 (iPhone 13 / USB / 1080p60 / 31ms / 正常 / 82%) を入れた demo コントローラで
メインウィンドウ (論理 1280x800) を作り、ナビの各ページ x {ライト, ダーク} x {倍率 1.0, 2.0} を
window.grab() で PNG にする。倍率は QT_SCALE_FACTOR を変えた子プロセスで切り替える。
各ページについて、スクロール位置を中ほどにした画像 (*_scroll.png) も撮る。
ページの build が失敗していたら終了コード 1 (画像は撮れた分だけ残す)。
最後に、セットアップのウィザード (ようこそ / 確認 / インストール / iPhone の 8 段階) と SideStore の導入チェックも、
サンプルの検出結果で x {ライト, ダーク} 撮る (setup_*.png)。本体から開く経路 (同じプロセスのダイアログ) と、
tkinter が読み込まれないことも確かめる。
カメラが 1 台のときは「カメラ」ページとカメラの切り替えが出ないことを確かめ、2 台にしたあとは
カメラページ・2 台目の接続ページ・音声ページ・「カメラを追加」ウィザード (3 ステップ)・iPhone の選択ダイアログも撮る。
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SCALES = ("1.0", "2.0")
THEMES = ("light", "dark")
W, H = 1280, 800
SCROLL_H = 560      # スクロールの確認用に低くする (短いページでもスクロールが起きるように)


def _settle(app, ms=350):
    from PySide6.QtCore import QThread
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        QThread.msleep(10)
    app.processEvents()


# ---------------------------------------------------------------------------
# 翻訳キーがそのまま画面に出ていないかの検査 (pytest の Qt テストと共用)
# ---------------------------------------------------------------------------

_KEY_RE = None
_NOT_KEYS = {"setup.exe", "setup.msi"}


def untranslated_texts(root):
    """root の下のすべてのウィジェットの文字 (ラベル・ボタン・選択肢・ツールチップ・見出しなど) のうち、
    翻訳キー (例: status.iphone.hot) がそのまま残っているものを [(部品, 種類, 文字)] で返す。"""
    global _KEY_RE
    import re
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget,
                                   QWidget)
    from phonecam.i18n import _load
    if _KEY_RE is None:
        _KEY_RE = re.compile(r"(?<![\w./-])([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)(?![\w])")
    prefixes = {key.split(".")[0] for key in _load("ja")}
    found = []

    def check(owner, kind, text):
        for match in _KEY_RE.finditer(text or ""):
            token = match.group(1)
            if token.split(".")[0] in prefixes and token not in _NOT_KEYS:
                found.append((type(owner).__name__, kind, text))
                return

    widgets = [root] + root.findChildren(QWidget)
    for widget in widgets:
        check(widget, "toolTip", widget.toolTip())
        check(widget, "accessibleName", widget.accessibleName())
        if widget.isWindow():
            check(widget, "windowTitle", widget.windowTitle())
        if isinstance(widget, QLabel):
            check(widget, "text", widget.text())
        if isinstance(widget, QAbstractButton):
            check(widget, "text", widget.text())
        if isinstance(widget, QGroupBox):
            check(widget, "title", widget.title())
        if isinstance(widget, QLineEdit):
            check(widget, "placeholder", widget.placeholderText())
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                check(widget, "item", widget.itemText(index))
        if isinstance(widget, QListWidget):
            for index in range(widget.count()):
                check(widget, "item", widget.item(index).text())
    return found


def check_expander_headers(win, failed):
    """全ページの折りたたみ: 説明が入っていて、要約が「—」だけではなく、説明・要約・矢印が重ならないこと。"""
    from phonecam.qt.widgets import NO_VALUE, Expander
    from phonecam.qt.main_window import NAV_ITEMS
    for i, (key, _icon) in enumerate(NAV_ITEMS):
        if key == "cameras":
            continue
        win.select(i)
        _settle(win._smoke_app, 120)
        for ex in win.current_page().findChildren(Expander):
            where = "%s/%s" % (key, ex.title_label.text())
            if not ex.description_label.text_full():
                failed.append((key, "expander '%s' has no description" % where))
            if ex.summary_label.text_full().strip() in ("", NO_VALUE) and ex.summary_label.isVisible():
                failed.append((key, "expander '%s' shows an empty summary" % where))
            header = ex.header
            desc_right = ex.description_label.mapTo(header, ex.description_label.rect().topRight()).x()
            if ex.summary_label.isVisible():
                summary_left = ex.summary_label.mapTo(header, ex.summary_label.rect().topLeft()).x()
                if desc_right > summary_left:
                    failed.append((key, "expander '%s': description overlaps the summary (%d > %d)" % (where, desc_right, summary_left)))
                if ex.summary_label.mapTo(header, ex.summary_label.rect().topRight()).x() > ex.arrow.mapTo(header, ex.arrow.rect().topLeft()).x():
                    failed.append((key, "expander '%s': summary overlaps the arrow" % where))


def check_animation(app, win, failed):
    """開閉アニメーションを手で 1 コマずつ (16ms 刻み) 進め、1 コマの処理時間と、レイアウト要求の数を測る。
    1 コマが 16ms を超えるとガクつく。CI の揺れを見込んで、失敗にするのは 50ms 超えだけにし、実測値は表示する。"""
    from PySide6.QtCore import QEvent, QObject
    from phonecam.qt.main_window import NAV_ITEMS
    from phonecam.qt.widgets import Expander

    class Count(QObject):
        n = 0

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.LayoutRequest:
                Count.n += 1
            return False

    counter = Count()
    app.installEventFilter(counter)
    try:
        key = "status"
        win.select(next(i for i, (k, _icon) in enumerate(NAV_ITEMS) if k == key))
        win.resize(W, H)
        _settle(app)
        ex = win.current_page().findChildren(Expander)[0]
        ex.set_expanded(False, animate=False, save=False)
        _settle(app, 150)
        for expanded in (True, False, True, False):
            Count.n = 0
            ex.set_expanded(expanded, animate=True, save=False)
            anim = ex._anim
            if anim.state() != anim.State.Running:
                failed.append((key, "expander animation did not start (expanded=%s)" % expanded))
                continue
            anim.pause()
            times, heights = [], []
            for ms in range(0, anim.duration() + 1, 16):
                t0 = time.perf_counter()
                anim.setCurrentTime(ms)
                app.processEvents()
                times.append((time.perf_counter() - t0) * 1000.0)
                heights.append(ex.curtain.height())
            anim.stop()
            ex._animation_finished()
            _settle(app, 150)
            steps = [b - a for a, b in zip(heights, heights[1:])]
            monotonic = all(d >= 0 for d in steps) if expanded else all(d <= 0 for d in steps)
            print("  animation expanded=%s frames=%d max=%.1fms avg=%.1fms layout_requests=%d" % (
                expanded, len(times), max(times), sum(times) / len(times), Count.n))
            if not monotonic:
                failed.append((key, "expander height is not monotonic while animating: %s" % heights))
            if max(times) > 50:
                failed.append((key, "expander animation frame took %.1fms (> 50ms)" % max(times)))
            if ex.curtain.isVisible() or ex.content.isVisible() != expanded:
                failed.append((key, "expander is in a wrong state after the animation (expanded=%s)" % expanded))
            if expanded and ex.content.height() < ex.content.layout().minimumSize().height():
                failed.append((key, "expander content is squeezed after opening (%d)" % ex.content.height()))
        ex.set_expanded(False, animate=False, save=False)
    finally:
        app.removeEventFilter(counter)


def _sample_frame():
    """プレビュー用のサンプル映像 (横グラデーション + 丸)。numpy が無ければ None。"""
    try:
        import numpy as np
    except Exception:
        return None
    h, w = 540, 960
    y, x = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[..., 0] = (40 + 120 * x / w).astype(np.uint8)
    img[..., 1] = (60 + 100 * y / h).astype(np.uint8)
    img[..., 2] = 150
    mask = (x - w * 0.5) ** 2 + (y - h * 0.45) ** 2 < (h * 0.28) ** 2
    img[mask] = (235, 200, 170)
    return img


# ---------------------------------------------------------------------------
# セットアップ (ウィザード・診断)
# ---------------------------------------------------------------------------

def _footer_visible(dlg):
    """下のボタン行 (次へ) が、ウィンドウの中に見えている。"""
    from PySide6.QtCore import QPoint
    btn = dlg.next_btn
    if not btn.isVisibleTo(dlg):
        return True   # このページでは次へを出さない
    top = btn.mapTo(dlg, QPoint(0, 0)).y()
    return top >= 0 and top + btn.height() <= dlg.height()


def _overflow(dlg):
    """本文の中で、右端が表示幅からはみ出している部品 (文字切れの疑い)。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget
    body = dlg.scroll.widget()
    width = dlg.scroll.viewport().width()
    bad = []
    for w in body.findChildren(QWidget):
        if not w.isVisibleTo(body):
            continue
        right = w.mapTo(body, QPoint(w.width(), 0)).x()
        if right > width + 1:
            bad.append("%s right=%d > %d" % (type(w).__name__, right, width))
    return bad


def setup_shots(app, ctrl, win, out, scale):
    """ウィザード全段階と診断ダイアログ x {ライト, ダーク} を撮る。失敗の一覧 [(名前, 理由)] を返す。"""
    import tempfile
    import threading
    from PySide6.QtCore import Qt
    import phonecam_setup as ps
    from phonecam.i18n import t
    from phonecam.qt.setup_common import Fold, QtRoot
    from phonecam.qt.setup_diag import DiagnosticsDialog
    from phonecam.qt.setup_wizard import SetupWizardDialog

    failed = []
    tk_before = "tkinter" in sys.modules
    iphone = ps.iphone
    fake_snap = {"service": True, "usb": ["00008110-001A2B3C4D5E6F01"], "trusted": True}
    saved = (iphone.query_usbmux, iphone.probe_phonecam, ps.find_iloader, ps.find_itunes, dict(ps._host))
    iphone.query_usbmux = lambda *a, **k: fake_snap
    iphone.probe_phonecam = lambda *a, **k: False
    ps.find_iloader = lambda: r"C:\Program Files\iloader\iloader.exe"
    ps.find_itunes = lambda: None
    ps.bind(is_frozen=lambda: True, link_connected=lambda: False)
    tmp = tempfile.mkdtemp(prefix="pcsetup")
    S_OK, S_MISS, S_WARN = ps.STATE_OK, ps.STATE_MISSING, ps.STATE_WARN
    statuses = {
        "camera": {"state": S_MISS, "detail": ""},
        "vcredist": {"state": S_OK, "detail": ""},
        "itunes": {"state": S_WARN, "detail": t("setup.wizard.detect_itunes_3")},
        "iloader": {"state": S_MISS, "detail": ""},
        "vbcable": {"state": S_MISS, "detail": ""},
    }

    def make_controller():
        c = ps.SetupController(QtRoot(), detector=lambda: statuses,
                               engine=ps.SetupEngine(directory=tmp, detector=lambda: statuses))
        c.statuses = dict(statuses)
        c.checked = True
        c.apply_defaults()
        return c

    def make_flow(phase, ids=("itunes", "iloader", "vbcable")):
        flow = ps.GuidedFlow(list(ids), tmp, detector=lambda: statuses)
        flow.phase = phase
        flow.statuses = dict(statuses)
        flow.job = {"path": os.path.join(tmp, "x.exe"), "args": [], "cwd": tmp, "sha256": "0"}
        return flow

    def settle_dialog(dlg, ms=350):
        _settle(app, ms)
        dlg.grab()      # 1 回目は保留中のレイアウトを確定させるだけ
        _settle(app, 200)

    def snap(dlg, name, theme, check_scroll=False):
        settle_dialog(dlg)
        dlg.grab().save(os.path.join(out, "%s_%s_x%s.png" % (name, theme, scale)))
        if not _footer_visible(dlg):
            failed.append((name, "footer button is outside the window"))
        for line in _overflow(dlg):
            failed.append((name, "overflow: " + line))
        if check_scroll:
            # 低いウィンドウでスクロール: ヘッダとボタン行は動かず、本文だけが動く
            dlg.resize(720, 440)
            settle_dialog(dlg, 250)
            bar = dlg.scroll.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            _settle(app, 150)
            dlg.grab().save(os.path.join(out, "%s_scroll_%s_x%s.png" % (name, theme, scale)))
            if not _footer_visible(dlg):
                failed.append((name, "scrolled: footer button is outside the window"))
            if bar.maximum() <= 0:
                failed.append((name, "scrolled: body does not scroll at a low window (max=%d)" % bar.maximum()))
            dlg.resize(720, 760)
            _settle(app, 250)

    def new_dialog(controller, **kw):
        dlg = SetupWizardDialog(controller, None, **kw)
        dlg.auto_fit = False
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg._visited.update(step["id"] for step in iphone.IPHONE_STEPS)   # 自動で次へ進めない (全段階を順に撮る)
        dlg.resize(720, 760)
        dlg.show()
        _settle(app, 250)
        return dlg

    def show(dlg, page):
        """ページを開く。検出・時計のタイマーは止める (撮るたびに内容が変わらないように)。"""
        dlg.show_page(page)
        dlg._pump_timer.stop()
        dlg._guided_timer.stop()
        dlg._hop_timer.stop()

    def close_dialog(dlg):
        dlg._end(lambda: None)
        dlg.deleteLater()
        _settle(app, 100)

    try:
        for theme in THEMES:
            ctrl.set_theme(theme)
            _settle(app)
            # 0 ようこそ / 1 必要なソフトの確認 (マイクの選択つき)
            c = make_controller()
            dlg = new_dialog(c, update_version="2.8.0")
            snap(dlg, "setup_00_welcome", theme)
            show(dlg, ps.PAGE_CHECK)
            snap(dlg, "setup_01_check", theme, check_scroll=True)
            dlg.mic_cb.setChecked(True)   # VB-CABLE も選ぶ
            _settle(app, 200)
            snap(dlg, "setup_01_check_mic", theme)
            # 2 インストール: ダウンロード中 -> 手順つき (開く待ち) -> 手順つき (実行中・長い) -> 結果
            c.busy = True
            c.stage = t("setup.wizard.prepare_prog_1", name="Visual C++ Runtime", done="12.3 MB", total="24.0 MB")
            c.progress = 0.45
            c.log_lines = ["Visual C++ Runtime: downloaded", "PhoneCam camera: registered"]
            show(dlg, ps.PAGE_INSTALL)
            snap(dlg, "setup_02_install_progress", theme)
            c.busy = False
            c.outcome = {"results": {"camera": {"ok": True, "detail": "", "code": 0},
                                     "vcredist": {"ok": False, "detail": t("setup.wizard.explain_exit_code_2"),
                                                  "code": 1603, "technical": "exit code 1603"}},
                         "manual": [], "reboot": False, "error": "", "statuses": {}, "ids": ["camera", "vcredist"]}
            c.guided = make_flow(ps.GuidedFlow.READY)
            c.guided.notice = t("setup.wizard.guidedflow_prepare_7")
            show(dlg, ps.PAGE_INSTALL)
            snap(dlg, "setup_02_install_guided_ready", theme, check_scroll=True)
            c.guided.phase = ps.GuidedFlow.RUNNING
            c.guided.notice = ""
            c.guided.launched_at = c.guided.clock() - 200
            c.guided.detected = True
            show(dlg, ps.PAGE_INSTALL)
            snap(dlg, "setup_02_install_guided_running", theme)
            c.guided.finished = True
            c.guided.results = {"itunes": {"ok": True, "skipped": False, "detail": ""},
                                "iloader": {"ok": True, "skipped": False, "detail": ""},
                                "vbcable": {"ok": False, "skipped": True, "detail": t("setup.wizard.guidedflow_skip_1")}}
            c.outcome["results"].update({k: dict(v, code=None) for k, v in c.guided.results.items()})
            c.outcome["ids"] += ["itunes", "iloader", "vbcable"]
            c.outcome["reboot"] = True
            show(dlg, ps.PAGE_INSTALL)
            snap(dlg, "setup_02_install_done", theme)
            # 3〜10 iPhone の手順 (全 8 段階)
            for i, step in enumerate(iphone.IPHONE_STEPS):
                show(dlg, ps.PAGE_IPHONE + i)
                dlg.snap = fake_snap
                dlg._render_chip()
                name = "setup_step%d_%s" % (i + 1, step["id"])
                snap(dlg, name, theme, check_scroll=step["id"] in ("iloader", "vpn", "phonecam"))
                if step["id"] == "iloader":
                    dlg.confirm_cb.setChecked(True)   # 「済んだ」
                    _settle(app, 200)
                    snap(dlg, name + "_confirmed", theme)
                    folds = dlg.findChildren(Fold)
                    if len(folds) != 2:
                        failed.append((name, "expected 2 folds (AltStore migration / pairing file), got %d" % len(folds)))
                    for fold in folds:
                        fold.set_open(True)
                    _settle(app, 250)
                    snap(dlg, name + "_folds", theme, check_scroll=True)
                if step["id"] == "sidestore":
                    folds = dlg.findChildren(Fold)
                    if len(folds) != 1:
                        failed.append((name, "expected 1 fold (pairing file), got %d" % len(folds)))
                    for fold in folds:
                        fold.set_open(True)
                    _settle(app, 250)
                    snap(dlg, name + "_folds", theme)
            close_dialog(dlg)
            # 診断ダイアログ (実行中 -> 結果)
            gate = threading.Event()
            results = [ps._diag("amds", t("setup.wizard.run_diagnostics_7"), ps.DIAG_OK),
                       ps._diag("itunes", t("setup.wizard.run_diagnostics_17"), ps.DIAG_WARN, t("setup.wizard.detect_itunes_3"),
                                t("setup.wizard.run_diagnostics_18")),
                       ps._diag("usb", t("setup.wizard.run_diagnostics_22"), ps.DIAG_NG, t("setup.wizard.run_diagnostics_25"),
                                t("setup.wizard.run_diagnostics_26")),
                       ps._diag("iloader", t("setup.wizard.check_iloader_2"), ps.DIAG_NG, t("setup.wizard.check_iloader_3"),
                                t("setup.wizard.check_iloader_4"))]

            def runner(progress=None):
                if progress:
                    progress(t("setup.wizard.run_diagnostics_21"))
                gate.wait(10)
                return results

            diag = DiagnosticsDialog(None, runner=runner)
            diag.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            diag.resize(720, 760)
            diag.show()
            snap_diag = lambda name: (settle_dialog(diag), diag.grab().save(os.path.join(out, "%s_%s_x%s.png" % (name, theme, scale))))
            _settle(app, 500)
            snap_diag("setup_diag_running")
            gate.set()
            _settle(app, 900)
            if diag.results is None:
                failed.append(("setup_diag", "diagnostics did not finish"))
            snap_diag("setup_diag")
            diag.resize(720, 440)
            _settle(app, 300)
            bar = diag.scroll.verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            _settle(app, 150)
            diag.grab().save(os.path.join(out, "setup_diag_scroll_%s_x%s.png" % (theme, scale)))
            if bar.maximum() <= 0:
                failed.append(("setup_diag", "body does not scroll at a low window"))
            diag.close()
            diag.deleteLater()
            _settle(app, 100)
        # 本体から開く経路: controller.setup_requested -> 同じプロセスのダイアログ。閉じたら再判定 (setup_dialog_closed) が走る
        ctrl._setup_open = True
        ctrl.setup_requested.emit(False, None)
        _settle(app, 500)
        dialogs = win.findChildren(SetupWizardDialog)
        if len(dialogs) != 1:
            failed.append(("setup_open", "expected one in-process wizard dialog, got %d" % len(dialogs)))
        else:
            dialogs[0].auto_fit = False
            dialogs[0]._skip()
            _settle(app, 300)
            if ctrl._setup_open:
                failed.append(("setup_open", "closing the dialog did not call setup_dialog_closed"))
            if not ctrl.get("setup_done"):
                failed.append(("setup_open", "closing the dialog did not mark setup_done"))
        if not tk_before and "tkinter" in sys.modules:
            failed.append(("setup", "tkinter was imported by the setup wizard / diagnostics"))
    except Exception:
        import traceback
        traceback.print_exc()
        failed.append(("setup", "exception while building the setup screens"))
    finally:
        (iphone.query_usbmux, iphone.probe_phonecam, ps.find_iloader, ps.find_itunes, host) = saved
        ps._host.clear()
        ps._host.update(host)
    return failed


def language_shots(app, ctrl, win, out, scale):
    """言語をすぐ切り替える: 英語にした直後の確認ダイアログと、英語表示の状態ページ (「iPhone」の折りたたみを開いた状態) を
    x {ライト, ダーク} 撮る。英語の全ページに翻訳キーが残っていないことと、時間切れで元の言語に戻ることも確かめる。
    失敗の一覧 [(名前, 理由)] を返す。最後は日本語に戻す。"""
    from PySide6.QtCore import QTimer, Qt
    from phonecam.i18n import get_language, t
    from phonecam.qt.language_dialog import LanguageConfirmDialog
    from phonecam.qt.main_window import NAV_ITEMS
    from phonecam.qt.widgets import Expander

    failed = []
    status_row = next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "status")
    win.select_camera(0)
    ctrl.language_trial.disconnect(win._language_trial)   # 確認のダイアログは下で自分で出す (実際の流れは pytest が確かめる)
    try:
        ctrl.set_language("en")
        win.retranslate()
        _settle(app, 500)
        if get_language() != "en" or not ctrl.language_trial_active or ctrl.settings["language"] != "ja":
            failed.append(("language", "choosing English did not switch at once without saving"))
        for row, (key, _icon) in enumerate(NAV_ITEMS):
            if win.nav.item(row).isHidden():
                continue
            win.select(row)
            _settle(app, 150)
            for expander in win.current_page().findChildren(Expander):
                expander.set_expanded(True, animate=False, save=False)
            _settle(app, 100)
            for item in untranslated_texts(win.current_page()):
                failed.append(("language_en_" + key, "translation key shown: %s %s %r" % item))
            for expander in win.current_page().findChildren(Expander):
                for part, text in (("description", expander.description_label.text_full()),
                                   ("summary", expander.summary_label.text_full())):
                    if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text):
                        failed.append(("language_en_" + key, "expander %s is still Japanese: %r" % (part, text)))
            for expander in win.current_page().findChildren(Expander):
                expander.set_expanded(False, animate=False, save=False)
        for theme in THEMES:
            ctrl.set_theme(theme)
            _settle(app)
            dialog = LanguageConfirmDialog(win, "ja", autostart=False)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            dialog.show()
            _settle(app, 300)
            dialog.grab()
            _settle(app, 150)
            dialog.grab().save(os.path.join(out, "lang_dialog_en_%s_x%s.png" % (theme, scale)))
            for item in untranslated_texts(dialog):
                failed.append(("language_dialog", "translation key shown: %s %s %r" % item))
            dialog.close()
            dialog.deleteLater()
            win.resize(W, H)
            win.select(status_row)
            _settle(app, 250)
            for expander in win.current_page().findChildren(Expander):
                expander.set_expanded(expander.key.endswith("status.iphone.title"), animate=False, save=False)
            _settle(app, 250)
            win.current_page().verticalScrollBar().setValue(0)
            _settle(app, 150)
            win.grab()
            _settle(app, 150)
            win.grab().save(os.path.join(out, "lang_en_status_iphone_%s_x%s.png" % (theme, scale)))
            for expander in win.current_page().findChildren(Expander):
                expander.set_expanded(False, animate=False, save=False)
        # 時間切れ (保存されないまま 10 秒): 元の言語に戻り、設定は変わらない
        dialog = LanguageConfirmDialog(win, "ja", seconds=2, autostart=False)
        QTimer.singleShot(0, dialog.tick)
        QTimer.singleShot(0, dialog.tick)
        win.ask_language("ja", dialog)
        win.retranslate()
        _settle(app, 300)
        if get_language() != "ja" or ctrl.settings["language"] != "ja" or ctrl.language_trial_active:
            failed.append(("language", "the language did not return to Japanese when the time ran out"))
        if win.nav.item(status_row).text() != t("nav.status"):
            failed.append(("language", "the navigation was not rebuilt after reverting"))
    finally:
        ctrl.language_trial.connect(win._language_trial)
    return failed


def child(scale, out):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from phonecam.i18n import set_language
    set_language("ja")
    from phonecam.controller import Controller
    from phonecam.qt.app import configure
    from phonecam.qt.main_window import NAV_ITEMS, MainWindow
    from phonecam.qt.widgets import Expander

    ctrl = Controller.demo_connected("ja")
    # Wi-Fi の一覧用: 登録済み (接続中) 1台 + 未登録 1台 (テザリング経由)
    from phonecam.core.wireless import Device
    from phonecam.i18n import t
    known = Device("5f1c0c1e-9a53-4a0e-8b8e-0b6f2f1d3a11", "iPhone 13", ("192.168.1.23",), "482913", "iPhone 13")
    ctrl.settings["known_devices"] = [known.stored()]
    ctrl.demo_discovered = [
        Device(known.id, "iPhone 13", ("192.168.1.23",), "", "iPhone 13"),
        Device("0d2e6a52-3c1b-4f0a-9d4e-7a1b2c3d4e5f", "リビングのiPhone", ("172.20.10.1",), "", "iPhone 15 Pro"),
    ]
    ctrl.settings["mode"] = "auto"
    ctrl.settings["ip"] = "192.168.1.23"
    ctrl.settings["pair_code"] = "482913"
    ctrl.state.update(transport="wifi", peer="192.168.1.23")   # 自動で Wi-Fi につながっている状態
    ctrl.poll()
    frame = _sample_frame()
    if frame is not None:
        ctrl.set_preview_frame(frame, "rgb24")
    app = QApplication(sys.argv)
    configure(app, ctrl)
    win = MainWindow(ctrl, tick=False)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(W, H)
    win.show()
    _settle(app)
    failed = list(win.page_errors)
    win._smoke_app = app
    check_expander_headers(win, failed)
    check_animation(app, win, failed)
    # カメラが 1 台のとき: 「カメラ」ページ・ページ見出し・ナビ下の小さな表示は出さない
    cams_row = next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "cameras")
    if not win.nav.item(cams_row).isHidden() or win.page_header.isHidden() is False or not win.camera_sub.isHidden():
        failed.append(("cameras", "1 camera: camera page / header / sub label must be hidden"))
    for theme in THEMES:
        ctrl.set_theme(theme)   # アプリと同じ経路: settings_changed -> apply_theme (ウィジェット構築後の実行時切替)
        _settle(app)
        print("scale=%s theme=%s window_lightness=%d" % (scale, theme, app.palette().window().color().lightness()))
        for i, (key, _icon) in enumerate(NAV_ITEMS):
            if key == "cameras":
                continue   # 1 台のときは出さない (2 台構成で撮る)
            win.resize(W, H)
            win.select(i)
            _settle(app)
            if not win.page_header.isHidden() or not win.segment.isHidden():
                failed.append((key, "1 camera: segmented switch must be hidden"))
            win.current_page().verticalScrollBar().setValue(0)
            _settle(app)
            base = "%02d_%s_%s_x%s" % (i, key, theme, scale)
            win.grab().save(os.path.join(out, base + ".png"))
            expanders = win.current_page().findChildren(Expander)
            if not expanders:
                failed.append((key, "no expander"))
            else:
                for expander in expanders:
                    expander.set_expanded(True, animate=False, save=False)
                _settle(app, 150)
                win.grab().save(os.path.join(out, base + "_expanded.png"))
                win.resize(W, SCROLL_H)
                _settle(app, 150)
                expanded_bar = win.current_page().verticalScrollBar()
                expanded_bar.setValue(expanded_bar.maximum() // 2)
                _settle(app, 150)
                win.grab().save(os.path.join(out, base + "_expanded_scroll.png"))
                # 小さな折りたたみ (Wi-Fi の「見つからないときは」「IP を手で入れる」) も開いて一番下を撮る
                from phonecam.qt.wireless import Disclosure
                discs = win.current_page().findChildren(Disclosure)
                if discs:
                    for d in discs:
                        d.set_open(True)
                    _settle(app, 200)
                    expanded_bar.setValue(expanded_bar.maximum())
                    _settle(app, 150)
                    win.grab().save(os.path.join(out, base + "_expanded_bottom.png"))
                    for d in discs:
                        d.set_open(False)
                for expander in expanders:
                    expander.set_expanded(False, animate=False, save=False)
            # スクロール: 低いウィンドウで中ほどまで送る
            win.resize(W, SCROLL_H)
            _settle(app, 150)
            bar = win.current_page().verticalScrollBar()
            bar.setValue(bar.maximum() // 2)
            _settle(app, 150)
            win.grab().save(os.path.join(out, base + "_scroll.png"))
            print("  %s: scroll max=%d" % (base, bar.maximum()))
    # 空の状態: 見つかった端末が 2 台 -> 0 台 (登録も消す) に変えてから、Wi-Fi 折りたたみを開いて撮る
    conn = next(i for i, (key, _icon) in enumerate(NAV_ITEMS) if key == "connection")
    win.resize(W, H)
    win.select(conn)
    _settle(app)
    wireless = win.current_page().wireless
    for expander in win.current_page().findChildren(Expander):
        expander.set_expanded(True, animate=False, save=False)
    _settle(app, 200)
    ctrl.settings["known_devices"] = []
    ctrl.demo_discovered = []
    wireless.discovered = []
    wireless._render()
    ctrl.state.update(transport="usb", peer="")   # 接続中の表示は外す
    ctrl.poll()
    for theme in THEMES:
        ctrl.set_theme(theme)
        _settle(app, 500)
        win.current_page().verticalScrollBar().setValue(0)
        _settle(app, 150)
        win.grab()   # 1 回目は保留中のレイアウトを確定させるだけ (撮り直す)
        _settle(app, 150)
        inner = win.current_page().widget()
        win.grab().save(os.path.join(out, "empty_state_%s_x%s.png" % (theme, scale)))
        for expander in win.current_page().findChildren(Expander):
            if expander.expanded:
                shot = wireless.devices_box.mapTo(win.current_page().widget(), wireless.devices_box.rect().topLeft()).y()
                win.current_page().verticalScrollBar().setValue(max(0, shot - 80))
                _settle(app, 150)
                win.grab()
                _settle(app, 150)
                win.grab().save(os.path.join(out, "empty_state_%s_x%s_scroll.png" % (theme, scale)))
                break
    box = wireless.devices_box
    need = box.layout().totalHeightForWidth(box.width())
    print("empty box height=%d need=%d" % (box.height(), need))
    if box.height() < need:
        failed.append(("connection", "empty state squeezed (%d < %d)" % (box.height(), need)))
    # カメラが 2 台以上のとき: カメラページ (1 台接続中・1 台未接続) / 2 台目の接続ページ / 音声ページ / ウィザード / 選択ダイアログ
    from phonecam.qt.multicam import AddCameraWizard, DevicePickDialog
    known2 = Device("0d2e6a52-3c1b-4f0a-9d4e-7a1b2c3d4e5f", "リビングのiPhone", ("172.20.10.1",), "135790", "iPhone 15 Pro")
    known3 = Device("7b3c1d90-5e2a-4c8b-8f61-2a9d0e4b6c77", "仕事用 iPhone", ("192.168.1.40",), "246802", "iPhone 14")
    ctrl.settings["known_devices"] = [known.stored(), known2.stored(), known3.stored()]
    ctrl.demo_discovered = [Device(known.id, "iPhone 13", ("192.168.1.23",), "", "iPhone 13"),
                            Device(known3.id, "仕事用 iPhone", ("192.168.1.40",), "", "iPhone 14")]
    ctrl.demo_usb = {"00008110-001A2B3C4D5E6F01": ""}
    ctrl.changed("target_device", known.id)
    ctrl.state.update(transport="usb", peer="")
    ctrl.poll()
    win.resize(W, H)
    camera2 = win._add_camera()
    win.cameras.assign(camera2, known2, "172.20.10.1")
    camera2.poll()
    camera2.frozen = True                  # exe 版の見た目 (仮想カメラの登録状態を出す) で撮る
    camera2.camera_registration = "ok"
    camera2.camera_registration_changed.emit()
    _settle(app)
    win.select(cams_row)
    win.select_camera(0)
    _settle(app)
    if win.nav.item(cams_row).isHidden() or win.page_header.isHidden():
        failed.append(("cameras", "2 cameras: camera page / header must be visible"))
    for theme in THEMES:
        ctrl.set_theme(theme)
        _settle(app)
        for row, (key, _icon) in enumerate(NAV_ITEMS):
            if key not in ("cameras", "connection", "video", "audio", "settings"):
                continue
            win.select_camera(1 if key in ("connection", "video") else 0)
            win.select(row)
            _settle(app, 450)
            shows_switch = not win.segment.isHidden()
            if shows_switch != (key in ("connection", "video")):
                failed.append((key, "segmented switch visibility is wrong (%s)" % shows_switch))
            win.current_page().verticalScrollBar().setValue(0)
            _settle(app, 150)
            if key == "cameras":
                name = "multi_cameras"
            elif key == "settings":
                name = "multi_settings"
            else:
                name = "multi_cam2_%s" % key if key != "audio" else "multi_audio"
            win.grab().save(os.path.join(out, "%s_%s_x%s.png" % (name, theme, scale)))
            if key == "connection":
                # 仮想カメラが未登録のとき (「準備する」ボタン)
                camera2.camera_registration = "missing"
                camera2.camera_registration_changed.emit()
                _settle(app, 200)
                win.grab().save(os.path.join(out, "multi_cam2_connection_missing_%s_x%s.png" % (theme, scale)))
                camera2.camera_registration = "ok"
                camera2.camera_registration_changed.emit()
                _settle(app, 200)
                # 2 台目の接続ページ: 一番下の「このカメラ」折りたたみも開いて撮る
                from phonecam.qt.widgets import Expander as _Expander
                for expander in win.current_page().findChildren(_Expander):
                    expander.set_expanded(True, animate=False, save=False)
                _settle(app, 200)
                bar = win.current_page().verticalScrollBar()
                bar.setValue(bar.maximum())
                _settle(app, 200)
                win.grab().save(os.path.join(out, "multi_cam2_connection_bottom_%s_x%s.png" % (theme, scale)))
                for expander in win.current_page().findChildren(_Expander):
                    expander.set_expanded(False, animate=False, save=False)
        # 「変更…」の選択ダイアログ (2 台目)
        pick = DevicePickDialog(win, win.cameras, camera2)
        pick.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        pick.show()
        _settle(app, 300)
        pick.resize(pick.sizeHint())
        _settle(app, 200)
        pick.grab().save(os.path.join(out, "multi_pick_dialog_%s_x%s.png" % (theme, scale)))
        pick.close()
        pick.deleteLater()
        # 「カメラを追加」ウィザード (3 ステップ)
        wizard = AddCameraWizard(win, win.cameras)
        wizard.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        wizard.show()
        _settle(app, 300)
        wanted = next((row for row in wizard.picker._rows if row.entry.key == known3.id), None)
        if wanted is None or wanted.radio is None:
            failed.append(("wizard", "step 1: the unassigned iPhone is not selectable"))
        else:
            wanted.radio.setChecked(True)
        in_use = [row for row in wizard.picker._rows if row.radio is None]
        if len(in_use) != 2:
            failed.append(("wizard", "step 1: expected 2 in-use rows, got %d" % len(in_use)))
        wizard.resize(wizard.sizeHint())
        _settle(app, 300)
        wizard.grab().save(os.path.join(out, "multi_wizard_step1_%s_x%s.png" % (theme, scale)))
        wizard._next()   # カメラ 3 を作って、ステップ 2 へ
        _settle(app, 200)
        name = wizard.group.obs_name(wizard.camera)
        wizard.show_prepare("busy", t("multi.wizard.preparing", name=name), t("multi.wizard.admin_hint"))
        wizard.resize(wizard.sizeHint())
        _settle(app, 250)
        wizard.grab().save(os.path.join(out, "multi_wizard_step2_busy_%s_x%s.png" % (theme, scale)))
        wizard.show_prepare("ok", t("multi.wizard.prepared", name=name))
        _settle(app, 250)
        wizard.grab().save(os.path.join(out, "multi_wizard_step2_%s_x%s.png" % (theme, scale)))
        wizard.show_prepare("error", t("multi.wizard.failed", name=name), t("multi.wizard.failed_note"))
        _settle(app, 250)
        wizard.grab().save(os.path.join(out, "multi_wizard_step2_error_%s_x%s.png" % (theme, scale)))
        wizard.show_prepare("ok", t("multi.wizard.prepared", name=name))
        wizard._next()
        _settle(app, 250)
        wizard.grab().save(os.path.join(out, "multi_wizard_step3_%s_x%s.png" % (theme, scale)))
        # 3 台構成のカメラページ (「カメラを追加」のタイルは 4 つ目)
        wizard.accept()
        win.select(cams_row)
        _settle(app, 450)
        win.grab().save(os.path.join(out, "multi_cameras3_%s_x%s.png" % (theme, scale)))
        wizard.deleteLater()
        win.cameras.remove(win.cameras.cameras[2])   # 次のテーマのために 2 台に戻す
        _settle(app)
    win.select_camera(0)
    # カメラ 2 のとき: 全ページの「よく使う設定」の行に操作部品が出ているか / 録画・設定ページを撮る / 更新確認ボタンが有効か
    from PySide6.QtWidgets import QPushButton
    from phonecam.i18n import t
    from phonecam.qt.widgets import Row, Section
    for theme in THEMES:
        ctrl.set_theme(theme)
        _settle(app)
        win.select_camera(1)
        for i, (key, _icon) in enumerate(NAV_ITEMS):
            win.select(i)
            win.select_camera(1)
            _settle(app, 250)
            page = win.current_page()
            page.verticalScrollBar().setValue(0)
            _settle(app, 100)
            sections = [w for w in page.widget().findChildren(Section) if w.title_label and w.title_label.text() == t("compact.primary")]
            for section in sections:
                for row in section.findChildren(Row):
                    for control in row.controls():
                        ok = control.isVisibleTo(page) and control.width() > 0 and control.height() > 0
                        if not ok:
                            failed.append((key, "camera2 row '%s' control %s is not shown" % (row.title_label.text(), type(control).__name__)))
            if key in ("recording", "settings", "quality", "effects", "status"):
                win.grab().save(os.path.join(out, "cam2_%02d_%s_%s_x%s.png" % (i, key, theme, scale)))
            if key == "settings":
                button = next((b for b in page.findChildren(QPushButton) if b.text() == t("update.check.button")), None)
                if button is None or not button.isEnabled():
                    failed.append((key, "update check button is missing or disabled"))
                else:
                    button.click()
                    _settle(app, 200)
                    hints = [row.hint_label.text() for row in page.findChildren(Row)
                             if row.title_label.text() == t("update.check.label")]
                    if not hints or not hints[0]:
                        failed.append((key, "update check shows no status after click"))
                    if not button.isEnabled():
                        failed.append((key, "update check button stays disabled after click"))
                    win.grab().save(os.path.join(out, "cam2_%02d_%s_checked_%s_x%s.png" % (i, key, theme, scale)))
    failed += list(win.page_errors)
    failed += setup_shots(app, ctrl, win, out, scale)
    failed += language_shots(app, ctrl, win, out, scale)
    win.close()
    if failed:
        for key, err in failed:
            print("PAGE ERROR %s: %s" % (key, err), file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="qt-screens")
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--scale", default="1.0")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.child:
        return child(args.scale, args.out)
    rc = 0
    for scale in SCALES:
        env = dict(os.environ)
        env["QT_SCALE_FACTOR"] = scale
        env["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
        r = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", "--scale", scale, "--out", args.out],
                           env=env)
        rc = rc or r.returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
