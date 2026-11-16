# -*- coding: utf-8 -*-
"""プレビュードック: 映像・画質・エフェクト・音声のページで、設定を変えながら映像を見るための固定プレビュー。

- 幅が十分あるとき (WIDE_W 以上) はページの右に固定幅の列、狭いときはページ上部の固定の帯。
- 映像はカメラページのプレビューと同じ発生源 (ctrl.preview_frame()) から受け取るだけ。受信・デコードは増やさない。
- 見えていないとき (ドック非表示・別ページ・最小化) は描かない。タイマーも止める。
- 音声ページでは、プレビューの下に入力レベルとミュート状態を出す。
"""

import time

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from phonecam.core.profiling import PROFILE
from phonecam.core.settings import PREVIEW_FPS_CHOICES
from phonecam.i18n import t
from phonecam.labels import LENS_LABELS, METER_MIN_DB, mic_state_text
from phonecam.qt.compact import fps_summary, join_summary
from phonecam.qt.pages.audio import InputLevelMeter
from phonecam.qt.pages.connection import frame_to_image
from phonecam.qt.widgets import (
    _THEME_EVENTS, Switch, border_color, card_color, ref_palette, secondary_color,
)
from phonecam.qt.plain import PlainLabel

DOCK_PAGES = ("video", "quality", "effects", "audio")   # ドックを出すページ
WIDE_W = 1100        # ウィンドウ幅 (論理px) がこれ以上なら右の列、未満なら上の帯
WIDE_PREVIEW_W = 380
BAND_H = 200         # 狭いときの帯の高さ
PAD = 8


def dock_text(camera):
    """映像の左下に出す現在値: 「広角 · 1080p · 60fps」。"""
    lens = dict(LENS_LABELS).get(camera.get("lens"), "")
    res = camera.get("resolution")
    fps = camera.get("fps")
    return join_summary((lens, res, fps_summary(fps)))


class PreviewSurface(QWidget):
    """16:9 の映像面。描くのは active のときだけ。"""

    def __init__(self):
        super().__init__()
        self._pix = None
        self._seq = -1
        self._label = ""
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_label(self, text):
        if text != self._label:
            self._label = text
            self.update()

    def reset(self):
        self._pix = None
        self._seq = -1
        self.update()

    def feed(self, camera):
        """最新フレームを今の大きさの QPixmap にする (変換だけ。デコードはしない)。"""
        item = camera.preview_frame()
        if item is None:
            if self._pix is not None:
                self._pix = None
                self.update()
            self._seq = -1
            return
        img, fmt, seq = item
        if seq == self._seq:
            return
        dpr = self.devicePixelRatioF()
        pw, ph = max(2, int((self.width() - 2) * dpr)), max(2, int((self.height() - 2) * dpr))
        started = time.perf_counter() if PROFILE is not None else 0.0
        qi = frame_to_image(img, fmt, pw, ph)
        if qi is None:
            return
        if qi.width() <= pw and qi.height() <= ph:
            qi = qi.copy()
        else:
            qi = qi.scaled(QSize(pw, ph), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pm = QPixmap.fromImage(qi)
        pm.setDevicePixelRatio(dpr)
        self._pix = pm
        self._seq = seq
        if PROFILE is not None:
            PROFILE.add(camera.link.vcam.slot, "preview", (time.perf_counter() - started) * 1000)
        self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in _THEME_EVENTS:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = ref_palette(self)
        w, h = self.width(), self.height()
        p.setPen(QPen(border_color(pal), 1))
        p.setBrush(card_color(pal))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 5, 5)
        if self._pix is not None:
            lw = self._pix.width() / self._pix.devicePixelRatio()
            lh = self._pix.height() / self._pix.devicePixelRatio()
            p.drawPixmap(int((w - lw) / 2), int((h - lh) / 2), self._pix)
        else:
            p.setPen(secondary_color(pal))
            p.drawText(QRect(0, 0, w, h), int(Qt.AlignmentFlag.AlignCenter), t("connection.preview.empty"))
        if self._label and self._pix is not None:
            font = QFont(self.font())
            font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
            p.setFont(font)
            fm = p.fontMetrics()
            tw = min(fm.horizontalAdvance(self._label), w - 24)
            pill = QRectF(8, h - fm.height() - 14, tw + 16, fm.height() + 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(pill, 4, 4)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(pill.toRect(), int(Qt.AlignmentFlag.AlignCenter),
                       fm.elidedText(self._label, Qt.TextElideMode.ElideRight, int(tw)))
        p.end()


class PreviewDock(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.camera = None
        self.wide = False
        self.audio = False
        self.active = False            # 出す状態 (ページが対象で、トグルが入っているとき)
        self._lay = QVBoxLayout(self)
        self._lay.setSpacing(6)
        self.surface = PreviewSurface()
        self._lay.addWidget(self.surface, 0, Qt.AlignmentFlag.AlignHCenter)
        self.audio_row = QWidget()
        arow = QHBoxLayout(self.audio_row)
        arow.setContentsMargins(0, 0, 0, 0)
        arow.setSpacing(10)
        self.level = InputLevelMeter()
        self.level.setMinimumWidth(100)
        self.level.setAccessibleName(t("audio.level.label"))
        self.level.setFixedHeight(20)
        self.mic_label = PlainLabel()
        self.mic_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        arow.addWidget(self.level, 1)
        arow.addWidget(self.mic_label, 0)
        self._lay.addWidget(self.audio_row, 0, Qt.AlignmentFlag.AlignHCenter)
        self.audio_row.hide()
        self.setAccessibleName(t("dock.toggle"))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(50)
        self._level_timer.timeout.connect(self._level_tick)
        self._apply_fps(ctrl.get("preview_fps", 30))
        ctrl.settings_changed.connect(self._setting)
        self._relayout()

    # -- 設定 --------------------------------------------------------------------------
    def _setting(self, key, value):
        if key == "preview_fps":
            self._apply_fps(value)

    def _apply_fps(self, fps):
        try:
            fps = int(fps)
        except (TypeError, ValueError, OverflowError):
            fps = 30
        if fps not in PREVIEW_FPS_CHOICES:
            fps = 30
        self._timer.setInterval(max(1, int(round(1000.0 / fps))))

    # -- 状態 --------------------------------------------------------------------------
    def set_camera(self, camera):
        if camera is not self.camera:
            self.camera = camera
            self.surface.reset()
            self.level.set_db(METER_MIN_DB)
        self._refresh_info()

    def set_audio(self, on):
        on = bool(on)
        if on != self.audio:
            self.audio = on
            self.audio_row.setVisible(on and self.active)
            self._relayout()
        self._sync_timers()

    def set_wide(self, wide):
        wide = bool(wide)
        if wide != self.wide:
            self.wide = wide
            self._relayout()

    def set_active(self, on):
        self.active = bool(on)
        self.audio_row.setVisible(self.audio and self.active)
        self._sync_timers()

    # -- 大きさ ------------------------------------------------------------------------
    def _relayout(self):
        if self.wide:
            self._lay.setContentsMargins(PAD, 16, 16, 16)
            pw = WIDE_PREVIEW_W
            ph = pw * 9 // 16
            self.setFixedWidth(pw + PAD + 16)
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        else:
            self._lay.setContentsMargins(PAD, PAD, PAD, PAD)
            extra = (20 + 6) if self.audio else 0
            ph = BAND_H - 2 * PAD - extra
            pw = ph * 16 // 9
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setFixedHeight(BAND_H)
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.surface.setFixedSize(pw, ph)
        self.audio_row.setFixedWidth(pw)
        self.surface.reset()

    # -- 描画の周期 ------------------------------------------------------------------------
    def _should_run(self):
        win = self.window()
        return (self.active and self.camera is not None and self.isVisible()
                and not (win is not None and win.isMinimized()))

    def _sync_timers(self):
        if self._should_run():
            if not self._timer.isActive():
                self._timer.start()
            if self.audio:
                if not self._level_timer.isActive():
                    self._level_timer.start()
            else:
                self._level_timer.stop()
        else:
            self._timer.stop()
            self._level_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_timers()

    def hideEvent(self, event):
        self._timer.stop()
        self._level_timer.stop()
        super().hideEvent(event)

    def _refresh_info(self):
        if self.camera is None:
            return
        self.surface.set_label(dock_text(self.camera))
        status = getattr(self.camera, "status", None)
        phone_muted = bool(getattr(status, "phone_muted", False)) if status else False
        text = mic_state_text(self.camera.get("mic_enabled", True), self.camera.get("mic_mute"), phone_muted)
        if text != self.mic_label.text():
            self.mic_label.setText(text)

    def _tick(self):
        if not self._should_run():
            self._sync_timers()
            return
        self._refresh_info()
        self.surface.feed(self.camera)

    def _level_tick(self):
        if not self._should_run():
            self._sync_timers()
            return
        self.level.set_db(self.camera.meter_db())


def make_toggle(ctrl):
    """ページ見出しの右に置く「プレビュー」のトグル (ラベル + スイッチ)。"""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    label = PlainLabel(t("dock.toggle"))
    switch = Switch()
    switch.setChecked(bool(ctrl.get("preview_dock", True)))
    switch.setAccessibleName(t("dock.toggle"))
    switch.setToolTip(t("dock.toggle_tip"))
    label.setToolTip(t("dock.toggle_tip"))
    lay.addWidget(label)
    lay.addWidget(switch)
    box.label = label
    box.switch = switch
    return box
