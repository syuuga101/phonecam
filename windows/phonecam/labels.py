# -*- coding: utf-8 -*-
"""UI 用の表示ラベル定数・色・文言整形関数

文言は言語の切り替え (設定の「言語」) に追従させるため、import 時に t() を評価した定数にはしない。
一覧は _Seq / _Map (使うたびに今の言語で作り直す)、文章は関数にしてある。
"""

from phonecam.core.softcam_shmem import (
    AUTO_UNSUPPORTED_NOTE as _AUTO_UNSUPPORTED_NOTE,
    IDLE_STOP_LABELS as _IDLE_STOP_LABELS,
    RUN_MODE_LABELS as _RUN_MODE_LABELS,
    WATCH_STATE_LABELS as _WATCH_STATE_LABELS,
)
from phonecam.i18n import NO_VALUE, t


class _Seq:
    """[(値, 表示名), ...] のように振る舞う一覧。参照するたびに今の言語で作り直す。"""

    def __init__(self, build):
        self._build = build

    def __iter__(self):
        return iter(self._build())

    def __len__(self):
        return len(self._build())

    def __getitem__(self, index):
        return self._build()[index]

    def __contains__(self, item):
        return item in self._build()


class _Map:
    """{値: 表示名} のように振る舞う対応表。参照するたびに今の言語で作り直す。"""

    def __init__(self, build):
        self._build = build

    def __iter__(self):
        return iter(self._build())

    def __len__(self):
        return len(self._build())

    def __getitem__(self, key):
        return self._build()[key]

    def __contains__(self, key):
        return key in self._build()

    def get(self, key, default=None):
        return self._build().get(key, default)

    def keys(self):
        return self._build().keys()

    def values(self):
        return self._build().values()

    def items(self):
        return self._build().items()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

PHONE_NOTE_SECONDS = 5.0


LENS_LABELS = _Seq(lambda: [("wide", t("labels.lens.wide")), ("ultrawide", t("labels.lens.ultrawide")), ("front", t("labels.lens.front"))])


RES_LABELS = ["720p", "1080p", "4K"]


FPS_LABELS = [30, 60]


MODE3_LABELS = _Seq(lambda: [("auto", t("labels.mode3.auto")), ("locked", t("labels.mode3.locked")), ("manual", t("labels.mode3.manual"))])


SHUTTER_VALUES = [30, 50, 60, 100, 120, 250, 500, 1000]


ORIENT_LABELS = _Seq(lambda: [("auto", t("labels.orient.auto")), ("landscape", t("labels.orient.landscape")), ("portrait", t("labels.orient.portrait"))])


BACKEND_LABELS = _Seq(lambda: [("phonecam", t("labels.backend.phonecam")), ("obs", "OBS Virtual Camera"), ("unitycapture", "Unity Capture")])


CODEC_LABELS = [("h264", "H.264"), ("hevc", "HEVC(H.265)")]


OUTPUT_SIZE_LABELS = _Seq(lambda: [("720p", t("labels.output.720p")), ("1080p", t("labels.output.1080p")), ("4K", t("labels.output.4K"))])


CANVAS_LABELS = _Seq(lambda: [("follow", t("labels.canvas.follow")), ("landscape", t("labels.canvas.landscape")), ("portrait", t("labels.canvas.portrait")),
                 ("native", t("labels.canvas.native"))])


DECODER_LABELS = _Map(lambda: {"hw": t("labels.decoder.hw"), "sw": t("labels.decoder.sw")})


MIC_PATH_LABELS = _Map(lambda: {"lowLatency": t("labels.micpath.lowLatency"), "capture": t("labels.micpath.capture")})


AUDIO_PATH_LABELS = _Map(lambda: {"dedicated": t("labels.audiopath.dedicated"), "shared": t("labels.audiopath.shared")})


def thin_text():
    return t("labels.thin")


def cam_reg_explain():
    return t("labels.cam_reg_explain")


FIT_LABELS = _Seq(lambda: [("pad", t("labels.fit.pad")), ("crop", t("labels.fit.crop"))])


BG_MODE_LABELS = _Seq(lambda: [("off", t("labels.bgmode.off")), ("blur", t("labels.bgmode.blur")), ("green", t("labels.bgmode.green"))])


BG_QUALITY_LABELS = _Seq(lambda: [("fast", t("labels.bgq.fast")), ("balanced", t("labels.bgq.balanced"))])


THERMAL_LABELS = _Map(lambda: {"nominal": t("labels.thermal.nominal"), "fair": t("labels.thermal.fair"), "serious": t("labels.thermal.serious"), "critical": t("labels.thermal.critical")})


BATTERY_STATE_LABELS = _Map(lambda: {"charging": t("labels.battery.charging"), "full": t("labels.battery.full"), "unplugged": t("labels.battery.unplugged"), "unknown": t("labels.battery.unknown")})


SCREEN_AUTO_LABELS = _Seq(lambda: [(0, t("labels.screen_auto.0")), (10, t("labels.sec.10")), (30, t("labels.sec.30")), (60, t("labels.sec.60"))])


REC_RES_LABELS = _Seq(lambda: [("same", t("labels.recres.same")), ("4k", "4K")])


REC_PRESET_LABELS = _Seq(lambda: [("standard", t("labels.recpreset.standard")), ("high", t("labels.recpreset.high")), ("max", t("labels.recpreset.max"))])


REC_CONTAINER_LABELS = _Seq(lambda: [("mp4", t("labels.container.mp4")), ("mov", t("labels.container.mov"))])


# core/softcam_shmem.py の日本語ラベルを、値をキーに翻訳したもの
RUN_MODE_LABELS = _Seq(lambda: [(v, t("labels.runmode." + v)) for v, _ in _RUN_MODE_LABELS])
IDLE_STOP_LABELS = _Seq(lambda: [(v, t("labels.sec.%d" % v)) for v, _ in _IDLE_STOP_LABELS])

def auto_unsupported_note():
    return t("labels.auto_unsupported")


WATCH_STATE_LABELS = _Map(lambda: {k: t("labels.watch." + k) for k in _WATCH_STATE_LABELS})


STATUS_TAB_INDEX = 6  # app.NAV_ITEMS の「状態」の番号


METER_MIN_DB = -60.0


def thermal_warning():
    return t("labels.thermal_warning")


THROTTLE_MS = 50  # 1コントロールあたり最大 20 msg/s


def setup_outcome_text(out, name_of):
    """インストール結果の要約 (成功 / 完了しなかった項目 / 再起動が必要)。name_of: 項目ID → 表示名。"""
    notes = []
    failed = {c: r for c, r in out.get("results", {}).items() if not r.get("ok")}
    if out.get("error"):
        notes.append(out["error"])
    elif failed:
        details = " / ".join("%s: %s" % (name_of(c), r.get("detail", "")) for c, r in failed.items())
        notes.append(t("labels.setup.partial", details=details))
    else:
        notes.append(t("labels.setup.done"))
    if out.get("reboot"):
        notes.append(t("labels.setup.reboot"))
    return "\n".join(notes)


def watch_text(watcher, now):
    """状態タブの「使用中の検出」: 使用中 / 未使用 / 共有メモリなし (+補足)。"""
    label = WATCH_STATE_LABELS.get(watcher.state(now), NO_VALUE)
    detail = watcher.detail()
    err = getattr(watcher, "last_error", "")
    if err:
        detail = detail + (t("common.list_separator") if detail else "") + t("labels.watch_error", error=err[:120])
    return t("labels.watch_fmt", label=label, detail=detail) if detail else label


def format_battery(level, state):
    """バッテリー表示文字列。level は 0-1 または -1 (不明)。"""
    st = BATTERY_STATE_LABELS.get(state, t("labels.unknown"))
    try:
        lv = float(level)
    except Exception:
        lv = -1.0
    if lv < 0:
        return st
    return t("labels.battery_fmt", pct=round(lv * 100), state=st)


def is_thermal_warning(thermal):
    return thermal in ("serious", "critical")


def phone_mic_text(connected, phone_muted):
    """音声ページの「iPhone のマイク」行 (読み取り専用)。未接続は「-」。"""
    if not connected:
        return "-"
    return t("audio.phonemic.muted" if phone_muted else "audio.phonemic.on")
