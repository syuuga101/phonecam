# -*- coding: utf-8 -*-
"""任意の依存ライブラリ・同梱モジュールの読み込み (無くても起動できるよう None にする)"""

try:
    import phonecam_update  # 更新の確認と自己更新
except Exception:  # pragma: no cover
    phonecam_update = None

try:
    import phonecam_audio  # VB-CABLE 導入で既定の音声デバイスが変わるのを戻す
except Exception:  # pragma: no cover
    phonecam_audio = None

try:
    import phonecam_dshowctl  # OBS の「ビデオの設定」(カメラ設定ダイアログ) との連携
except Exception:  # pragma: no cover
    phonecam_dshowctl = None

try:
    import phonecam_hotkeys  # グローバルショートカットキー (OBS やゲームを操作中でも効く)
except Exception:  # pragma: no cover
    phonecam_hotkeys = None

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None
try:
    import av
except Exception:  # pragma: no cover
    av = None
try:
    import pyvirtualcam
except Exception:  # pragma: no cover
    pyvirtualcam = None
try:
    import sounddevice as sd  # iPhoneのマイク再生用 (無くても映像は使える)
except Exception:  # pragma: no cover
    sd = None


def __getattr__(name):
    """重くて Qt 版の通常起動では要らないもの (セットアップ本体と tkinter) は、最初に参照されたときに読み込む。
    Tk 版・ウィザード・管理者ヘルパだけが `from phonecam.core.deps import phonecam_setup / tk ...` で使う。"""
    if name == "phonecam_setup":
        try:
            import phonecam_setup as value  # セットアップ (必要ソフトの検出・インストール・ウィザード)
        except Exception:  # pragma: no cover
            value = None
    elif name in ("tk", "ttk", "messagebox"):
        try:
            import tkinter
            from tkinter import messagebox, ttk
            values = {"tk": tkinter, "ttk": ttk, "messagebox": messagebox}
        except Exception:  # pragma: no cover
            values = {"tk": None, "ttk": None, "messagebox": None}
        globals().update(values)
        return values[name]
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value
