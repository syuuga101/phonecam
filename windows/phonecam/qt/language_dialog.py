# -*- coding: utf-8 -*-
"""言語を切り替えた直後に出す確認 (Windows の画面解像度の変更と同じ流れ)。

新しい言語で「この言語のままにしますか？ N 秒後に元の言語（日本語）に戻ります」と出し、
「保存」で確定、「元に戻す」・閉じる・時間切れ (10 秒) で元の言語に戻す。
exec() の結果は、保存なら Accepted、それ以外は Rejected。
"""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from phonecam.i18n import t
from phonecam.qt.widgets import Hint, button

REVERT_SECONDS = 10


def language_name(code):
    """言語の名前 (今の言語で)。知らない言語は記号のまま。"""
    name = t("settings.language.name_" + str(code))
    return str(code) if name.startswith("settings.language.") else name


class LanguageConfirmDialog(QDialog):
    def __init__(self, parent, old_language, seconds=REVERT_SECONDS, autostart=True):
        super().__init__(parent)
        self.old_language = old_language
        self.remaining = int(seconds)
        self.setWindowTitle("PhoneCam")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(6)
        self.title_label = QLabel(t("settings.language.confirm"))
        font = self.title_label.font()
        font.setPointSizeF(13.0)
        font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(font)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.countdown_label = Hint("")
        layout.addWidget(self.countdown_label)
        layout.addSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.revert_button = button(t("settings.language.revert"))
        self.keep_button = button(t("settings.language.keep"), primary=True)
        row.addWidget(self.revert_button)
        row.addWidget(self.keep_button)
        layout.addLayout(row)
        self.keep_button.clicked.connect(self.accept)
        self.revert_button.clicked.connect(self.reject)
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self.tick)
        self._show_remaining()
        if autostart:
            self._timer.start()

    def _show_remaining(self):
        self.countdown_label.setText(t("settings.language.countdown", n=self.remaining,
                                       old=language_name(self.old_language)))

    def tick(self):
        """1 秒たった。0 になったら元に戻す (時間切れ)。"""
        self.remaining = max(0, self.remaining - 1)
        self._show_remaining()
        if self.remaining <= 0:
            self.reject()

    def done(self, result):
        self._timer.stop()
        super().done(result)
