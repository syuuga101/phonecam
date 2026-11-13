# -*- coding: utf-8 -*-
"""「カメラ」ページ: 各カメラのプレビューを 1 画面に並べる (カメラが 2 台以上のときだけナビに出る)。"""

from phonecam.qt.multicam import CamerasPage


def build(ctrl):
    return CamerasPage(ctrl)
