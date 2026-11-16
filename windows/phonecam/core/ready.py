# -*- coding: utf-8 -*-
"""「今すぐつなげる相手がいるか」の判断 (画面を持たない純粋な関数と、USB の見張り)

- UsbWatcher: usbmux の端末一覧を 2 秒ごとに見て、PhoneCam アプリが応答しているか (識別ポート 5557) も調べる。
  映像のポート (5555) には触れない。つないでいる間 (busy) は何もしない。
- usb_state_of / usb_pick / connect_ready / connect_view: 見張りの結果から、画面に出す状態とボタンの種類を決める。
"""

from dataclasses import dataclass
from threading import Event, Lock, Thread
import time

from phonecam.core.usbmux import usbmux_app_state, usbmux_devices
from phonecam.core.wireless import device_id

READY = "ready"      # USB で見えていて、PhoneCam アプリも応答している
CABLE = "cable"      # USB のケーブルは見えるが、アプリが応答していない (前面にいない・信頼前など)

WATCH_SEC = 2.0          # 端末一覧を見る間隔 (アプリが応答していないときの確認し直しも同じ間隔)
READY_RECHECK_SEC = 6.0  # 応答していた端末を確かめ直す間隔
TRY_SEC = 8.0            # 「接続」を押してから、ボタンを押せなくして「接続中…」を出す長さ (それ以降は「切断」で止められる)


@dataclass(frozen=True)
class UsbSeen:
    state: str          # READY / CABLE
    ident: str = ""     # iPhone アプリの端末 ID (UUID)。応答していない・古いアプリでは空


class UsbWatcher:
    """usbmux を軽く見張る。step() が 1 回ぶんの仕事 (スレッドなしでもテストできる)。"""

    def __init__(self, list_fn=usbmux_devices, probe_fn=usbmux_app_state, clock=time.monotonic):
        self._list, self._probe, self._clock = list_fn, probe_fn, clock
        self._seen = {}          # {serial: UsbSeen}  (差し替えるだけ。読む側はコピー不要)
        self._due = {}           # {serial: 次に確かめる時刻}
        self._busy = {}
        self._lock = Lock()
        self._stop = Event()
        self._thread = None

    # -- 読む側 ----------------------------------------------------------
    def snapshot(self):
        return dict(self._seen)

    def set_busy(self, key, flag):
        """key (カメラなど) が接続を求めている間は、見張りを止める (配信を邪魔しない)。"""
        with self._lock:
            was = any(self._busy.values())
            if flag:
                self._busy[key] = True
            else:
                self._busy.pop(key, None)
            if was and not self._busy:
                self._due.clear()    # 切断した: すぐ見直す

    def busy(self):
        with self._lock:
            return bool(self._busy)

    # -- 1 回ぶん ----------------------------------------------------------
    def step(self, now=None):
        """端末一覧を見て、必要なものだけアプリの応答を確かめる。変わったら True。"""
        if self.busy():
            return False
        now = self._clock() if now is None else now
        try:
            serials = list(self._list())
        except Exception:
            serials = []
        before = self._seen
        seen = {}
        for serial in serials:
            old = before.get(serial)
            if old is not None and now < self._due.get(serial, 0.0):
                seen[serial] = old
                continue
            try:
                state, raw = self._probe(serial)
            except Exception:
                state, raw = CABLE, ""
            seen[serial] = UsbSeen(READY if state == READY else CABLE, device_id(raw) if state == READY else "")
            self._due[serial] = now + (READY_RECHECK_SEC if state == READY else WATCH_SEC)
        for serial in [s for s in self._due if s not in seen]:
            del self._due[serial]
        self._seen = seen
        return seen != before

    # -- スレッド ----------------------------------------------------------
    def ensure_started(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="phonecam-usb-ready", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                pass
            self._stop.wait(WATCH_SEC)


_watcher = None
_watcher_lock = Lock()


def usb_watcher():
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = UsbWatcher()
        return _watcher


# ---------------------------------------------------------------------------
# 状態の決め方 (純粋な関数)
# ---------------------------------------------------------------------------

def usb_state_of(seen, device_id="", serial="", sole=False):
    """登録済みの端末 1 台の USB の状態: READY / CABLE / "" (見えない)。
    端末 ID が合うものを優先し、次に保存してある USB シリアル、最後に「登録も USB も 1 台だけ」(sole) のときの ID が読めない端末。"""
    for value in seen.values():
        if device_id and value.ident == device_id:
            return value.state
    if serial and serial in seen and seen[serial].ident in ("", device_id):
        return seen[serial].state
    if sole and len(seen) == 1:
        value = next(iter(seen.values()))
        if not value.ident:
            return value.state
    return ""


def usb_pick(seen, target="", serial="", multi=False, taken=frozenset()):
    """このカメラが USB で使う端末 -> (状態, USB シリアル, 端末 ID) か None。Link._eligible_usb と同じ決め方。
    taken: 別のカメラが使っている USB シリアル。"""
    pool = {s: v for s, v in seen.items() if s not in taken}
    if not pool:
        return None
    if not multi:
        # 1 台のカメラなら、保存した USB の端末か、見えている端末 (応答しているものを先に) をそのまま使う
        chosen = serial if serial in pool else min(pool, key=lambda s: (pool[s].state != READY, s))
    elif serial and serial in pool:
        chosen = serial
    elif target:
        chosen = next((s for s, v in sorted(pool.items()) if v.ident == target), None)
    elif serial:
        chosen = None
    else:
        chosen = min(pool, key=lambda s: (pool[s].state != READY, s))
    if chosen is None:
        return None
    return pool[chosen].state, chosen, pool[chosen].ident


@dataclass(frozen=True)
class Ready:
    kind: str = ""       # "usb" (USB で今すぐつなげる) / "wifi" (登録した端末が Wi-Fi で見つかっている) / "cable" (ケーブルだけ見える) / "" (なし)
    name: str = ""


def connect_ready(usb_state, usb_name, wifi_name, mode="auto"):
    """つなげる相手の種類。usb_state は READY / CABLE / "" 、wifi_name は見つかった端末の名前 ("" ならなし)。
    自動 (auto) なら USB が先、次に Wi-Fi、最後にケーブルだけ。方式を決めているとき (usb / wifi) はその方式だけを見る。"""
    if mode != "wifi" and usb_state == READY:
        return Ready("usb", usb_name)
    if mode != "usb" and wifi_name:
        return Ready("wifi", wifi_name)
    if mode != "wifi" and usb_state == CABLE:
        return Ready("cable", usb_name)
    return Ready()


def is_trying(wanted, connected, elapsed, failed=False):
    """「接続」を押して間もなく、まだ結果が出ていない間。"""
    return bool(wanted and not connected and not failed and elapsed is not None and elapsed < TRY_SEC)


@dataclass(frozen=True)
class ConnectView:
    look: str            # "go" (緑の塗り) / "plain" (中立の枠線) / "stop" (切断: 中立で、ホバーだけ赤っぽい)
    text: str            # ボタンの文言の翻訳キー
    enabled: bool = True
    busy: bool = False   # 「接続中…」とスピナーを出す
    card: str = ""       # 上のカード: "ready" (つなぐ準備ができています) / "connecting" / ""


def connect_view(ready, wanted, connected, trying):
    if connected or (wanted and not trying):
        return ConnectView("stop", "connection.button.disconnect")
    if trying:
        return ConnectView("plain", "connection.button.connecting", enabled=False, busy=True, card="connecting")
    if ready.kind == "usb":
        return ConnectView("go", "connection.button.connect_usb", card="ready")
    if ready.kind == "wifi":
        return ConnectView("go", "connection.button.connect_wifi", card="ready")
    return ConnectView("plain", "connection.button.connect")
