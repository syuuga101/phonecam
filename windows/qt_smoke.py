# -*- coding: utf-8 -*-
"""Qt UI のスクリーンショット・スモークテスト (CI 用)。

    python windows/qt_smoke.py [--out qt-screens]

接続中のサンプル値 (iPhone 13 / USB / 1080p60 / 31ms / 正常 / 82%) を入れた demo コントローラで
メインウィンドウ (論理 1280x800) を作り、ナビの各ページ x {ライト, ダーク} x {倍率 1.0, 2.0} を
window.grab() で PNG にする。倍率は QT_SCALE_FACTOR を変えた子プロセスで切り替える。
各ページについて、スクロール位置を中ほどにした画像 (*_scroll.png) も撮る。
ページの build が失敗していたら終了コード 1 (画像は撮れた分だけ残す)。
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


def child(scale, out):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from phonecam.i18n import set_language
    set_language("ja")   # ui/labels.py は import 時に t() を評価するので、Controller を読む前に言語を決める
    from phonecam.controller import Controller
    from phonecam.qt.app import configure
    from phonecam.qt.main_window import NAV_ITEMS, MainWindow
    from phonecam.qt.widgets import Expander

    ctrl = Controller.demo_connected("ja")
    # Wi-Fi の一覧用: 登録済み (接続中) 1台 + 未登録 1台 (テザリング経由)
    from phonecam.core.wireless import Device
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
    for theme in THEMES:
        ctrl.set_theme(theme)   # アプリと同じ経路: settings_changed -> apply_theme (ウィジェット構築後の実行時切替)
        _settle(app)
        print("scale=%s theme=%s window_lightness=%d" % (scale, theme, app.palette().window().color().lightness()))
        for i, (key, _icon) in enumerate(NAV_ITEMS):
            win.resize(W, H)
            win.select(i)
            _settle(app)
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
    # カメラが 2 台以上のとき (カメラ切り替え・出力先の欄が出る状態): 接続・映像ページをカメラ 1 / 2 で撮る
    win.resize(W, H)
    win._add_camera()
    _settle(app)
    for theme in THEMES:
        ctrl.set_theme(theme)
        _settle(app)
        for cam in (0, 1):
            win._select_camera(cam)
            for i, (key, _icon) in enumerate(NAV_ITEMS):
                if key not in ("connection", "video", "audio"):
                    continue
                win.select(i)
                win._select_camera(cam)
                _settle(app)
                win.current_page().verticalScrollBar().setValue(0)
                _settle(app, 150)
                win.grab().save(os.path.join(out, "multi_cam%d_%02d_%s_%s_x%s.png" % (cam + 1, i, key, theme, scale)))
    failed += list(win.page_errors)
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
