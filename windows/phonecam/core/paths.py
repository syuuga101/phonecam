# -*- coding: utf-8 -*-
"""実行場所の判定とフォルダ (exe 化 / ソース実行 / %APPDATA%)
"""

import os
import sys


# PhoneCam の windows/ フォルダ (このファイルは windows/phonecam/core/paths.py)
WINDOWS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _app_dir():
    """ソース実行時はスクリプトのあるフォルダ。exe 化されているときは exe と同じ場所。"""
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return WINDOWS_DIR


def _appdata_dir():
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "PhoneCam")
