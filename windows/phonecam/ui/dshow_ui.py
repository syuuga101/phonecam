# -*- coding: utf-8 -*-
"""OBS「ビデオの設定」(DirectShow プロパティ) との連携の UI 側
"""

import sys
import time
from phonecam.core.deps import phonecam_dshowctl
from phonecam.ui.labels import MODE3_LABELS, SHUTTER_VALUES


def _dshow_init(app):
    """起動時: 共有メモリを開き、変更を取り込むループを始める。使えなくてもアプリは普通に動く。"""
    app.dshowctl = None
    app._dshow_reason = ""
    if phonecam_dshowctl is None:
        app._dshow_reason = "モジュールがありません"
    else:
        try:
            app.dshowctl = phonecam_dshowctl.ControlBridge(shutter_snap=SHUTTER_VALUES)
            if not app.dshowctl.open():
                app._dshow_reason = ("Windowsのみ対応" if not sys.platform.startswith("win")
                                      else "PhoneCamカメラの準備ができていません")
        except Exception:
            app.dshowctl = None
            app._dshow_reason = "初期化に失敗しました"
    app._dshow_show_state()
    app._dshow_retry_t = time.monotonic() + app.DSHOW_RETRY_SEC
    app.root.after(app.DSHOW_POLL_MS, app._dshow_tick)


def _dshow_show_state(app):
    try:
        ok = app.dshowctl is not None and app.dshowctl.is_open
        app.dshow_var.set("有効" if ok else "無効（%s）" % (app._dshow_reason or "未接続"))
    except Exception:
        pass


def _dshow_tick(app):
    if app._closing:
        return
    try:
        app._dshow_step()
    except Exception:
        pass  # 連携の失敗でアプリ本体を止めない
    try:
        app.root.after(app.DSHOW_POLL_MS, app._dshow_tick)
    except Exception:
        pass


def _dshow_step(app):
    d = app.dshowctl
    if d is None:
        return
    if not d.is_open:
        now = time.monotonic()
        if now < app._dshow_retry_t:
            return
        app._dshow_retry_t = now + app.DSHOW_RETRY_SEC
        if d.open():
            app._dshow_reason = ""
        app._dshow_show_state()
        if not d.is_open:
            return
    try:
        changes = d.poll_changes()
        for c in changes:
            try:
                app._changed(c["key"], c["value"], c.get("group"))
            except Exception:
                pass
        if changes:
            app._dshow_sync_ui([c["key"] for c in changes])
    finally:
        snap = app.state.snapshot()
        st = snap["phone_status"] if snap.get("connected") else {}
        d.publish(st or {}, app.settings)  # 変更を取り込んだ後は必ず現在値を書き戻す
    app._dshow_show_state()


def _dshow_sync_ui(app, keys):
    """設定の値に合わせてスライダー・コンボボックスを更新する (保存・送信はしない)。"""
    app._ui_sync = True
    try:
        for k in keys:
            try:
                v = app.settings[k]
                if k in app._slider_vars:
                    app._slider_vars[k].set(float(v))
                elif k in app._mode_vars:
                    app._mode_vars[k].set(app._value_to_label(MODE3_LABELS, v))
                elif k == "shutter":
                    app.shutter_var.set("1/%d" % int(v))
            except Exception:
                pass
        try:
            app._update_enabled()
        except Exception:
            pass
    finally:
        app._ui_sync = False


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_dshow_init',
    '_dshow_show_state',
    '_dshow_tick',
    '_dshow_step',
    '_dshow_sync_ui',

)
