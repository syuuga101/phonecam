# -*- coding: utf-8 -*-
"""バージョン情報 (ビルド時に生成される phonecam_version.py から読む)"""

try:
    from phonecam_version import VERSION as APP_VERSION  # ビルド時に生成される
except Exception:
    APP_VERSION = "dev"
try:
    from phonecam_version import REPO as APP_REPO  # ビルド時に生成される ("owner/name")
except Exception:
    APP_REPO = ""
