# -*- coding: utf-8 -*-
"""初回セットアップ / やり直し用のウィザード (Qt 版)。

画面の構成 (上から):
    ヘッダ (固定)   段階の番号 (iPhone の手順のとき) と見出し
    本文            スクロールできる。中身が収まらなくても、下のボタン行は常に見える
    ボタン行 (固定)  あとで / 戻る / 次へ

ページ: 0 ようこそ / 1 必要なソフトの確認 / 2 インストール / 3〜9 iPhone の手順 (全7段階)。
ロジック (検出・ジョブ・状態機械) は phonecam_setup.py の SetupController / GuidedFlow をそのまま使い、
この module は画面だけを持つ。tkinter は読み込まない。

    open_setup_dialog(parent, ctrl, diagnose=False, after_update=None)   # アプリ本体から (同じプロセスのダイアログ)
    SetupWizardDialog(controller, parent, ...)                             # 単体 (--setup-wizard) / テスト
"""

import os
import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import phonecam_setup as ps
from phonecam.i18n import t
from phonecam.qt.setup_common import (
    Fold,
    NumberedLines,
    QrView,
    QtRoot,
    Rule,
    Spinner,
    StatusGlyph,
    StepDots,
    ThinBar,
    WrapLabel,
    available_geometry,
    big_button,
    centered,
    clear_layout,
    fit_to_work_area,
    link_button,
    plain_button,
)
from phonecam.qt.widgets import Card, FitBox, button

COL_MAX = 720
STATE_GLYPH = {ps.STATE_OK: "ok", ps.STATE_MISSING: "idle", ps.STATE_WARN: "warn"}
STATE_ROLE = {ps.STATE_OK: "ok", ps.STATE_MISSING: "sub", ps.STATE_WARN: "warn"}


def state_word(state):
    return {ps.STATE_OK: t("setup.wizard.icons_1"), ps.STATE_MISSING: t("setup.wizard.icons_2"),
            ps.STATE_WARN: t("setup.wizard.icons_3")}.get(state, t("setup.wizard.icons_3"))


def default_open_url(url):
    QDesktopServices.openUrl(QUrl(url))


def semibold(label, size=None):
    f = label.font()
    f.setWeight(QFont.Weight.DemiBold)
    if size is not None:
        f.setPointSizeF(float(size))
    label.setFont(f)
    return label


# ---------------------------------------------------------------------------
# 部品
# ---------------------------------------------------------------------------

class ComponentRow(QWidget):
    """必要なソフト 1 件の行: 状態アイコン | 名前 + 状態 / 用途 / 補足 | 右に チェック (導入済みは「スキップ」)。"""

    def __init__(self, comp, status, selected, locked, selectable, on_toggle, parent=None):
        super().__init__(parent)
        cid = comp["id"]
        state = (status or {}).get("state", ps.STATE_WARN)
        detail = (status or {}).get("detail", "")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)
        lay.addWidget(StatusGlyph(STATE_GLYPH.get(state, "warn")), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(comp["name"])
        semibold(name)
        head.addWidget(name)
        head.addWidget(WrapLabel(state_word(state), STATE_ROLE.get(state, "warn"), 9.0, wrap=False))
        head.addStretch(1)
        col.addLayout(head)
        col.addWidget(WrapLabel(comp["purpose"], "sub", 9.0))
        if detail:
            col.addWidget(WrapLabel(detail, "warn", 9.0))
        if cid in ps.GUIDED_IDS and state != ps.STATE_OK:
            col.addWidget(WrapLabel(t("setup.wizard.setuppanel__row_2"), "accent", 9.0))
        lay.addLayout(col, 1)
        self.checkbox = None
        if state == ps.STATE_OK:
            lay.addWidget(WrapLabel(t("setup.wizard.setuppanel__wizard_action_1"), "sub", 9.0, wrap=False), 0,
                          Qt.AlignmentFlag.AlignTop)
        elif selectable:
            cb = QCheckBox()
            cb.setAccessibleName(comp["name"])
            cb.setChecked(bool(selected))
            if locked:
                cb.setChecked(True)
                cb.setEnabled(False)
            cb.toggled.connect(lambda checked, cid=cid: on_toggle(cid, checked))
            self.checkbox = cb
            lay.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)


class ResultRow(QWidget):
    """インストール結果 1 件: ✓ / ○ (あとで) / ✗ | 名前 / 補足 / 詳細。"""

    def __init__(self, comp, result, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(12)
        ok = bool(result.get("ok"))
        skipped = bool(result.get("skipped"))
        glyph = "ok" if ok else ("skip" if skipped else "bad")
        lay.addWidget(StatusGlyph(glyph), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        name = QLabel(comp["name"])
        semibold(name)
        col.addWidget(name)
        if result.get("detail"):
            col.addWidget(WrapLabel(result["detail"], "bad" if not (ok or skipped) else "warn", 9.0))
        tech = result.get("technical") or (
            t("setup.wizard.run_installer_2", code=result["code"])
            if result.get("code") not in (None, 0) and not ok else "")
        if tech:
            col.addWidget(WrapLabel(t("setup.wizard.setupwizard__render_results_1", detail=tech), "sub", 8.0))
        lay.addLayout(col, 1)


# ---------------------------------------------------------------------------
# ウィザード
# ---------------------------------------------------------------------------

class SetupWizardDialog(QDialog):
    """初回セットアップ / やり直し用のウィザード。start="iphone" のときは iPhone の手順だけを開く。"""

    def __init__(self, controller, parent=None, on_finish=None, on_skip=None, open_url=None, launch=None,
                 start="setup", update_version=None):
        super().__init__(parent)
        self.controller = controller
        self.update_version = update_version
        self.on_finish = on_finish or (lambda: None)
        self.on_skip = on_skip or self.on_finish
        self.open_url = open_url or default_open_url
        self.launch = launch or ps._startfile
        self.mode = start
        self.page = 0
        self.auto_fit = True
        self._ended = False
        # iPhone ページの状態
        self.snap = {}
        self.app_ok = False
        self._visited = set()
        self._first = False
        self._probing = False
        self._alt_probe = False
        self._probe_result = None
        self._next_probe = 0.0
        self._dl_state = None
        self.alt_running = False
        self._confirmed = set()
        self._just_auto = False
        self._step_id = ""
        self._reset_refs()

        self.setWindowTitle(t("setup.wizard.setupwizard___init___1"))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self._build_frame()
        self._timers()
        fit_to_work_area(self, 700, 580)
        self._listener = self._on_controller
        controller.listen(self._listener)
        self.show_page(ps.PAGE_IPHONE if start == "iphone" else (ps.PAGE_INSTALL if start == "guided" else ps.PAGE_WELCOME))

    # -- 枠: ヘッダ / 本文 / ボタン行 ------------------------------------------
    def _build_frame(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        self.header_lay = QVBoxLayout(header)
        self.header_lay.setContentsMargins(32, 24, 32, 12)
        self.header_lay.setSpacing(4)
        self.header_wrap = centered(header, COL_MAX)
        root.addWidget(self.header_wrap)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)
        rule = Rule()
        root.addWidget(rule)
        foot = QWidget()
        row = QHBoxLayout(foot)
        row.setContentsMargins(32, 12, 32, 14)
        row.setSpacing(8)
        self.later_btn = self._footer_button(link_button(""))
        self.abort_btn = self._footer_button(link_button(""))
        self.extra_btn = self._footer_button(plain_button(""))
        self.back_btn = self._footer_button(plain_button(""))
        self.next_btn = self._footer_button(button("", primary=True))
        self.next_btn.setAutoDefault(False)
        row.addWidget(self.later_btn)
        row.addWidget(self.abort_btn)
        row.addStretch(1)
        row.addWidget(self.extra_btn)
        row.addWidget(self.back_btn)
        row.addWidget(self.next_btn)
        self.footer_wrap = centered(foot, COL_MAX)
        root.addWidget(self.footer_wrap)

    def _footer_button(self, btn):
        btn._cb = None
        btn.clicked.connect(lambda _checked=False, b=btn: self._dispatch(b))
        btn.hide()
        return btn

    def _dispatch(self, btn):
        cb = getattr(btn, "_cb", None)
        if cb is not None:
            cb()

    def _set_footer(self, btn, spec):
        """spec = (text, callback) か None (隠す)。"""
        if spec is None:
            btn._cb = None
            btn.hide()
            return
        text, cb = spec
        btn.setText(text)
        btn._cb = cb
        btn.show()

    def _footer(self, later=None, abort=None, extra=None, back=None, nxt=None, primary=True, enabled=True):
        self._set_footer(self.later_btn, later)
        self._set_footer(self.abort_btn, abort)
        self._set_footer(self.extra_btn, extra)
        self._set_footer(self.back_btn, back)
        self._set_footer(self.next_btn, nxt)
        self.next_btn.setEnabled(bool(enabled))
        self._style_next(primary)

    def _style_next(self, ready):
        """「次へ」をアクセント色 (既定ボタン) にするか。検出できて ✓ のときだけ強調する。"""
        try:
            self.next_btn.setDefault(bool(ready))
            self.next_btn.update()
        except RuntimeError:
            pass

    def _timers(self):
        self._pump_timer = QTimer(self)
        self._pump_timer.setSingleShot(True)
        self._pump_timer.timeout.connect(self._pump)
        self._hop_timer = QTimer(self)
        self._hop_timer.setSingleShot(True)
        self._hop_timer.timeout.connect(self._auto_hop)
        self._hop_page = None
        self._guided_timer = QTimer(self)
        self._guided_timer.setInterval(1000)
        self._guided_timer.timeout.connect(self._guided_tick)
        self._dl_timer = QTimer(self)
        self._dl_timer.setInterval(300)
        self._dl_timer.timeout.connect(self._dl_pump)

    # -- 共通 -------------------------------------------------------------
    def _reset_refs(self):
        self.panel_card = None
        self.mic_box = None
        self.stage_label = None
        self.bar = None
        self.log_edit = None
        self.detail_btn = None
        self.result_card = None
        self.manual_lay = None
        self.note_label = None
        self.audio_label = None
        self.guided_lay = None
        self.g_elapsed = None
        self.g_hint = None
        self.chip = None
        self.chip_glyph = None
        self.confirm_cb = None
        self.alt_btn = None
        self.alt_btn_next = None
        self.dl_label = None
        self.dl_btn = None
        self.log_visible = False

    def _new_body(self):
        body = FitBox()
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        col = QWidget()
        col.setMaximumWidth(COL_MAX)
        outer.addStretch(1)
        outer.addWidget(col, 1000)
        outer.addStretch(1)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(32, 4, 32, 24)
        lay.setSpacing(12)
        old = self.scroll.takeWidget()
        self.scroll.setWidget(body)
        if old is not None:
            old.deleteLater()
        self.body = body
        self.lay = lay
        return lay

    def _header(self, title, number=None, total=None):
        clear_layout(self.header_lay)
        if number is not None:
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(WrapLabel(str(number), "accent", 34.0, True, wrap=False), 0, Qt.AlignmentFlag.AlignBottom)
            row.addStretch(1)
            row.addWidget(StepDots(total, number - 1), 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(WrapLabel(t("setup.wizard.setupwizard__page_step_1", total=total), "sub", 9.0, wrap=False),
                          0, Qt.AlignmentFlag.AlignVCenter)
            self.header_lay.addLayout(row)
        self.header_lay.addWidget(WrapLabel(title, "text", 20.0, True))

    def show_page(self, index):
        self.page = index
        self._hop_timer.stop()
        self._guided_timer.stop()
        self._dl_timer.stop()
        self._reset_refs()
        self._new_body()
        if index >= ps.PAGE_IPHONE:
            self._page_step(index - ps.PAGE_IPHONE)
        else:
            (self._page_welcome, self._page_check, self._page_install)[index]()
        self.lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)
        self._queue_fit()
        if index >= ps.PAGE_IPHONE:
            self._start_pump()
        else:
            self._pump_timer.stop()

    def _queue_fit(self):
        QTimer.singleShot(0, self._fit_window)
        QTimer.singleShot(150, self._fit_window)

    def _fit_window(self):
        """中身の高さに合わせてウィンドウを高くする (縮めはしない)。作業領域からははみ出させない。
        収まらないぶんは本文のスクロールで受け、下のボタン行は常に見える。"""
        if self._ended or not self.auto_fit:
            return
        try:
            if not self.isVisible():
                return
            avail = available_geometry(self)
            frame = 48
            body_need = max(self.body.minimumHeight(), self.body.sizeHint().height())
            need = self.header_wrap.sizeHint().height() + body_need + self.footer_wrap.sizeHint().height() + 2
            max_h = avail.height() - frame - 8
            h = min(max(self.height(), need), max_h)
            if h > self.height():
                top = max(avail.y(), min(self.y(), avail.y() + avail.height() - h - frame))
                self.resize(self.width(), h)
                self.move(self.x(), top)
        except RuntimeError:
            pass

    # -- ページ 0: ようこそ ----------------------------------------------------
    def _page_welcome(self):
        self._header(t("setup.wizard.setupwizard__page_welcome_1"))
        if self.update_version:
            self.lay.addWidget(WrapLabel(t("setup.after_update", version=self.update_version), "sub", 10.0))
        self.lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__page_welcome_2"), "text", 11.0))
        # これから進む 3 つの段階 (見出しは各ページと同じ文言)
        card = Card()
        for glyph, key in (("check", "setup.wizard.setupwizard_2"), ("download", "setup.wizard.setuppanel__tab_action_2"),
                           ("phone", "setup.wizard.setupwizard_3")):
            card.layout().addWidget(_overview_row(glyph, t(key)))
        self.lay.addSpacing(6)
        self.lay.addWidget(card)
        self._footer(later=(t("setup.wizard.setuppanel__wizard_action_1"), self._skip),
                     nxt=(t("setup.wizard.setupwizard__page_welcome_3"), lambda: self.show_page(ps.PAGE_CHECK)))

    # -- ページ 1: 必要なソフトの確認 -----------------------------------------------
    def _page_check(self):
        self._header(t("setup.wizard.setupwizard_2"))
        self.panel_card = Card()
        self.lay.addWidget(self.panel_card)
        # VB-CABLE が導入済みなら選ぶ必要がないので表示しない (_sync_mic_option で出し入れする)
        self.mic_box = QWidget()
        mic = QVBoxLayout(self.mic_box)
        mic.setContentsMargins(0, 4, 0, 0)
        mic.setSpacing(2)
        self.mic_cb = QCheckBox(t("setup.wizard.setupwizard__page_check_1"))
        self.mic_cb.setChecked(bool(self.controller.mic))
        self.mic_cb.toggled.connect(self._mic_changed)
        mic.addWidget(self.mic_cb)
        hint = WrapLabel(t("setup.wizard.setupwizard__page_check_2"), "sub", 9.0)
        row = QHBoxLayout()
        row.setContentsMargins(26, 0, 0, 0)
        row.addWidget(hint)
        mic.addLayout(row)
        self.lay.addWidget(self.mic_box)
        self._footer(later=(t("setup.wizard.setuppanel__wizard_action_1"), self._skip),
                     extra=(t("setup.wizard.setupwizard__page_check_3"), self.controller.recheck),
                     nxt=(t("setup.wizard.setuppanel__tab_action_2"), self._start_install))
        self._render_panel()
        self._update_next_label()
        if not self.controller.checked:
            self.controller.recheck()

    def _render_panel(self):
        if self.panel_card is None:
            return
        lay = self.panel_card.layout()
        clear_layout(lay)
        c = self.controller
        if not c.checked:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(16, 16, 16, 16)
            row.setSpacing(10)
            spinner = Spinner()
            row.addWidget(spinner)
            row.addWidget(WrapLabel(t("setup.wizard.setuppanel_render_1"), "sub"), 1)
            lay.addWidget(box)
            spinner.start()
            return
        for comp in ps.COMPONENTS:
            cid = comp["id"]
            st = c.statuses.get(cid, {"state": ps.STATE_WARN, "detail": ""})
            state = st.get("state", ps.STATE_WARN)
            locked = comp["required"] and state == ps.STATE_MISSING
            selectable = ps.is_selectable(cid, c.statuses)
            lay.addWidget(ComponentRow(comp, st, bool(c.selection.get(cid)) or locked, locked, selectable,
                                       self._component_toggled))

    def _component_toggled(self, cid, checked):
        self.controller.set_selected(cid, checked)
        self._update_next_label()

    def _sync_mic_option(self):
        if self.mic_box is None:
            return
        installed = self.controller.statuses.get("vbcable", {}).get("state") == ps.STATE_OK
        self.mic_box.setVisible(not installed)

    def _mic_changed(self, checked):
        self.controller.set_mic(checked)
        self._render_panel()
        self._update_next_label()
        self._queue_fit()

    def _update_next_label(self):
        if self.page != ps.PAGE_CHECK:
            return
        ids = self.controller.selected_ids()
        self.next_btn.setText(t("setup.wizard.setuppanel__tab_action_2") if ids else t("setup.wizard.setupwizard__page_welcome_3"))

    def _start_install(self):
        ids = self.controller.selected_ids()
        if not ids:
            self.show_page(ps.PAGE_IPHONE)
            return
        if self.controller.start(ids):
            self.show_page(ps.PAGE_INSTALL)

    # -- ページ 2: インストール -------------------------------------------------
    def _page_install(self):
        c = self.controller
        self._header(t("setup.wizard.setuppanel__tab_action_2"))
        self.stage_label = WrapLabel(c.stage, "text", 10.5)
        self.lay.addWidget(self.stage_label)
        self.bar = ThinBar()
        self.bar.set_value(c.progress)
        self.lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.detail_btn = link_button(t("setup.wizard.setupwizard__page_install_1"), self._toggle_log)
        row.addWidget(self.detail_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFixedHeight(130)
        f = self.log_edit.font()
        f.setPointSizeF(8.5)
        self.log_edit.setFont(f)
        self.log_edit.hide()
        self.lay.addWidget(self.log_edit)
        self._refill_log()
        self.result_card = Card()
        self.result_card.hide()
        self.lay.addWidget(self.result_card)
        self.note_label = WrapLabel("", "warn", 9.5)
        self.note_label.hide()
        self.lay.addWidget(self.note_label)
        manual = QWidget()
        self.manual_lay = QVBoxLayout(manual)
        self.manual_lay.setContentsMargins(0, 0, 0, 0)
        self.manual_lay.setSpacing(0)
        self.lay.addWidget(manual)
        guided = QWidget()
        self.guided_lay = QVBoxLayout(guided)
        self.guided_lay.setContentsMargins(0, 4, 0, 0)
        self.guided_lay.setSpacing(6)
        self.lay.addWidget(guided)
        self.audio_label = WrapLabel("", "sub", 9.0)
        self.audio_label.hide()
        self.lay.addWidget(self.audio_label)
        last = t("setup.wizard.diagnosticsdialog___init___2") if self.mode == "guided" else t("setup.wizard.setupwizard__page_welcome_3")
        self._footer(nxt=(last, self._after_install), enabled=False)
        if not c.busy and c.outcome is not None:
            self._show_outcome()
        self._render_guided()
        self._show_audio_note()
        self._update_install_footer()
        self._guided_timer.start()

    def _after_install(self):
        if self.mode == "guided":
            self._end(self.on_finish)
        else:
            self.show_page(ps.PAGE_IPHONE)

    def _guided_active(self):
        g = self.controller.guided
        return g is not None and not g.finished

    def _install_complete(self):
        c = self.controller
        return (not c.busy) and (not self._guided_active()) and c.outcome is not None

    def _update_install_footer(self):
        if self.page != ps.PAGE_INSTALL:
            return
        self.next_btn.setEnabled(self._install_complete())
        self._set_footer(self.abort_btn, (t("setup.wizard.setupwizard__page_install_3"), self._abort_guided)
                         if self._guided_active() else None)

    def _abort_guided(self):
        self.controller.guided_abort()
        self._show_outcome()

    def _toggle_log(self):
        if self.log_edit is None:
            return
        self.log_visible = not self.log_visible
        self.log_edit.setVisible(self.log_visible)
        self.detail_btn.setText(t("setup.wizard.setupwizard__toggle_log_1") if self.log_visible
                                else t("setup.wizard.setupwizard__page_install_1"))
        self._queue_fit()

    def _refill_log(self):
        if self.log_edit is None:
            return
        self.log_edit.setPlainText("\n".join(self.controller.log_lines))
        bar = self.log_edit.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _retry(self):
        self.controller.recheck()
        self.show_page(ps.PAGE_CHECK)

    def _show_audio_note(self):
        """VB-CABLE を入れたあとの、音の出力先に関する案内 (戻した / 戻せなかった)。"""
        msg = getattr(self.controller, "audio_message", "")
        if self.audio_label is None or not msg:
            return
        self.audio_label.setText(msg)
        self.audio_label.show()
        self._queue_fit()

    @staticmethod
    def overall_text(out, guided_pending=False):
        return ps.overall_text(out, guided_pending)

    def _show_outcome(self):
        if self.page != ps.PAGE_INSTALL or self.stage_label is None:
            return
        out = self.controller.outcome or {}
        pending = self._guided_active() or bool(getattr(self.controller, "_pending_guided", []))
        self.stage_label.setText(self.overall_text(out, pending))
        if self.bar is not None and not pending:
            self.bar.set_value(1.0)
        self._render_results(out)
        notes = []
        if out.get("error"):
            notes.append(out["error"])
        if out.get("reboot"):
            notes.append(t("setup.wizard.setupwizard__show_outcome_1"))
        for m in out.get("manual", []):
            notes.append(t("setup.wizard.setupwizard__show_outcome_2", name=m["name"], text=m["text"]))
        self.note_label.setText("\n".join(notes))
        self.note_label.setVisible(bool(notes))
        clear_layout(self.manual_lay)
        for m in out.get("manual", []):
            row = QHBoxLayout()
            row.addWidget(link_button(t("setup.wizard.setupwizard__show_outcome_3", name=m["name"]),
                                      lambda u=m["url"]: self.open_url(u)))
            row.addStretch(1)
            self.manual_lay.addLayout(row)
        failed = [cid for cid, r in out.get("results", {}).items()
                  if not r.get("ok") and not r.get("skipped") and cid not in ps.GUIDED_IDS]
        self._set_footer(self.extra_btn, (t("setup.wizard.setupwizard__page_install_2"), self._retry)
                         if failed and not pending else None)
        self._update_install_footer()
        self._queue_fit()

    def _render_results(self, out):
        """結果の行 (インストールした項目だけ) を描画する。"""
        lay = self.result_card.layout()
        clear_layout(lay)
        ids = out.get("ids", [])
        for cid in ids:
            comp = ps.COMPONENT_BY_ID[cid]
            r = out.get("results", {}).get(cid, {"ok": False, "detail": ""})
            lay.addWidget(ResultRow(comp, r))
        self.result_card.setVisible(bool(ids))

    # -- 手順つきインストール (iTunes / iCloud / AltServer / VB-CABLE) ----------
    def _render_guided(self):
        if self.guided_lay is None:
            return
        clear_layout(self.guided_lay)
        self.g_elapsed = self.g_hint = None
        flow = self.controller.guided
        if flow is None:
            return
        c = self.controller
        if flow.finished:
            self._update_install_footer()
            return
        lay = self.guided_lay
        cid = flow.current
        g = ps.GUIDE.get(cid, {"steps": [], "note": ""})
        lay.addWidget(Rule())
        lay.addSpacing(8)
        title = WrapLabel(t("setup.wizard.setupwizard__render_guided_1", name=flow.name(cid), index=flow.index + 1,
                            total=len(flow.ids)), "text", 14.0, True)
        lay.addWidget(title)
        phase = flow.phase
        if phase in (ps.GuidedFlow.PREPARING, ps.GuidedFlow.CHECKING):
            text = (t("setup.wizard.setupwizard__render_guided_2") if phase == ps.GuidedFlow.PREPARING
                    else t("setup.wizard.setupwizard__render_guided_3"))
            row = QHBoxLayout()
            row.setSpacing(10)
            spinner = Spinner()
            row.addWidget(spinner, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(WrapLabel(text, "sub"), 1)
            lay.addLayout(row)
            spinner.start()
        else:
            if phase == ps.GuidedFlow.FAILED:
                lay.addWidget(WrapLabel(flow.message, "bad"))
                if flow.technical:
                    lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__render_results_1", detail=flow.technical), "sub", 8.0))
            else:
                lay.addWidget(NumberedLines(g["steps"], numbered=True, size=10.0))
                if g.get("note"):
                    lay.addWidget(WrapLabel(g["note"], "sub", 9.0))
                if flow.notice:
                    lay.addWidget(WrapLabel(flow.notice, "warn", 9.0))
                if phase == ps.GuidedFlow.READY and flow.message:
                    lay.addWidget(WrapLabel(flow.message, "warn"))
                if phase == ps.GuidedFlow.RUNNING:
                    self.g_elapsed = WrapLabel(self._elapsed_text(flow), "accent")
                    lay.addWidget(self.g_elapsed)
                    if flow.message:
                        lay.addWidget(WrapLabel(flow.message, "warn"))
                    if flow.detected:
                        lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__render_guided_4"), "ok"))
                    self.g_hint = WrapLabel(ps.SLOW_HINT, "warn")
                    self.g_hint.setVisible(flow.is_slow())
                    lay.addWidget(self.g_hint)
                if phase == ps.GuidedFlow.MISSING:
                    lay.addWidget(WrapLabel(flow.message, "warn"))
            row = QHBoxLayout()
            row.setSpacing(10)
            if phase == ps.GuidedFlow.READY:
                row.addWidget(big_button(t("setup.wizard.setupwizard__render_guided_5"), c.guided_open))
            elif phase in (ps.GuidedFlow.RUNNING, ps.GuidedFlow.MISSING):
                row.addWidget(big_button(t("setup.wizard.setupwizard__render_guided_6"), c.guided_confirm))
                again = plain_button(t("setup.wizard.setupwizard__render_guided_7"), c.guided_open)
                again.setMinimumHeight(40)
                row.addWidget(again)
            elif phase == ps.GuidedFlow.FAILED:
                row.addWidget(big_button(t("setup.wizard.setupwizard__page_install_2"), c.guided_retry))
            row.addStretch(1)
            lay.addSpacing(4)
            lay.addLayout(row)
            row2 = QHBoxLayout()
            row2.setSpacing(10)
            url = flow.page_url(cid)
            if url:
                row2.addWidget(link_button(t("setup.wizard.setupwizard__render_guided_8"), lambda u=url: self.open_url(u)))
            row2.addWidget(link_button(t("setup.wizard.setupwizard__render_guided_9"), c.guided_skip))
            row2.addStretch(1)
            lay.addLayout(row2)
        self._update_install_footer()
        self._queue_fit()

    @staticmethod
    def _elapsed_text(flow):
        sec = int(flow.elapsed())
        return t("setup.wizard.setupwizard__elapsed_text_1", minutes=sec // 60, seconds=sec % 60)

    def _guided_tick(self):
        if self._ended or self.page != ps.PAGE_INSTALL:
            self._guided_timer.stop()
            return
        flow = self.controller.guided
        if flow is None or flow.finished:
            return
        try:
            self.controller.guided_tick()
            if self.g_elapsed is not None and flow.phase == ps.GuidedFlow.RUNNING:
                self.g_elapsed.setText(self._elapsed_text(flow))
                if self.g_hint is not None and flow.is_slow():
                    self.g_hint.show()
        except RuntimeError:
            self.g_elapsed = self.g_hint = None
        except Exception:
            pass

    # -- ページ 3〜9: iPhone の手順 --------------------------------------------
    def _ctx(self):
        alt = bool(ps.find_altserver())
        if not alt:
            try:
                alt = self.controller.statuses.get("altserver", {}).get("state") == ps.STATE_OK
            except Exception:
                pass
        link = False
        fn = ps._host.get("link_connected")
        if fn:
            try:
                link = bool(fn())
            except Exception:
                link = False
        return {"altserver": alt, "app": self.app_ok, "link": link, "altserver_running": self.alt_running,
                "confirmed": self._step_id in self._confirmed}

    def _page_step(self, i):
        iphone = ps.iphone
        steps = iphone.IPHONE_STEPS
        step = steps[i]
        sid = step["id"]
        self._step_id = sid
        self._first = sid not in self._visited
        self._visited.add(sid)
        self._header(step["title"], number=i + 1, total=len(steps))
        for line in step["lines"]:
            self.lay.addWidget(WrapLabel(line, "text", 10.0))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.setContentsMargins(0, 8, 0, 4)
        self.chip_glyph = StatusGlyph("idle")
        self.chip = WrapLabel(t("setup.wizard.setuppanel_render_1"), "sub", 10.0)
        row.addWidget(self.chip_glyph, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.chip, 1)
        self.confirm_cb = None
        if sid in ps.MANUAL_STEPS:
            self.confirm_cb = QCheckBox(t("setup.wizard.step_confirm"))
            self.confirm_cb.setChecked(sid in self._confirmed)
            self.confirm_cb.toggled.connect(self._confirm_changed)
            row.addWidget(self.confirm_cb, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(row)
        extra = getattr(self, "_extra_" + sid, None)
        if extra:
            extra()
        last = i == len(steps) - 1
        back = None if (i == 0 and self.mode == "iphone") else (t("setup.wizard.setupwizard__page_step_3"), self._back)
        if last:
            nxt = (t("setup.wizard.setupengine__run_elevated_7"), self._finish)
        else:
            nxt = (t("setup.wizard.setupwizard__page_welcome_3"), self._next)
        self._footer(later=(t("setup.wizard.setupwizard__page_step_2"), self._skip_iphone), back=back, nxt=nxt,
                     primary=last, enabled=not last)
        self._render_chip()

    def _next(self):
        self._just_auto = False
        self.show_page(self.page + 1)

    def _confirm_changed(self, checked):
        if checked:
            self._confirmed.add(self._step_id)
        else:
            self._confirmed.discard(self._step_id)
        self._render_chip()

    def _back(self):
        self._just_auto = False
        if self.page == ps.PAGE_IPHONE:
            self.controller.recheck()
            self.show_page(ps.PAGE_CHECK)
        else:
            self.show_page(self.page - 1)

    def _btn_row(self, items):
        """ボタンを 2 つずつ並べる (幅が足りなくても切れない)。items = [(text, command, enabled)]"""
        out = []
        row = None
        for n, (text, cmd, enabled) in enumerate(items):
            if n % 2 == 0:
                row = QHBoxLayout()
                row.setSpacing(10)
                row.addStretch(1)
                self.lay.addLayout(row)
            b = plain_button(text, cmd, enabled)
            row.insertWidget(row.count() - 1, b)
            out.append(b)
        return out

    def _extra_itunes(self):
        path = ps.find_itunes()
        self._btn_row([(t("setup.wizard.setupwizard__extra_itunes_1"), lambda: self.launch(path), bool(path))])
        if not path:
            self.lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_itunes_2"), "sub", 9.0))

    def _extra_altstore(self):
        iphone = ps.iphone
        path = ps.find_altserver()
        btns = self._btn_row([
            (t("setup.wizard.setupwizard__extra_altstore_1"), lambda: self.launch(path), bool(path)),
            (t("setup.wizard.setupwizard__extra_altstore_2"), lambda: self.open_url(ps.FAQ_URL), True),
            (t("setup.wizard.diagnosticsdialog___init___1"), self._open_diagnostics, True)])
        self.alt_btn = btns[0]
        self.alt_btn_next = btns[1]
        self.lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_altstore_3"), "sub", 9.0))
        if iphone is not None:
            def req(lay):
                lay.addWidget(WrapLabel(t("setup.wizard.diagnosticsdialog_render_2"), "sub", 9.0))
                lay.addWidget(NumberedLines(iphone.ALTSTORE_REQUIREMENTS, numbered=True, size=9.0))
                lay.addWidget(WrapLabel(iphone.ALTSTORE_NEW_PC_NOTE, "sub", 9.0))

            def err(lay):
                lay.addWidget(NumberedLines(iphone.ALT_2003_CHECKLIST, numbered=True, size=9.0))
            self.lay.addWidget(Fold(t("setup.wizard.fold_requirements"), req, self._queue_fit))
            self.lay.addWidget(Fold(iphone.ALT_2003_TITLE, err, self._queue_fit))

    def _open_diagnostics(self):
        from phonecam.qt.setup_diag import DiagnosticsDialog
        dlg = DiagnosticsDialog(self, open_url=self.open_url)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.open()

    def _extra_phonecam(self):
        iphone = ps.iphone
        repo = ps.APP_REPO
        url = iphone.source_url(repo)
        deeplink = iphone.source_deeplink(repo)
        if url and ps.phonecam_qr is not None:
            box = QHBoxLayout()
            box.setSpacing(16)
            try:
                box.addWidget(QrView(ps.phonecam_qr.make_qr(deeplink, "M"), cell=4, quiet=3), 0, Qt.AlignmentFlag.AlignTop)
            except Exception:
                pass
            side = QVBoxLayout()
            side.setSpacing(6)
            side.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_phonecam_1"), "sub", 9.0))
            side.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_phonecam_2"), "sub", 9.0))
            side.addStretch(1)
            box.addLayout(side, 1)
            self.lay.addLayout(box)
            entry = QLineEdit(url)
            entry.setReadOnly(True)
            entry.setCursorPosition(0)
            entry.setMinimumHeight(30)
            self.lay.addWidget(entry)
            self._btn_row([(t("setup.wizard.setupwizard__extra_phonecam_3"), lambda: self._copy(url), True)])
        self.lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_phonecam_4"), "sub", 9.0))
        self.lay.addWidget(WrapLabel(t("setup.wizard.setupwizard__extra_phonecam_5"), "sub", 9.0))
        ipa = ps.ipa_url()
        row = QHBoxLayout()
        self.dl_btn = plain_button(t("setup.wizard.setupwizard__extra_phonecam_6"), self._download_ipa, bool(ipa))
        row.addWidget(self.dl_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.dl_label = WrapLabel("", "sub", 9.0)
        self.lay.addWidget(self.dl_label)
        if self._dl_state and self._dl_state[0] == "ok":
            self.dl_label.setText(t("setup.wizard.setupwizard__extra_phonecam_7", path=self._dl_state[1]))

    def _copy(self, text):
        try:
            QGuiApplication.clipboard().setText(text)
            if self.chip is not None:
                self.chip.setText(t("setup.wizard.setupwizard__copy_1"))
                self.chip.set_role("ok")
        except Exception:
            pass

    def _download_ipa(self):
        url = ps.ipa_url()
        if not url or (self._dl_state and self._dl_state[0] == "busy"):
            return
        self._dl_state = ("busy", "")
        if self.dl_btn is not None:
            self.dl_btn.setEnabled(False)
        if self.dl_label is not None:
            self.dl_label.setText(t("setup.wizard.setupwizard__download_ipa_1"))
            self.dl_label.set_role("sub")
        dest = os.path.join(ps.downloads_dir(), "PhoneCam.ipa")

        def work():
            try:
                ps.download_file(url, dest, "zip")
                self._dl_state = ("ok", dest)
            except Exception as e:
                self._dl_state = ("err", str(e) if isinstance(e, ps.DownloadError) else ps.friendly_net_error(e))

        threading.Thread(target=work, name="ipa-download", daemon=True).start()
        self._dl_timer.start()

    def _dl_pump(self):
        st = self._dl_state
        if self._ended or (st and st[0] == "busy"):
            if self._ended:
                self._dl_timer.stop()
            return
        self._dl_timer.stop()
        try:
            if self.dl_btn is not None:
                self.dl_btn.setEnabled(True)
            if st and st[0] == "ok":
                if self.dl_label is not None:
                    self.dl_label.setText(t("setup.wizard.setupwizard__extra_phonecam_7", path=st[1]))
                    self.dl_label.set_role("ok")
                ps._open_folder_select(st[1])
            elif st and self.dl_label is not None:
                self.dl_label.setText(st[1])
                self.dl_label.set_role("bad")
        except RuntimeError:
            pass

    # -- 状態の自動更新 (2 秒ごと) -----------------------------------------------
    def _start_pump(self):
        self._next_probe = 0.0
        if not self._pump_timer.isActive():
            self._pump_timer.start(200)

    def _pump(self):
        if self._ended or self.page < ps.PAGE_IPHONE:
            return
        iphone = ps.iphone
        try:
            if self._probe_result is not None:
                snap, app = self._probe_result
                self._probe_result = None
                self.snap, self.app_ok = snap, app
                self.alt_running = bool(self._alt_probe)
                self._next_probe = time.monotonic() + ps.POLL_MS / 1000.0
                self._apply()
            if not self._probing and time.monotonic() >= self._next_probe:
                self._probing = True
                want_app = self._step_id == "test"
                want_alt = self._step_id == "altstore"
                linked = bool(self._ctx().get("link"))

                def work():
                    try:
                        snap = iphone.query_usbmux()
                        app = False
                        if want_app and not linked:
                            app = iphone.probe_phonecam(snap)
                        if want_alt:
                            self._alt_probe = iphone.altserver_running()
                        self._probe_result = (snap, app)
                    except Exception:
                        self._probe_result = ({}, False)
                    finally:
                        self._probing = False

                threading.Thread(target=work, name="iphone-probe", daemon=True).start()
        except RuntimeError:
            pass
        except Exception:
            pass
        self._pump_timer.start(250)

    def _status_text(self, sid, state, text, ctx):
        """翻訳できる状態文 (自動検出の追加分)。それ以外は iphone モジュールの文言をそのまま使う。"""
        iphone = ps.iphone
        if sid == "altstore":
            if ctx.get("altserver_running"):
                return t("setup.wizard.alt_running")
            if state == iphone.ISSUE:
                return t("setup.wizard.alt_not_running") if ctx.get("altserver") else t("setup.wizard.alt_missing")
        if sid in ps.MANUAL_STEPS:
            if state == iphone.DONE:
                return t("setup.wizard.confirmed_done")
            if state == iphone.MANUAL:
                return t("setup.wizard.manual_check")
        return text

    def _render_chip(self):
        """検出状態の 1 行 (✓ / ⚠ / ○ と文) を更新する。戻り値: 状態。"""
        if self.chip is None or self.page < ps.PAGE_IPHONE:
            return None
        iphone = ps.iphone
        ctx = self._ctx()
        state, text = iphone.step_status(self._step_id, self.snap, ctx)
        text = self._status_text(self._step_id, state, text, ctx)
        if state == iphone.DONE:
            glyph = "ok"
            if self._step_id != "test":
                text = "%s — %s" % (text, t("setup.wizard.done_hint"))
        elif state in (iphone.ISSUE, iphone.UNKNOWN):
            glyph = "warn"
        else:
            glyph = "idle"
        try:
            self.chip.setText(text)
            self.chip.set_role("text" if state == iphone.DONE else "sub")
            self.chip_glyph.set_state(glyph)
            detected = self._step_id == "altstore" and bool(ctx.get("altserver_running"))
            if self.confirm_cb is not None:
                self.confirm_cb.setVisible(not detected)
            if self.alt_btn is not None:
                self.alt_btn.setVisible(not ctx.get("altserver_running"))
            if self._step_id == "test":
                self.next_btn.setEnabled(state == iphone.DONE)
            elif self.page < ps.PAGE_IPHONE + len(iphone.IPHONE_STEPS) - 1:
                self._style_next(state == iphone.DONE)
        except RuntimeError:
            return None
        return state

    def _apply(self):
        """新しい検出結果を画面へ。初めて開いた手順が検出済みのときだけ、1 段だけ自動で進める。
        1 回進んだら次の手順では自動では進まない (何段も飛ばさない)。"""
        state = self._render_chip()
        if (self._first and not self._just_auto and not self._hop_timer.isActive()
                and ps.iphone.should_auto_advance(self._step_id, state, set())):
            self._hop_page = self.page
            self._hop_timer.start(900)

    def _auto_hop(self):
        page, self._hop_page = self._hop_page, None
        if self._ended or self.page != page:
            return
        self.show_page(page + 1)
        self._just_auto = True

    # -- 終了 -------------------------------------------------------------
    def _finish(self):
        self._end(self.on_finish)

    def _skip(self):
        self._end(self.on_skip)

    def _skip_iphone(self):
        self._end(self.on_finish if self.mode == "setup" else self.on_skip)

    def _close(self):
        if self.controller.busy:
            return
        if self._guided_active():
            self.controller.guided_abort()
        if self.mode == "guided":
            self._end(self.on_skip)
            return
        if self.page >= ps.PAGE_IPHONE:
            self._skip_iphone()
        else:
            self._skip()

    def _end(self, cb):
        if self._ended:
            return
        self._ended = True
        self.controller.unlisten(self._listener)
        for timer in (self._pump_timer, self._hop_timer, self._guided_timer, self._dl_timer):
            timer.stop()
        QDialog.accept(self)
        try:
            cb()
        except Exception:
            pass

    def reject(self):
        """Esc: ほかの閉じ方と同じく、やめる扱い (インストール中は閉じない)。"""
        if not self._ended:
            self._close()

    def closeEvent(self, e):
        if self._ended:
            e.accept()
            return
        e.ignore()
        self._close()

    # -- コントローラーからの通知 ------------------------------------------------
    def _on_controller(self, what):
        if self._ended:
            return
        try:
            if what in ("statuses", "checking"):
                if self.page == ps.PAGE_CHECK and self.panel_card is not None:
                    self._render_panel()
                    self._sync_mic_option()
                    self._update_next_label()
                    self._queue_fit()
            elif what == "stage" and self.stage_label is not None:
                self.stage_label.setText(self.controller.stage)
            elif what == "progress" and self.bar is not None:
                self.bar.set_value(self.controller.progress)
            elif what == "log":
                self._refill_log()
            elif what == "audio" and self.page == ps.PAGE_INSTALL:
                self._show_audio_note()
            elif what == "done" and self.page == ps.PAGE_INSTALL:
                self._show_outcome()
            elif what in ("guided", "guided_done") and self.page == ps.PAGE_INSTALL:
                self._render_guided()
                if what == "guided_done" or not self._guided_active():
                    self._show_outcome()
                self._update_install_footer()
        except RuntimeError:
            pass   # 作り直した画面の部品が破棄されていた (次の通知で描き直される)


def _overview_row(glyph, title):
    """ようこそ画面の「これから進む段階」の 1 行。"""
    from phonecam.qt.widgets import Row
    return Row(glyph, title)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def make_audio_guard(get, set_):
    """VB-CABLE の導入で既定の音声デバイスが変わるのを戻す (Windows のみ)。"""
    try:
        from phonecam.core.deps import phonecam_audio
        if phonecam_audio is None or os.name != "nt":
            return None
        return phonecam_audio.RestoreGuard(get, set_)
    except Exception:
        return None


def open_setup_dialog(parent, ctrl, diagnose=False, after_update=None):
    """アプリ本体から、同じプロセスのダイアログとして開く (非同期。閉じたら ctrl.setup_dialog_closed())。
    設定は ctrl 経由で読み書きするので、閉じたあとに別プロセスの保存結果を読み直す必要はない。"""
    from phonecam.core.softcam_reg import bind_setup_host
    bind_setup_host(link_connected=lambda: bool(ctrl.state.connected))
    if diagnose:
        from phonecam.qt.setup_diag import DiagnosticsDialog
        dialog = DiagnosticsDialog(parent)
    else:
        def audio_set(key, value):
            ctrl.changed(key, value)
            ctrl.save_now()

        controller = ps.SetupController(QtRoot(), audio_guard=make_audio_guard(ctrl.get, audio_set))

        def finish():
            ctrl.changed("setup_done", True)
            ctrl.save_now()

        dialog = SetupWizardDialog(controller, parent, on_finish=finish, on_skip=finish, update_version=after_update)
        dialog.finished.connect(lambda _result: controller.close())
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog.finished.connect(lambda _result: ctrl.setup_dialog_closed())
    dialog.open()
    return dialog
