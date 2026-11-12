# -*- coding: utf-8 -*-
"""ストリーム制御 (StreamController): 自動/常時/手動、アイドル停止
"""

import threading
from phonecam.core.settings import IDLE_STOP_VALUES, RUN_MODES


class StreamController:
    """iPhone のカメラを動かすか (stream.active) を決める。判定は desired() ひとつに集約する。"""

    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.Lock()
        self.manual_started = False   # 手動モード: 開始を押した
        self.user_paused = False      # 自動/常時: 一時停止を押した (開始を押すまで保持)
        self.in_use = False
        self.last_in_use = None
        self.sent = None              # 最後にiPhoneへ送った値 (未送信は None)

    # -- 設定 ------------------------------------------------------------
    def mode(self):
        """実際に使うモード。PhoneCam 以外の出力先では自動判定できないので「常時」と同じ扱い。"""
        m = self.settings.get("run_mode", "auto")
        if m not in RUN_MODES:
            m = "auto"
        if m == "auto" and self.settings.get("backend") != "phonecam":
            return "always"
        return m

    def auto_unsupported(self):
        return self.settings.get("run_mode", "auto") == "auto" and self.settings.get("backend") != "phonecam"

    def idle_sec(self):
        v = self.settings.get("idle_stop_sec", 10)
        return v if v in IDLE_STOP_VALUES else 10

    # -- 入力 ------------------------------------------------------------
    def note_in_use(self, in_use, now):
        with self._lock:
            # 自動モードの「一時停止」は、アプリがカメラを開き直したら (未使用→使用中) 解除する。
            # 押したまま忘れて「自動で起動しない」状態にならないようにするため
            if in_use and not self.in_use and self.user_paused and self.mode() == "auto":
                self.user_paused = False
            self.in_use = bool(in_use)
            if in_use:
                self.last_in_use = now

    def mode_changed(self):
        with self._lock:
            self.user_paused = False
            self.manual_started = False

    def reset_user(self):
        """切断したとき: 押したボタンの状態を忘れる。"""
        with self._lock:
            self.user_paused = False
            self.manual_started = False
            self.last_in_use = None
            self.sent = None

    # -- 判定 ------------------------------------------------------------
    def _desired_locked(self, connected, now):
        if not connected:
            return False
        mode = self.mode()
        if mode == "manual":
            return self.manual_started
        if self.user_paused:
            return False
        if mode == "always":
            return True
        if self.in_use:
            return True
        li = self.last_in_use
        return li is not None and (now - li) < self.idle_sec()

    def desired(self, connected, now):
        with self._lock:
            return self._desired_locked(connected, now)

    def press_button(self, connected, now):
        """開始/一時停止ボタン。"""
        with self._lock:
            active = self._desired_locked(connected, now)
            if self.mode() == "manual":
                self.manual_started = not active
            elif active:
                self.user_paused = True
            else:
                self.user_paused = False
                if self.mode() == "auto":
                    self.last_in_use = now  # 押した直後は設定時間だけ動かして映像を確認できる

    def button_label(self, connected, now):
        return "一時停止" if self.desired(connected, now) else "開始"

    # -- 送信管理 ----------------------------------------------------------
    def on_connect(self, now):
        """接続直後の "all" 制御に載せる値。送信済みとして記録する。"""
        with self._lock:
            v = self._desired_locked(True, now)
            self.sent = v
            return v

    def pending(self, connected, now):
        """変化があれば送るべき値、なければ None。切断中は送信済みの記録を消す。"""
        with self._lock:
            if not connected:
                self.sent = None
                return None
            v = self._desired_locked(True, now)
            return v if v != self.sent else None

    def mark_sent(self, value):
        with self._lock:
            self.sent = value

    # -- 表示 ------------------------------------------------------------
    def state_text(self, connected, st, now):
        if not connected:
            return "-"
        with self._lock:
            want = self._desired_locked(True, now)
            manual = self.mode() == "manual"
            paused = self.user_paused
        if want:
            if st.get("cameraRunning") is False or st.get("streamActive") is False:
                return "起動中…"
            return "配信中"
        if manual:
            return "待機中（開始を押すと動きます）"
        if paused:
            if self.mode() == "auto":
                return "一時停止中（「開始」を押すか、アプリでカメラを開き直すと再開）"
            return "一時停止中（「開始」を押すと再開）"
        return "待機中（使っているアプリなし）"


def camera_state_text(connected, st):
    """iPhone の状態から「カメラ: 動作中/停止」。"""
    if not connected or "cameraRunning" not in st:
        return "-"
    return "動作中" if st.get("cameraRunning") else "停止"
