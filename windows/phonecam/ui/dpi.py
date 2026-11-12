# -*- coding: utf-8 -*-
"""高 DPI 対応 (UI_SCALE / px)
"""

import ctypes
import os
from phonecam.core.deps import phonecam_setup


# --- 画面の拡大率 (高DPI対応) -------------------------------------------------
UI_SCALE = 1.0  # DPI / 96。ピクセル指定の幅・余白は px() で拡大する


def enable_dpi_awareness():
    """Tk を作る前に呼ぶ。Windows にウィンドウを勝手に拡大 (ぼやけ・切れ) させず、実際のDPIで描画する。"""
    if os.name != "nt":
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        return True
    except Exception:
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        return False


def system_dpi(root=None):
    """実際のDPI (取れなければ 96)。"""
    if os.name == "nt":
        try:
            return int(ctypes.windll.user32.GetDpiForSystem())
        except Exception:
            pass
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
            if dpi > 0:
                return dpi
        except Exception:
            pass
    if root is not None:
        try:
            return int(round(root.winfo_fpixels("1i")))
        except Exception:
            pass
    return 96


def apply_ui_scale(root):
    """tk の scaling を実際のDPIに合わせ (フォントの大きさがそろう)、UI_SCALE を設定する。"""
    global UI_SCALE
    dpi = system_dpi(root)
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    UI_SCALE = max(1.0, min(3.0, dpi / 96.0))
    if phonecam_setup is not None:
        try:
            phonecam_setup.set_scale(UI_SCALE)
        except Exception:
            pass
    return UI_SCALE


def px(n):
    return max(1, int(round(n * UI_SCALE)))
