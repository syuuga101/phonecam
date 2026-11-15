# -*- coding: utf-8 -*-
"""外から来た文字列 (端末名・エラー・QR/Bonjour 由来の名前など) を HTML として描画しないラベル。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class PlainLabel(QLabel):
    """QLabel の自動形式判定 (HTML / Markdown) を使わず、常に文字としてそのまま出す。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTextFormat(Qt.TextFormat.PlainText)
