# -*- coding: utf-8 -*-
"""iPhone 側の状態を UI へ反映 (_apply_phone_edit / _update_v13 / _apply_ranges)
"""

from phonecam.i18n import t
from phonecam.ui import theme
from phonecam.core.settings import AUDIO_AVAILABLE, LENSES, SCREEN_AUTO_VALUES
from phonecam.core.stats import format_bytes, format_mmss
from phonecam.ui.labels import (
    AUDIO_PATH_LABELS,
    LENS_LABELS,
    MIC_PATH_LABELS,
    SCREEN_AUTO_LABELS,
    STATUS_TAB_INDEX,
)


def _apply_phone_edit(app, st, now=None):
    """status の phoneEdit を取り込む。seq が前回より増えたときだけ lens / micMuted を設定とUIに反映する
    (iPhone へは送り返さない)。最初に見た seq は基準にするだけ。st=None (未接続) で基準をリセット。
    戻り値: 状態欄に出す通知文 (無ければ None)。"""
    if not st:
        app._edit_seq = None
        return None
    pe = st.get("phoneEdit")
    if not isinstance(pe, dict):
        return None
    seq = pe.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return None
    last = getattr(app, "_edit_seq", None)
    app._edit_seq = seq
    if last is None or seq <= last:
        return None
    notes = []
    lens = pe.get("lens")
    if lens in LENSES and lens != app.settings.get("lens"):
        app._lens_changed(lens, send=False)
        notes.append(t("shell.sync.lens", name=dict(LENS_LABELS)[lens]))
    muted = pe.get("micMuted")
    if isinstance(muted, bool) and muted != bool(app.settings.get("mic_muted", False)):
        app.mic_muted_var.set(muted)
        app._changed("mic_muted", muted, None)
        notes.append(t("shell.sync.muted") if muted else t("shell.sync.unmuted"))
    return " / ".join(notes) if notes else None


def _update_v13(app, st, connected, snap, now):
    """省電力・録画・遅延・音声の表示と、温度履歴の記録 (1秒ごと)。"""
    # 温度履歴 (1秒ごとに記録、グラフは1秒に1回まで描き直す)
    if now - app._last_sample >= 1.0:
        app._last_sample = now
        app.thermal_log.sample(now, connected, st.get("thermalState"), snap["recv_fps"])
        app._graph_dirty = True
        app._housekeeping()
    hot = app.thermal_log.hot_seconds
    app.st_vars["hot"].set(format_mmss(hot) if connected else "-")
    if app._graph_dirty and now - getattr(app, "_graph_drawn", 0.0) >= 1.0:
        try:
            if app.nb.index("current") == STATUS_TAB_INDEX:
                app._graph_dirty = False
                app._graph_drawn = now
                app._draw_graph()
        except Exception:
            pass

    # 遅延
    avg = app.link.latency.averages() if connected else {}
    for key, var in app.lat_vars.items():
        v = avg.get(key)
        var.set("%.0f ms" % v if isinstance(v, (int, float)) else "-")

    # 省電力
    if connected and "screenBlack" in st:
        black = bool(st.get("screenBlack"))
        if now - app._screen_user_t > 3.0 and black != app.settings["screen_black"]:
            app.settings["screen_black"] = black
            app.screen_black_var.set(black)
            app._schedule_save()
        auto = st.get("autoBlackSeconds")
        state = t("shell.sync.screen_black") if black else t("shell.sync.screen_normal")
        if auto in SCREEN_AUTO_VALUES:
            app.screen_state_var.set(t("shell.sync.screen_with_auto", state=state, auto=app._value_to_label(SCREEN_AUTO_LABELS, auto)))
        else:
            app.screen_state_var.set(t("shell.sync.screen", state=state))
    else:
        app.screen_state_var.set("")

    # 録画
    rec = st.get("recording")
    rec = rec if isinstance(rec, dict) else {}
    active = bool(rec.get("active"))
    if connected:
        if now >= app._rec_lock_until:
            app.rec_btn.state(["!disabled"])
        app.rec_btn.config(text=t("recording.button.stop") if active else t("recording.button.start"))
    else:
        app.rec_btn.state(["disabled"])
        app.rec_btn.config(text=t("recording.button.start"))
    if connected and rec:
        app.rec_vars["state"].set(t("shell.sync.rec_active", time=format_mmss(rec.get("seconds", 0))) if active else t("shell.sync.rec_stopped"))
        app.rec_labels["state"].config(foreground=theme.colors()["live"] if active else theme.colors()["text"])
        app.rec_vars["file"].set(rec.get("fileName") or "-")
        size = rec.get("bytes")
        app.rec_vars["size"].set(format_bytes(size) if (active or size) else "-")
        free = rec.get("freeBytes")
        app.rec_vars["free"].set(format_bytes(free) if free else "-")
        app.rec_error_var.set(rec.get("error") or "")
    else:
        app.rec_vars["state"].set("-")
        for k in ("file", "size", "free"):
            app.rec_vars[k].set("-")
        app.rec_labels["state"].config(foreground=theme.colors()["text"])
        app.rec_error_var.set("")

    # 音声の状態表示
    if AUDIO_AVAILABLE and app.settings["mic_enabled"]:
        mic = st.get("mic") if isinstance(st.get("mic"), dict) else {}
        if app.audio.error:
            app.audio_err_var.set(app.audio.error)
            app.audio_info_var.set("")
        elif connected and mic and mic.get("available") is False:
            app.audio_err_var.set(t("shell.sync.mic_unavailable"))
            app.audio_info_var.set("")
        else:
            app.audio_err_var.set("")
            info = t("shell.sync.output", name=app.audio.device_name) if app.audio.running else ""
            if app.audio.running and connected and mic.get("sampleRate"):
                info = t("shell.sync.output_detail", name=app.audio.device_name, hz=int(mic["sampleRate"]),
                         buf=round(app.audio.buffered_ms()), target=round(app.audio.target_ms()))
            app.audio_info_var.set(info)
    elif AUDIO_AVAILABLE:
        app.audio_err_var.set("")
        app.audio_info_var.set("")
    # 音声経路と遅延
    path = snap.get("audio_path")
    app.audio_path_var.set(AUDIO_PATH_LABELS.get(path, "-") if connected else "-")
    lat = app.audio.latency_ms() if (AUDIO_AVAILABLE and connected and app.audio.running) else None
    app.audio_lat_var.set("%d ms" % round(lat) if lat is not None else "-")
    mp = st.get("micPath")
    if mp is None and isinstance(st.get("mic"), dict):
        mp = st["mic"].get("micPath")
    app.mic_path_var.set(MIC_PATH_LABELS.get(mp, "-") if connected and app.settings["mic_enabled"] else "-")
    if AUDIO_AVAILABLE and app.audio.running:
        if app.audio.mode == "exclusive":
            mode = t("shell.sync.excl", d=app.audio.mode_detail)
        else:
            mode = t("shell.sync.shared", d=app.audio.mode_detail)
        if app.audio.notice:
            mode = app.audio.notice + "\n" + mode
        app.audio_mode_var.set(mode)
        app.audio_under_var.set(t("shell.sync.underruns", n=app.audio.underruns))
    else:
        app.audio_mode_var.set(app.audio.notice or "-")
        app.audio_under_var.set("")


def _apply_ranges(app, st):
    key = (st.get("isoMin"), st.get("isoMax"), st.get("zoomMax"), st.get("focusSupported"), st.get("torchAvailable"))
    if key == app._range_cache.get("k"):
        return
    app._range_cache["k"] = key
    try:
        if st.get("isoMin") is not None and st.get("isoMax") is not None and st["isoMax"] > st["isoMin"]:
            app.iso_scale.config(from_=st["isoMin"], to=st["isoMax"])
        if st.get("zoomMax") and st["zoomMax"] > 1.0:
            app.zoom_scale.config(from_=1.0, to=st["zoomMax"])
    except Exception:
        pass
    app._update_enabled()


# App のメソッドとして取り付ける関数 (呼び出しは app._xxx のまま。第1引数が App)
METHODS = (
    '_apply_phone_edit',
    '_update_v13',
    '_apply_ranges',

)
