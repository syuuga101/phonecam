# -*- coding: utf-8 -*-
"""「SideStoreの導入チェック」(Qt 版)。チェックごとに ✓ / ✗ / ⚠ と、直し方を 1 行で出す。チェックは別スレッドで実行する。

    DiagnosticsDialog(parent, runner=None, open_url=None)   # runner(progress=fn) -> [診断結果]
    --setup-diagnose と、設定の「SideStoreの導入チェック」、ウィザードの iloader の手順から開く。
"""

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

import phonecam_setup as ps
from phonecam.i18n import t
from phonecam.qt.setup_common import (
    NumberedLines,
    Rule,
    Spinner,
    StatusGlyph,
    WrapLabel,
    centered,
    fit_to_work_area,
    heading,
    link_button,
    plain_button,
)
from phonecam.qt.widgets import Card, FitBox, button

try:
    import phonecam_iphone as iphone
except Exception:  # pragma: no cover
    iphone = None

COL_MAX = 720
DIAG_GLYPH = {ps.DIAG_OK: "ok", ps.DIAG_NG: "bad", ps.DIAG_WARN: "warn"}
DIAG_ROLE = {ps.DIAG_OK: "ok", ps.DIAG_NG: "bad", ps.DIAG_WARN: "warn"}


def section_title(text):
    label = heading(text, 11.0)
    label.setWordWrap(True)
    return label


def add_text_section(lay, title, lines, numbered=False, intro=""):
    """見出し + (導入文) + 番号つき / 箇条書きの文章ブロック。title が空なら見出しなし。"""
    if title:
        lay.addSpacing(8)
        lay.addWidget(section_title(title))
    if intro:
        lay.addWidget(WrapLabel(intro, "sub", 9.0))
    lay.addWidget(NumberedLines(lines, numbered=numbered, size=9.0))


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None, runner=None, open_url=None, autorun=True):
        super().__init__(parent)
        self.runner = runner or ps.run_diagnostics
        self.open_url = open_url or _default_open_url
        self.results = None
        self.running = False
        self.progress_text = ""
        self._box = []
        self._ended = False
        self.status_label = None
        self.setWindowTitle(t("setup.wizard.diagnosticsdialog___init___1"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # ヘッダ (固定)
        head = QWidget()
        head_lay = QVBoxLayout(head)
        head_lay.setContentsMargins(32, 24, 32, 10)
        self.title = heading(t("setup.wizard.diagnosticsdialog___init___1"), 20.0)
        head_lay.addWidget(self.title)
        root.addWidget(centered(head, COL_MAX))
        # 本文 (スクロール)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)
        # ボタン行 (固定)
        root.addWidget(Rule())
        foot = QWidget()
        foot_lay = QHBoxLayout(foot)
        foot_lay.setContentsMargins(32, 12, 32, 14)
        foot_lay.setSpacing(8)
        foot_lay.addStretch(1)
        self.run_btn = button(t("setup.wizard.diagnosticsdialog___init___3"), primary=True)
        self.run_btn.setAutoDefault(False)
        self.run_btn.clicked.connect(self.run)
        self.close_btn = plain_button(t("setup.wizard.diagnosticsdialog___init___2"), self.close)
        foot_lay.addWidget(self.run_btn)
        foot_lay.addWidget(self.close_btn)
        root.addWidget(centered(foot, COL_MAX))
        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._poll)
        fit_to_work_area(self, 680, 760)
        self.render()
        if autorun:
            self.run()

    # -- 実行 -------------------------------------------------------------
    def run(self):
        if self.running or self._ended:
            return
        self.running = True
        self.results = None
        self.progress_text = t("setup.wizard.diagnosticsdialog_run_1")
        self._box = []
        self.run_btn.setEnabled(False)
        self.render()

        def prog(msg):
            if msg:
                self.progress_text = msg

        def work():
            try:
                self._box.append(self.runner(progress=prog))
            except Exception as e:
                self._box.append([ps._diag("error", t("setup.wizard.run_work_1"), ps.DIAG_WARN,
                                           t("setup.wizard.run_work_2", detail=e), t("setup.wizard.run_work_3"))])

        threading.Thread(target=work, name="sidestore-check", daemon=True).start()
        self._timer.start()

    def _poll(self):
        if self._ended:
            self._timer.stop()
            return
        if self._box:
            self._timer.stop()
            self.results = self._box[0]
            self.running = False
            self.run_btn.setEnabled(True)
            self.render()
            return
        if self.status_label is not None:
            try:
                self.status_label.setText(self.progress_text)
            except RuntimeError:
                self.status_label = None

    # -- 描画 -------------------------------------------------------------
    def render(self):
        body = FitBox()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(32, 4, 32, 24)
        body_lay.setSpacing(10)
        self.status_label = None
        if self.running or self.results is None:
            row = QHBoxLayout()
            row.setSpacing(10)
            spinner = Spinner()
            row.addWidget(spinner, 0, Qt.AlignmentFlag.AlignTop)
            self.status_label = WrapLabel(self.progress_text or t("setup.wizard.diagnosticsdialog_run_1"), "sub")
            row.addWidget(self.status_label, 1)
            body_lay.addLayout(row)
            spinner.start()
        else:
            body_lay.addWidget(WrapLabel(ps.diagnostics_summary(self.results), "text", 10.0))
            card = Card()
            for r in self.results:
                card.layout().addWidget(self._result_row(r))
            body_lay.addWidget(card)
        if iphone is not None:
            # SideStore を入れる条件と、ペアリングファイルが切れたときの入れ直し
            add_text_section(body_lay, t(iphone.PREREQ_TITLE), [t(k) for k in iphone.PREREQ_LINES],
                             numbered=True, intro=t(iphone.PREREQ_INTRO))
            body_lay.addWidget(WrapLabel(t(iphone.PREREQ_NOTE), "sub", 9.0))
            add_text_section(body_lay, t(iphone.PAIRING_TITLE), [t(k) for k in iphone.PAIRING_LINES[1:]],
                             numbered=True, intro=t(iphone.PAIRING_LINES[0]))
        link_row = QHBoxLayout()
        link_row.addWidget(link_button(t("setup.wizard.diagnosticsdialog_render_3"), lambda: self.open_url(ps.SIDESTORE_DOCS_URL)))
        link_row.addWidget(link_button(t("setup.wizard.iloader_page"), lambda: self.open_url(ps.ILOADER_PAGE_URL)))
        link_row.addStretch(1)
        body_lay.addLayout(link_row)
        body_lay.addStretch(1)
        wrapper = centered(body, COL_MAX)
        old = self.scroll.takeWidget()
        self.scroll.setWidget(wrapper)
        if old is not None:
            old.deleteLater()

    @staticmethod
    def _result_row(r):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        row.addWidget(StatusGlyph(DIAG_GLYPH.get(r["state"], "warn")), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        label = QLabel(r["label"])
        f = label.font()
        f.setWeight(QFont.Weight.DemiBold)
        label.setFont(f)
        label.setWordWrap(True)
        col.addWidget(label)
        if r.get("detail"):
            col.addWidget(WrapLabel(r["detail"], DIAG_ROLE.get(r["state"], "warn"), 9.0))
        if r.get("fix") and r["state"] != ps.DIAG_OK:
            col.addWidget(WrapLabel("→ " + r["fix"], "text", 9.0))
        row.addLayout(col, 1)
        return box

    # -- 終了 -------------------------------------------------------------
    def closeEvent(self, e):
        self._ended = True
        self._timer.stop()
        super().closeEvent(e)


def _default_open_url(url):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(url))
