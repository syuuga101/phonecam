# -*- coding: utf-8 -*-
"""遅延計測 (LatencyTracker)・熱の履歴 (ThermalLog)・グラフ計算・書式
"""

import threading
from collections import deque


class LatencyTracker:
    """ping/pong で iPhone と PC の時計のずれ(offset)を推定し、フレームごとの遅延を集計する。
    時刻はすべて秒。offset は「iPhone時刻 ≈ PC時刻(perf_counter) + offset」。"""

    def __init__(self, max_offsets=15, max_frames=60):
        self._lock = threading.Lock()
        self._max_offsets = max_offsets
        self._max_frames = max_frames
        self.reset()

    def reset(self):
        with getattr(self, "_lock", threading.Lock()):
            self._pongs = deque(maxlen=self._max_offsets)   # (rtt, offset)
            self._frames = deque(maxlen=self._max_frames)   # (iphone, comm|None, pc)

    def add_pong(self, sent_s, recv_s, phone_t):
        rtt = recv_s - sent_s
        if rtt < 0 or rtt > 10.0:
            return False
        offset = phone_t - (sent_s + rtt / 2.0)
        with self._lock:
            self._pongs.append((rtt, offset))
        return True

    def offset(self):
        """RTT が最小だったサンプルの offset (非対称な遅れの影響が最も小さいため)。無ければ None。"""
        with self._lock:
            return self._offset_locked()

    def _offset_locked(self):
        if not self._pongs:
            return None
        return min(self._pongs, key=lambda p: p[0])[1]

    def add_frame(self, capture, send, recv_pc, done_pc):
        """capture/send: iPhone時刻[秒]、recv_pc/done_pc: PCの perf_counter[秒]。"""
        iphone = max(0.0, (send - capture) * 1000.0)
        pc = max(0.0, (done_pc - recv_pc) * 1000.0)
        with self._lock:
            off = self._offset_locked()
            comm = None
            if off is not None:
                comm = max(0.0, ((recv_pc + off) - send) * 1000.0)  # PC時刻 + offset = iPhone時刻
            self._frames.append((iphone, comm, pc))

    def averages(self):
        """直近の平均 (ms)。無い項目は None。キー: iphone, comm, pc, total, rtt"""
        with self._lock:
            frames = list(self._frames)
            rtts = [p[0] for p in self._pongs]
        out = {"iphone": None, "comm": None, "pc": None, "total": None,
               "rtt": (sum(rtts) / len(rtts) * 1000.0) if rtts else None}
        if frames:
            out["iphone"] = sum(f[0] for f in frames) / len(frames)
            out["pc"] = sum(f[2] for f in frames) / len(frames)
            full = [f for f in frames if f[1] is not None]
            if full:
                out["comm"] = sum(f[1] for f in full) / len(full)
                out["total"] = sum(f[0] + f[1] + f[2] for f in full) / len(full)
        return out


THERMAL_LEVELS = ("nominal", "fair", "serious", "critical")


HISTORY_SECONDS = 30 * 60


class ThermalLog:
    """温度と受信fpsを1秒ごとに記録 (直近30分)。「高い」以上だった時間も積算する。"""

    def __init__(self, seconds=HISTORY_SECONDS):
        self.samples = deque(maxlen=seconds)  # (レベル 0-3 または None, fps)
        self.hot_seconds = 0.0
        self._was_connected = False
        self._last_t = None

    def sample(self, now, connected, thermal, fps):
        if connected and not self._was_connected:
            self.hot_seconds = 0.0
        dt = 0.0 if self._last_t is None else min(2.0, max(0.0, now - self._last_t))
        self._last_t = now
        self._was_connected = connected
        level = THERMAL_LEVELS.index(thermal) if (connected and thermal in THERMAL_LEVELS) else None
        if level is not None and level >= 2:
            self.hot_seconds += dt
        self.samples.append((level, float(fps) if connected else 0.0))


def format_mmss(seconds):
    try:
        t = max(0, int(seconds))
    except Exception:
        return "-"
    if t >= 3600:
        return "%d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60)
    return "%02d:%02d" % (t // 60, t % 60)


GRAPH_RANGES = (5, 15, 30)  # 履歴グラフの表示範囲 (分)


GRAPH_FPS_MAX = 60.0


THERMAL_BLOCK_COLORS = {"nominal": "#8fd6a1", "fair": "#f5c542", "serious": "#f2994a", "critical": "#e5484d"}


def graph_window(samples, seconds):
    """直近 seconds 個のサンプル (1秒=1個)。足りない分は先頭を「データなし」で埋めて、常に seconds 個にそろえる。"""
    data = list(samples)[-seconds:] if seconds > 0 else []
    return [(None, 0.0)] * (seconds - len(data)) + data


def level_runs(window):
    """温度の帯用。同じレベルが続く区間にまとめる → [(開始位置, 長さ(秒), レベル 0-3 または None)]"""
    runs = []
    for i, (lv, _fps) in enumerate(window):
        if runs and runs[-1][2] == lv:
            runs[-1][1] += 1
        else:
            runs.append([i, 1, lv])
    return [tuple(r) for r in runs]


def fps_runs(window, min_fps=0.5):
    """fps折れ線用。未接続・受信なし(0 fps) の秒で線を切り、つながった区間ごとに [(位置, fps), ...] を返す。"""
    runs, cur = [], []
    for i, (lv, fps) in enumerate(window):
        if lv is None or fps < min_fps:
            if cur:
                runs.append(cur)
                cur = []
        else:
            cur.append((i, float(fps)))
    if cur:
        runs.append(cur)
    return runs


def range_label(minutes, fraction=1.0):
    """時間軸の「◯分前」。fraction=0.5 なら半分の位置。"""
    m = minutes * fraction
    return "%d分前" % m if abs(m - round(m)) < 1e-9 else "%.1f分前" % m


def format_bytes(n):
    try:
        n = float(n)
    except Exception:
        return "-"
    if n < 0:
        return "-"
    if n >= 1024 ** 3:
        return "%.2f GB" % (n / 1024 ** 3)
    return "%.1f MB" % (n / 1024 ** 2)


PRESSURE_LABELS = {"nominal": "正常", "fair": "やや高い", "serious": "高い", "critical": "危険", "shutdown": "停止"}


PRESSURE_FACTOR_LABELS = {
    "systemTemperature": "本体の温度",
    "peakPower": "電力の消費",
    "depthModuleTemperature": "深度センサーの温度",
    "cameraTemperature": "カメラの温度",
}


def format_pressure_factors(factors):
    if not isinstance(factors, (list, tuple)):
        return "-"
    names = [PRESSURE_FACTOR_LABELS[f] for f in factors if f in PRESSURE_FACTOR_LABELS]
    return "、".join(names) if names else "-"
