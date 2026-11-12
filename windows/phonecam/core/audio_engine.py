# -*- coding: utf-8 -*-
"""音声エンジン: リングバッファ・リサンプラ・出力デバイス選択・AudioEngine
"""

import os
import threading
import time
from collections import deque
from phonecam.core.deps import np, sd
from phonecam.core.protocol import parse_audio


# ---------------------------------------------------------------------------
# iPhoneのマイク再生
# ---------------------------------------------------------------------------

class AudioRing:
    """float32 モノラルのリングバッファ (呼び出し側がロックする)。"""

    def __init__(self, capacity):
        self.buf = np.zeros(capacity, dtype=np.float32)
        self.cap = capacity
        self.head = 0   # 次に読む位置
        self.count = 0

    def write(self, x):
        n = len(x)
        if n >= self.cap:
            x = x[-self.cap:]
            n = self.cap
            self.head = 0
            self.count = 0
        over = self.count + n - self.cap
        if over > 0:  # 溢れる分は古い方を捨てる
            self.head = (self.head + over) % self.cap
            self.count -= over
        tail = (self.head + self.count) % self.cap
        first = min(n, self.cap - tail)
        self.buf[tail:tail + first] = x[:first]
        if first < n:
            self.buf[:n - first] = x[first:]
        self.count += n

    def read(self, n):
        n = min(n, self.count)
        first = min(n, self.cap - self.head)
        out = np.empty(n, dtype=np.float32)
        out[:first] = self.buf[self.head:self.head + first]
        if first < n:
            out[first:] = self.buf[:n - first]
        self.head = (self.head + n) % self.cap
        self.count -= n
        return out

    def drop(self, n):
        n = min(n, self.count)
        self.head = (self.head + n) % self.cap
        self.count -= n
        return n

    def clear(self):
        self.head = 0
        self.count = 0


class LinearResampler:
    """線形補間のリサンプラ。パケットをまたいで位相を引き継ぐのでつなぎ目でプチ音が出にくい。"""

    def __init__(self, src_rate, dst_rate):
        self.step = float(src_rate) / float(dst_rate)
        self.same = int(src_rate) == int(dst_rate)
        self.carry = None
        self.phase = 0.0

    def process(self, x):
        if self.same or len(x) == 0:
            return x
        if self.carry is None:
            buf = x.astype(np.float32, copy=False)  # 最初のパケットは先頭サンプルが位置0
        else:
            buf = np.concatenate((np.array([self.carry], dtype=np.float32), x))
        last = len(buf) - 1
        if last < 1:  # 1サンプルだけ: 次のパケットと合わせて補間する
            self.carry = buf[-1]
            return np.zeros(0, dtype=np.float32)
        if self.phase > last:
            n = 0
        else:
            n = int((last - self.phase) / self.step) + 1
        pos = self.phase + np.arange(n, dtype=np.float64) * self.step
        idx = np.minimum(pos.astype(np.int64), last - 1)
        frac = (pos - idx).astype(np.float32)
        out = buf[idx] * (1.0 - frac) + buf[idx + 1] * frac
        self.phase = self.phase + n * self.step - last
        self.carry = buf[-1]
        return out.astype(np.float32, copy=False)


DEFAULT_DEVICE_LABEL = "システム既定"


SYSTEM_DEFAULT_KEY = "__default__"


CABLE_KEYWORD = "CABLE Input"


AUDIO_START_MS = 20.0       # ジッターを測る前の目標 (これにユーザーの遅延設定を足す)


AUDIO_MIN_MS = 15.0         # 適応目標の下限 (大きめのパケットのとき・低遅延優先)


AUDIO_MIN_STABLE_MS = 20.0  # 「安定優先」の下限


AUDIO_MIN_FAST_MS = 8.0     # 「低遅延優先」でパケットが小さく安定しているときの下限


AUDIO_SMALL_PACKET_MS = 10.0  # 到着間隔がこれ以下なら「小さいパケット」(iPhoneの低遅延マイク経路は約5ms)


AUDIO_MAX_MS = 120.0        # 適応目標の上限 (ユーザーの遅延設定は別に足す)


AUDIO_JITTER_FACTOR = 2.5


AUDIO_JITTER_ALPHA = 1.0 / 16.0


AUDIO_JITTER_MIN_PACKETS = 10   # この数だけ測ってから適応に切り替える


AUDIO_SLACK_MIN_MS = 20.0   # 目標をこれ以上超えて溜まったら目標まで捨てる (下限。実際は max(20ms, 3×目標))


AUDIO_SLACK_FACTOR = 3.0


AUDIO_LAT_SAMPLES = 50


AUDIO_PRIORITIES = ("low", "stable")   # 低遅延優先 / 安定優先


EXCLUSIVE_FALLBACK_NOTICE = "排他モードを使えなかったため共有モードで再生中"


EXCLUSIVE_RATES_FIRST = 48000


EXCLUSIVE_DTYPES = ("int16", "int32", "float32")


EXCLUSIVE_BUDGET_SEC = 2.0  # 排他モードの試行にかける時間の上限 (超えたら共有モードへ)


def adaptive_audio_target_ms(jitter_ms, delay_ms=0.0, interval_ms=None, low_latency=True):
    """ジッター (パケット到着間隔の揺れ、EWMA, ms) から再生バッファの目標 [ms] を決める。
    未計測 (None) のときは 20ms + ユーザー遅延。
    interval_ms: パケットの到着間隔 (EWMA)。10ms 以下の小さく安定したパケットなら、下限を
    低遅延優先で 8ms / 安定優先で 20ms にする。大きいパケットは従来どおり max(15, 2.5×ジッター)
    (安定優先では下限 20ms)。"""
    return audio_base_target_ms(jitter_ms, interval_ms, low_latency) + float(delay_ms)


def audio_base_target_ms(jitter_ms, interval_ms=None, low_latency=True):
    """ユーザー遅延を含まない目標 [ms]。"""
    if jitter_ms is None:
        return AUDIO_START_MS
    small = interval_ms is not None and interval_ms <= AUDIO_SMALL_PACKET_MS
    if not low_latency:
        floor = AUDIO_MIN_STABLE_MS
    else:
        floor = AUDIO_MIN_FAST_MS if small else AUDIO_MIN_MS
    return min(AUDIO_MAX_MS, max(floor, AUDIO_JITTER_FACTOR * float(jitter_ms)))


def audio_slack_ms(base_target_ms):
    """目標をどれだけ超えて溜まったら古い方を捨てるか [ms]: max(20ms, 3×目標)。"""
    return max(AUDIO_SLACK_MIN_MS, AUDIO_SLACK_FACTOR * float(base_target_ms))


def audio_latency_ms(now_pc, capture_phone, offset, buffered_ms):
    """音声パケット1つの「iPhoneで録ってから PC の再生バッファを出るまで」の見込み [ms]。
    offset: iPhone時刻 ≈ PC時刻(perf_counter) + offset。offset が無ければ None。"""
    if offset is None:
        return None
    capture_pc = capture_phone - offset
    return max(0.0, (now_pc - capture_pc) * 1000.0 + float(buffered_ms))


VBCABLE_PANEL_EXE = "VBCABLE_ControlPanel.exe"


VBCABLE_LATENCY_HINT = ("VB-CABLEのコントロールパネルで \"Max Latency\" を 7168 → 2048 などに下げると、さらに遅延が減ります"
                        "（音が途切れる場合は戻してください）。管理者として開きます。変更の反映にはPCの再起動が必要なことがあります")


OBS_AUDIO_TIP = ("OBSで「CABLE Output」の音声ソースのプロパティを開き、「デバイスのタイムスタンプを使用」を"
                 "オフにすると遅延が減ることがあります")


VBCABLE_PANEL_MISSING = "VB-CABLEのコントロールパネルが見つかりません（VB-CABLEが入っていないか、別の場所にあります）"


def find_vbcable_control_panel(env=None, isfile=None):
    """VB-CABLE のコントロールパネル (VBCABLE_ControlPanel.exe) を Program Files から探す。無ければ None。"""
    env = os.environ if env is None else env
    isfile = os.path.isfile if isfile is None else isfile
    roots = []
    for key, default in (("ProgramW6432", r"C:\Program Files"), ("ProgramFiles", r"C:\Program Files"),
                         ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        for r in (env.get(key), default):
            if r and r not in roots:
                roots.append(r)
    for r in roots:
        for sub in ("VB\\CABLE", "VB\\VBCABLE", "VB-Audio\\Cable"):
            path = r.rstrip("\\/") + "\\" + sub + "\\" + VBCABLE_PANEL_EXE
            if isfile(path):
                return path
    return None


def list_output_devices(sd_mod=None):
    """出力デバイス一覧。WASAPI があればそれだけ (名前の重複は除く)。"""
    sd_mod = sd_mod or sd
    if sd_mod is None:
        return []
    apis = sd_mod.query_hostapis()
    out = []
    for i, d in enumerate(sd_mod.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            api = apis[d["hostapi"]]["name"] if d.get("hostapi") is not None else ""
            out.append({"index": i, "name": d["name"], "api": api,
                        "rate": int(d.get("default_samplerate") or 48000),
                        "channels": int(d["max_output_channels"])})
    wasapi = [d for d in out if "WASAPI" in d["api"]]
    use = wasapi or out
    seen, result = set(), []
    for d in use:
        if d["name"] not in seen:
            seen.add(d["name"])
            result.append(d)
    return result


def choose_output_device(devices, saved):
    """保存された名前に合うデバイスを返す。None はシステム既定。
    saved が空 (未設定) なら 'CABLE Input' を含むものを優先、無ければシステム既定。"""
    if saved == SYSTEM_DEFAULT_KEY:
        return None
    if saved:
        for d in devices:
            if d["name"] == saved:
                return d
        return None  # 保存したデバイスが見つからない → 既定で鳴らす
    for d in devices:
        if CABLE_KEYWORD.lower() in d["name"].lower():
            return d
    return None


class AudioEngine:
    """type 6 の音声をリングバッファに溜め、sounddevice の出力コールバックで再生する。
    ネットワークスレッドからは feed() だけを呼ぶ (短いロックのみでブロックしない)。"""

    def __init__(self, get_params):
        """get_params() → (音量 0-200 [%], ミュート, 遅延 [ms])"""
        self._get_params = get_params
        self._lock = threading.Lock()
        self.ring = None
        self.rate = 0
        self.channels = 1
        self._stream = None
        self._resampler = None
        self._src_rate = 0
        self._primed = False
        self._last_delay = 0.0
        self._peak = 0.0
        self._stopping = False
        self.dead = False
        self.error = ""
        self.device_name = ""
        self.dropped_samples = 0
        self.underruns = 0
        self._out_dtype = "float32"
        self.low_latency = True        # True: 低遅延優先 (下限 8ms) / False: 安定優先 (下限 20ms)
        self.mode = ""                 # 再生中のモード: "exclusive" / "shared" / ""
        self.mode_detail = ""          # 例: "48000 Hz / 2ch / int16 / 5 ms"
        self.notice = ""               # 排他モードを使えなかったときの案内
        self.offset_fn = None          # () -> iPhone時刻 - PC時刻 の推定 (LatencyTracker.offset)。無ければ None
        self._feed_lock = threading.Lock()   # 専用チャンネルと映像チャンネルの切替で2つのスレッドから呼ばれ得る
        self._jitter = None            # パケット到着間隔の揺れ (EWMA, ms)
        self._jitter_n = 0
        self._interval = None          # パケット到着間隔 (EWMA, ms)
        self._last_arrival = None
        self._last_dur = 0.0
        self._lat = deque(maxlen=AUDIO_LAT_SAMPLES)

    # -- バッファ -------------------------------------------------------------
    def setup(self, rate):
        """出力サンプルレートを決めてバッファを用意する (ストリームは開かない)。"""
        with self._lock:
            self.rate = int(rate)
            self.ring = AudioRing(int(rate * 2))
            self._primed = False
            self._resampler = None
            self._src_rate = 0
        self._reset_stats()

    def flush(self):
        with self._lock:
            if self.ring is not None:
                self.ring.clear()
            self._primed = False
            self._resampler = None
            self._src_rate = 0
        self._reset_stats()

    def _reset_stats(self):
        self._jitter = None
        self._jitter_n = 0
        self._interval = None
        self._last_arrival = None
        self._lat.clear()

    def jitter_ms(self):
        """測定済みのジッター (ms)。測り始めて間もないときは None。"""
        return self._jitter if self._jitter_n >= AUDIO_JITTER_MIN_PACKETS else None

    def interval_ms(self):
        """パケット到着間隔の平均 (ms)。測り始めて間もないときは None。"""
        return self._interval if self._jitter_n >= AUDIO_JITTER_MIN_PACKETS else None

    def _base_target_ms(self):
        return audio_base_target_ms(self.jitter_ms(), self.interval_ms(), self.low_latency)

    def _target(self, delay_ms):
        ms = self._base_target_ms() + float(delay_ms)
        return int(self.rate * ms / 1000.0)

    def target_ms(self):
        return self._base_target_ms() + float(self._get_params()[2])

    def _note_arrival(self, now, dur_ms):
        """到着間隔が直前のパケットの長さからどれだけずれたかを EWMA で集計する。"""
        last = self._last_arrival
        if last is not None:
            gap = (now - last) * 1000.0
            dev = abs(gap - self._last_dur)
            if self._interval is None:
                self._interval = gap
            else:
                self._interval += (gap - self._interval) * AUDIO_JITTER_ALPHA
            if self._jitter is None:
                self._jitter = dev
            else:
                self._jitter += (dev - self._jitter) * AUDIO_JITTER_ALPHA
            self._jitter_n += 1
        self._last_arrival = now
        self._last_dur = dur_ms

    def latency_ms(self):
        """直近の音声遅延の平均 [ms] (PCの出力バッファを出るまで)。測れていなければ None。"""
        lat = list(self._lat)
        return sum(lat) / len(lat) if lat else None

    def feed(self, payload, now=None):
        """ネットワークスレッドから呼ぶ。解析 → リサンプル → 追加。"""
        if self.ring is None:
            return
        parsed = parse_audio(payload)
        if parsed is None:
            return
        capture, src_rate, pcm = parsed
        if now is None:
            now = time.perf_counter()
        peak = float(np.abs(pcm).max()) if len(pcm) else 0.0
        if peak > self._peak:
            self._peak = peak
        with self._feed_lock:
            self._note_arrival(now, len(pcm) * 1000.0 / src_rate)
            offset = self.offset_fn() if self.offset_fn is not None else None
            lat = audio_latency_ms(now, capture, offset, self.buffered_ms())
            if lat is not None and lat < 5000.0:
                self._lat.append(lat)
            if self._resampler is None or self._src_rate != src_rate:
                self._resampler = LinearResampler(src_rate, self.rate)
                self._src_rate = src_rate
            x = self._resampler.process(pcm)
            self.push(x)

    def push(self, x):
        delay = float(self._get_params()[2])
        target = self._target(delay)
        with self._lock:
            ring = self.ring
            if ring is None:
                return
            ring.write(x)
            slack = int(self.rate * audio_slack_ms(self._base_target_ms()) / 1000.0)
            if ring.count > target + slack:
                self.dropped_samples += ring.drop(ring.count - target)  # 溜まりすぎ: 目標まで古い方を捨てる

    def pull(self, frames):
        """出力コールバック用: frames 個の mono float32 を返す。足りなければ無音。"""
        vol, mute, delay = self._get_params()
        target = self._target(float(delay))
        with self._lock:
            ring = self.ring
            if ring is None:
                return np.zeros(frames, dtype=np.float32)
            if self._primed and delay > self._last_delay + 10:
                self._primed = False  # 遅延を増やしたので溜め直す
            self._last_delay = float(delay)
            if not self._primed:
                if ring.count >= target:
                    self._primed = True
                else:
                    return np.zeros(frames, dtype=np.float32)
            if ring.count < frames:
                got = ring.read(frames)
                self._primed = False
                self.underruns += 1
                out = np.zeros(frames, dtype=np.float32)
                out[:len(got)] = got
            else:
                out = ring.read(frames)
        gain = 0.0 if mute else float(vol) / 100.0
        out *= gain
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def level_db(self):
        """前回読んでからのピーク (dBFS)。無音は -100。"""
        p, self._peak = self._peak, 0.0
        if p <= 0.0:
            return -100.0
        import math
        return max(-100.0, 20.0 * math.log10(p))

    def buffered_ms(self):
        with self._lock:
            if self.ring is None or not self.rate:
                return 0.0
            return self.ring.count * 1000.0 / self.rate

    # -- デバイス ---------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status):
        try:
            mono = self.pull(frames)
            dt = self._out_dtype
            if dt == "int16":
                outdata[:] = (mono * 32767.0).astype(np.int16)[:, None]
            elif dt == "int32":
                outdata[:] = (mono.astype(np.float64) * 2147483647.0).astype(np.int32)[:, None]
            else:
                outdata[:] = mono[:, None]
        except Exception:
            outdata.fill(0)

    def _finished(self):
        if not self._stopping:
            self.dead = True

    @staticmethod
    def default_exclusive(device_name):
        """排他モードの既定: 出力先の名前に 'CABLE Input' を含むときだけ ON。"""
        return CABLE_KEYWORD.lower() in str(device_name or "").lower()

    @staticmethod
    def make_wasapi_settings(exclusive):
        """sd.WasapiSettings を作る。古い sounddevice や非 Windows では None。"""
        try:
            return sd.WasapiSettings(exclusive=bool(exclusive))
        except Exception:  # AttributeError (WASAPI なし) / TypeError (古い署名)
            return None

    def _make_stream(self, dev, rate, ch, dtype, blocksize, extra):
        kw = dict(device=dev["index"], samplerate=rate, channels=ch, dtype=dtype,
                  blocksize=blocksize, latency="low", callback=self._callback,
                  finished_callback=self._finished)
        if extra is not None:
            kw["extra_settings"] = extra
        stream = sd.OutputStream(**kw)
        stream.start()
        return stream

    def _open_exclusive(self, dev):
        """WASAPI 排他モードで開く。成功なら (stream, rate, ch, dtype, blocksize)、だめなら例外。"""
        if "WASAPI" not in str(dev.get("api", "")).upper():
            raise RuntimeError("WASAPI のデバイスではありません")
        extra = self.make_wasapi_settings(True)
        if extra is None:
            raise RuntimeError("この sounddevice は WASAPI 排他モードに対応していません")
        rates = []
        for r in (EXCLUSIVE_RATES_FIRST, int(dev["rate"])):
            if r and r not in rates:
                rates.append(r)
        chans = []
        for c in (dev["channels"], 2, 1):
            if c and c >= 1 and c not in chans:
                chans.append(c)
        last = None
        deadline = time.monotonic() + EXCLUSIVE_BUDGET_SEC
        for rate in rates:
            for ch in chans:
                for dtype in EXCLUSIVE_DTYPES:
                    for blocksize in (max(32, rate // 200), 0):
                        if time.monotonic() > deadline:
                            raise last or RuntimeError("時間切れ")
                        self._out_dtype = dtype
                        self.setup(rate)
                        try:
                            return self._make_stream(dev, rate, ch, dtype, blocksize, extra), rate, ch, dtype, blocksize
                        except Exception as e:
                            last = e
        raise last or RuntimeError("開けませんでした")

    def _open_shared(self, dev):
        """共有モード (従来どおり: float32、5ms ブロック)。成功なら (stream, rate, ch, 'float32', blocksize)。"""
        last = None
        tried = []
        self._out_dtype = "float32"
        self.setup(dev["rate"])
        for ch in (min(2, dev["channels"]), dev["channels"], 1):
            if ch < 1 or ch in tried:
                continue
            tried.append(ch)
            # 5ms ごとのコールバック (小さいブロック)。デバイスが受け付けなければ既定のブロックサイズで再挑戦
            for blocksize in (max(32, int(dev["rate"]) // 200), 0):
                try:
                    return self._make_stream(dev, dev["rate"], ch, "float32", blocksize, None), dev["rate"], ch, "float32", blocksize
                except Exception as e:  # 次のブロックサイズ / チャンネル数で再挑戦
                    last = e
        raise last or RuntimeError("開けませんでした")

    def start(self, saved_device, exclusive=None):
        """ストリームを開く。成功なら True。失敗時は self.error に日本語の理由を入れる。
        exclusive: True/False で排他モードを指定。None なら出力先が 'CABLE Input' のときだけ ON。
        排他モードで開けなければ自動で共有モードに切り替え、self.notice に案内を入れる。"""
        self.stop()
        self.notice = ""
        self.mode = ""
        self.mode_detail = ""
        if sd is None or np is None:
            self.error = "sounddevice がインストールされていません"
            return False
        try:
            devices = list_output_devices()
            dev = choose_output_device(devices, saved_device)
            if dev is None:
                idx = sd.default.device[1]
                if idx is None or idx < 0:
                    raise RuntimeError("出力デバイスが見つかりません")
                info = sd.query_devices(idx)
                try:
                    api = sd.query_hostapis()[info["hostapi"]]["name"]
                except Exception:
                    api = ""
                dev = {"index": idx, "name": info["name"], "api": api, "rate": int(info["default_samplerate"]),
                       "channels": int(info["max_output_channels"])}
            want_ex = self.default_exclusive(dev["name"]) if exclusive is None else bool(exclusive)
            opened = None
            if want_ex:
                try:
                    opened = self._open_exclusive(dev)
                    self.mode = "exclusive"
                except Exception:
                    self.notice = EXCLUSIVE_FALLBACK_NOTICE
                    opened = None
            if opened is None:
                opened = self._open_shared(dev)
                self.mode = "shared"
            stream, rate, ch, dtype, blocksize = opened
        except Exception as e:
            self.error = "音声の出力先を開けませんでした。別のデバイスを選ぶか、他のアプリが占有していないか確認してください（%s）" % e
            self.mode = ""
            with self._lock:
                self.ring = None
            return False
        self._stream = stream
        self.channels = ch
        self.device_name = dev["name"]
        self.mode_detail = "%d Hz / %dch / %s / %s" % (
            rate, ch, dtype, ("%.0f ms" % (blocksize * 1000.0 / rate)) if blocksize else "自動")
        self.underruns = 0
        self.dropped_samples = 0
        self.dead = False
        self.error = ""
        return True

    def stop(self):
        self._stopping = True
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._stopping = False
        self.dead = False
        self.mode = ""
        with self._lock:
            self.ring = None

    @property
    def running(self):
        return self._stream is not None and not self.dead
