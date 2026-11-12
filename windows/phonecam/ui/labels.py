# -*- coding: utf-8 -*-
"""UI 用の表示ラベル定数・色・文言整形関数
"""

from phonecam.core.softcam_shmem import WATCH_STATE_LABELS


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

PHONE_NOTE_SECONDS = 5.0


LENS_LABELS = [("wide", "広角"), ("ultrawide", "超広角"), ("front", "インカメ")]


RES_LABELS = ["720p", "1080p", "4K"]


FPS_LABELS = [30, 60]


MODE3_LABELS = [("auto", "自動"), ("locked", "固定"), ("manual", "マニュアル")]


SHUTTER_VALUES = [30, 50, 60, 100, 120, 250, 500, 1000]


ORIENT_LABELS = [("auto", "自動"), ("landscape", "横"), ("portrait", "縦")]


BACKEND_LABELS = [("phonecam", "PhoneCam（専用カメラ）"), ("obs", "OBS Virtual Camera"), ("unitycapture", "Unity Capture")]


CODEC_LABELS = [("h264", "H.264"), ("hevc", "HEVC(H.265)")]


OUTPUT_SIZE_LABELS = [("720p", "1280×720（HD）"), ("1080p", "1920×1080（フルHD・おすすめ）"), ("4K", "3840×2160（4K）")]


CANVAS_LABELS = [("follow", "向きの設定に合わせる"), ("landscape", "横長で固定"), ("portrait", "縦長で固定"),
                 ("native", "映像のサイズそのまま（サイズが変わるとOBS・Discordで再読み込みが必要）")]


DECODER_LABELS = {"hw": "ハードウェア", "sw": "ソフトウェア"}


MIC_PATH_LABELS = {"lowLatency": "低遅延", "capture": "通常"}


AUDIO_PATH_LABELS = {"dedicated": "専用（低遅延）", "shared": "映像と共用"}


THIN_TEXT = "PCの処理が追いつかないためフレームを間引いています"


CAM_REG_EXPLAIN = ("OBSやDiscordのカメラ一覧に「PhoneCam」を出すための登録です。普段はそのままで大丈夫です。"
                   "新しいバージョンにして「更新が必要」と出たときは「登録する（更新）」を押してください。"
                   "「登録を解除」はPhoneCamを使わなくなったとき（アンインストール）に使います。")


FIT_LABELS = [("pad", "余白を付けて全体を表示"), ("crop", "はみ出す部分を切り取って埋める")]


BG_MODE_LABELS = [("off", "オフ"), ("blur", "ぼかし"), ("green", "切り抜き(緑)")]


BG_QUALITY_LABELS = [("fast", "速度優先"), ("balanced", "バランス")]


THERMAL_LABELS = {"nominal": "正常", "fair": "やや高い", "serious": "高い", "critical": "危険"}


BATTERY_STATE_LABELS = {"charging": "充電中", "full": "満充電", "unplugged": "放電中", "unknown": "不明"}


SCREEN_AUTO_LABELS = [(0, "しない"), (10, "10秒"), (30, "30秒"), (60, "60秒")]


REC_RES_LABELS = [("same", "配信と同じ"), ("4k", "4K")]


REC_PRESET_LABELS = [("standard", "標準"), ("high", "高画質"), ("max", "最高")]


REC_CONTAINER_LABELS = [("mp4", "MP4（おすすめ・どこでも再生できます）"), ("mov", "MOV（編集ソフト向け）")]




# 更新バナー・更新欄の配色 (Apple 風: 淡いグレーの帯、青のボタン)
UPD_BG = "#f5f5f7"


UPD_BG_WHITE = "#ffffff"


UPD_FG = "#1d1d1f"


UPD_SUB = "#6e6e73"


UPD_LINE = "#d2d2d7"


UPD_ACCENT = "#0071e3"


UPD_WARN = "#b25000"


UPD_FONT = "Yu Gothic UI"


STATUS_TAB_INDEX = 5


METER_MIN_DB = -60.0


THERMAL_WARNING = "iPhoneが熱くなっています。fpsを30に下げるか、エフェクトをオフにしてください"


HINT_FG = "#666666"


THROTTLE_MS = 50  # 1コントロールあたり最大 20 msg/s


def setup_outcome_text(out, name_of):
    """インストール結果の要約 (成功 / 完了しなかった項目 / 再起動が必要)。name_of: 項目ID → 表示名。"""
    notes = []
    failed = {c: r for c, r in out.get("results", {}).items() if not r.get("ok")}
    if out.get("error"):
        notes.append(out["error"])
    elif failed:
        details = " / ".join("%s: %s" % (name_of(c), r.get("detail", "")) for c, r in failed.items())
        notes.append("完了しなかった項目があります。" + details)
    else:
        notes.append("インストールが完了しました。")
    if out.get("reboot"):
        notes.append("再起動が必要です。PCを再起動してください。")
    return "\n".join(notes)


def watch_text(watcher, now):
    """状態タブの「使用中の検出」: 使用中 / 未使用 / 共有メモリなし (+補足)。"""
    label = WATCH_STATE_LABELS.get(watcher.state(now), "-")
    detail = watcher.detail()
    err = getattr(watcher, "last_error", "")
    if err:
        detail = (detail + "、" if detail else "") + "エラー: " + err[:120]
    return "%s（%s）" % (label, detail) if detail else label


def format_battery(level, state):
    """バッテリー表示文字列。level は 0-1 または -1 (不明)。"""
    st = BATTERY_STATE_LABELS.get(state, "不明")
    try:
        lv = float(level)
    except Exception:
        lv = -1.0
    if lv < 0:
        return "不明" if st == "不明" else st
    return "%d%%（%s）" % (round(lv * 100), st)


def is_thermal_warning(thermal):
    return thermal in ("serious", "critical")
