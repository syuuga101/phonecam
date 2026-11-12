# -*- coding: utf-8 -*-
"""デコード: FrameMailbox・BacklogGuard・DecoderManager・make_decoder
"""

import threading
from collections import deque
from phonecam.core.deps import av
from phonecam.core.protocol import MSG_VIDEO, MSG_VIDEO_HEVC


# ---------------------------------------------------------------------------
# 映像を詰まらせないための部品 (受信/デコード → 出力 の受け渡しと遅れ検出)
# ---------------------------------------------------------------------------

LAG_LIMIT_MS = 100.0      # これ以上遅れたら、次のキーフレームまでデコードを省略する


LAG_SUSTAIN = 3           # 遅れ(通信側の計測)が何パケット続いたら遅れとみなすか


LAG_BASELINE_FRAMES = 300  # 通信時間の「いつもの値」を見積もる範囲


KEYFRAME_MIN_INTERVAL = 1.0


THIN_NOTICE_SEC = 3.0     # 間引いた後、この時間は「間引いています」を表示する


class FrameMailbox:
    """最新の1フレームだけを持つ受け渡し箱。出力が遅れたら古いフレームを上書きして捨てる (数える)。"""

    def __init__(self):
        self._cond = threading.Condition()
        self._item = None
        self.closed = False
        self.skipped = 0
        self.put_count = 0

    def put(self, item):
        """入れる。まだ取られていない前のフレームを上書きしたら True。"""
        with self._cond:
            replaced = self._item is not None
            if replaced:
                self.skipped += 1
            self._item = item
            self.put_count += 1
            self._cond.notify()
            return replaced

    def get(self, timeout=None):
        """最新のフレームを取り出す。タイムアウトまたは閉じられて空なら None (closed で区別する)。"""
        with self._cond:
            if self._item is None and not self.closed:
                self._cond.wait(timeout)
            item, self._item = self._item, None
            return item

    def close(self):
        with self._cond:
            self.closed = True
            self._cond.notify_all()


def _nal_types(payload, codec):
    """Annex B のパケットに含まれる NAL 種別を順に返す。"""
    n = len(payload)
    i = payload.find(b"\x00\x00\x01")
    while 0 <= i < n - 3:
        b = payload[i + 3]
        yield ((b >> 1) & 0x3F) if codec == "hevc" else (b & 0x1F)
        i = payload.find(b"\x00\x00\x01", i + 3)


def is_keyframe_packet(codec, payload):
    """デコードせずにキーフレーム (IDR/IRAP) か判定する。H.264: NAL 5 / HEVC: NAL 16〜21。"""
    try:
        for t in _nal_types(payload, codec):
            if codec == "hevc":
                if 16 <= t <= 21:
                    return True
            elif t == 5:
                return True
    except Exception:
        return False
    return False


class BacklogGuard:
    """デコードが映像に追いついていないか見張る。次のどちらかなら「遅れ」とする。
    - デコード時間が、iPhoneがフレームを作る間隔より長い状態が積み上がった量 (debt, ≒ 未処理パケットの滞留時間) が 100ms 超
    - 時計のずれを使って測った通信側の遅れが、いつもの値より 100ms 超の状態が数パケット続く
    遅れたらキーフレームが来るまで非キーフレームのデコードを省略する (decide)。"""

    def __init__(self, lag_ms=LAG_LIMIT_MS, sustain=LAG_SUSTAIN, window=LAG_BASELINE_FRAMES):
        self.lag_ms = float(lag_ms)
        self.sustain = int(sustain)
        self._comm = deque(maxlen=window)
        self.debt = 0.0
        self.excess = 0.0
        self._over = 0
        self.lagging = False
        self.skipped = 0

    def note_arrival(self, comm_ms):
        """パケット到着時の通信時間 (ms, 時計のずれ込み)。None なら無視する。"""
        if comm_ms is None:
            return
        self._comm.append(comm_ms)
        self.excess = comm_ms - min(self._comm)
        self._over = self._over + 1 if self.excess > self.lag_ms else 0

    def note_decode(self, decode_ms, interval_ms):
        """1フレームのデコード時間と、iPhoneがフレームを作る間隔 (ms)。"""
        self.debt = max(0.0, self.debt + decode_ms - max(1.0, interval_ms))

    def behind(self):
        return self.debt > self.lag_ms or self._over >= self.sustain

    def resume(self):
        self.lagging = False
        self.debt = 0.0
        self._over = 0

    def decide(self, is_key):
        """このパケットをデコードするなら True、省略するなら False。is_key() はキーフレーム判定 (遅れているときだけ呼ぶ)。"""
        if not self.lagging:
            if not self.behind():
                return True
            self.lagging = True
        if is_key():
            self.resume()
            return True
        self.skipped += 1
        return False


# ---------------------------------------------------------------------------
# 接続 + デコード スレッド
# ---------------------------------------------------------------------------

def codec_for_msg(msg_type):
    """映像パケット種別 → コーデック名。映像以外は None。"""
    if msg_type == MSG_VIDEO:
        return "h264"
    if msg_type == MSG_VIDEO_HEVC:
        return "hevc"
    return None


HW_DEVICE_TYPE = "d3d11va"


HW_MAX_FAILS = 45   # ハードウェアで一度も映像を出せないまま、デコードの失敗がこの回数続いたらソフトウェアに切り替える


CODEC_NAMES = {"h264": "H.264", "hevc": "HEVC(H.265)"}


class DecoderManager:
    """デコーダー (CodecContext) を1つだけ保持し、パケットのコーデックが変わったら作り直す。
    ハードウェアデコード (hw_wanted) を試し、使えない・失敗したときはソフトウェアへ自動で切り替える。"""

    def __init__(self, factory, hw=False):
        self._factory = factory
        self.codec = None
        self.decoder = None
        self.hw_wanted = bool(hw)      # 次にデコーダーを作るときにハードウェアを試すか
        self.hw_active = False         # 今のデコーダーが実際にハードウェアか
        self.hw_error = ""             # ハードウェアが使えなかった理由
        self.hw_disabled = False       # 失敗して、このセッション中は使わないと決めた
        self._requested_hw = None
        self._ok = 0
        self._fail = 0

    def get(self, codec):
        """(decoder, switched)。switched は既存のデコーダーを作り直したとき (コーデックの切替・ハードウェア⇔ソフトウェア) True。"""
        want = bool(self.hw_wanted) and not self.hw_disabled
        if self.decoder is None or codec != self.codec or want != self._requested_hw:
            switched = self.decoder is not None
            info = {}
            if want:
                self.decoder = self._factory(codec, True, info)
            else:
                self.decoder = self._factory(codec)
            self.codec = codec
            self._requested_hw = want
            self.hw_active = bool(info.get("hw"))
            if want:
                self.hw_error = info.get("hw_error", "")
            elif not self.hw_disabled:
                self.hw_error = ""
            self._ok = 0
            self._fail = 0
            return self.decoder, switched
        return self.decoder, False

    def note_ok(self):
        self._ok += 1
        self._fail = 0

    def note_error(self):
        """デコード例外を数える。ハードウェアで成功が無いまま続くならソフトウェアに切り替える (True)。"""
        if not self.hw_active:
            return False
        self._fail += 1
        if self._ok == 0 and self._fail >= HW_MAX_FAILS:
            return self.disable_hw("ハードウェアデコードで映像を出せませんでした")
        return False

    def disable_hw(self, reason):
        """このセッションではハードウェアを使わない。次の get() でソフトウェアのデコーダーを作る。"""
        self.hw_disabled = True
        self.hw_error = reason
        return True


def _hwaccel_class():
    """PyAV の HWAccel (PyAV 14 以降)。無ければ None。"""
    try:
        from av.codec.hwaccel import HWAccel
        return HWAccel
    except Exception:
        return None


def _tune_decoder(ctx, threads=True):
    if threads:
        try:
            ctx.thread_type = "SLICE"
        except Exception:
            pass
    # 低遅延フラグ (PyAV のバージョン差を吸収するため何重にも試す)
    applied = False
    try:
        flags_cls = getattr(av.codec.context, "Flags", None)
        if flags_cls is not None:
            for name in ("low_delay", "LOW_DELAY"):
                flag = getattr(flags_cls, name, None)
                if flag is not None:
                    try:
                        ctx.flags = ctx.flags | flag
                    except Exception:
                        ctx.flags |= flag
                    applied = True
                    break
    except Exception:
        pass
    if not applied:
        try:
            ctx.options = {"flags": "low_delay"}
        except Exception:
            pass


def make_decoder(codec="h264", hw=False, info=None):
    """デコーダーを作る。hw=True なら d3d11va を試し、できなければソフトウェア。
    info (dict) に結果を入れる: info["hw"]=ハードウェアで開けたか、info["hw_error"]=開けなかった理由。
    ハードウェアは allow_software_fallback=False で開く (PyAV が黙ってソフトウェアに落ちると、どちらで動いているか分からないため)。"""
    if info is None:
        info = {}
    info["hw"] = False
    if hw:
        try:
            cls = _hwaccel_class()
            if cls is None:
                raise RuntimeError("PyAV がハードウェアデコードに対応していません")
            ctx = av.CodecContext.create(codec, "r", hwaccel=cls(device_type=HW_DEVICE_TYPE, allow_software_fallback=False))
            _tune_decoder(ctx, threads=False)
            ctx.open()  # デバイスが作れないならここで例外になる
            info["hw"] = True
            return ctx
        except Exception as e:
            info["hw_error"] = "ハードウェアデコードを開始できませんでした (%s)" % e
    ctx = av.CodecContext.create(codec, "r")
    _tune_decoder(ctx)
    return ctx
