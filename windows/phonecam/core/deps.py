# -*- coding: utf-8 -*-
"""任意の依存ライブラリ・同梱モジュールの読み込み (無くても起動できるよう None にする)"""

try:
    import phonecam_setup  # セットアップ (必要ソフトの検出・インストール・ウィザード)
except Exception:  # pragma: no cover
    phonecam_setup = None

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

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception:  # pragma: no cover
    tk = None
    ttk = None
    messagebox = None
