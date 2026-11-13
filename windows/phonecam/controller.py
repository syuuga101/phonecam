# -*- coding: utf-8 -*-
"""UI に依存しないアプリケーションコントローラ (Qt UI 用。Tk の ui/app.py とは独立)

設定・共有状態・受信 (Link)・音声・ストリーム制御を持ち、UI からは
  - changed(key, value, group)  設定変更 (保存と iPhone への送信は間引く)
  - tick()                      UI のタイマーから 50ms 前後ごとに呼ぶ (保存/送信の期限処理 + status の更新)
  - status                      最新の状態スナップショット (Status = 属性アクセスできる dict)
  - status_changed / settings_changed / toast   コールバックリスト (.connect(fn) / .emit(...))
だけ知っていればよい。tk / Qt は import しない (テストや CI で単体実行できる)。

docs: scratchpad/qt_contract.md
"""

import threading
import time

from phonecam.core.audio_engine import AudioEngine
from phonecam.core.canvas import build_control, canvas_size
from phonecam.core.deps import av, np, pyvirtualcam
from phonecam.core.link import Link
from phonecam.core.settings import (
    AUDIO_AVAILABLE,
    DEFAULT_SETTINGS,
    LENSES,
    load_settings,
    migrate_legacy_settings,
    mirror_key,
    normalize_pair_code,
    sanitize_settings,
    save_settings,
    valid_pair_code,
)
from phonecam.core.softcam_shmem import (
    AUTO_UNSUPPORTED_NOTE,
    ConsumerWatcher,
    SoftcamMemoryReader,
    WATCH_INTERVAL_MS,
)
from phonecam.core.state import SharedState
from phonecam.core.stats import ThermalLog
from phonecam.core.stream import StreamController, camera_state_text
from phonecam.core.vcam import CAM_RELEASE_HOLD_SEC
from phonecam.i18n import set_language, t
from phonecam.ui.labels import (
    LENS_LABELS,
    METER_MIN_DB,
    PHONE_NOTE_SECONDS,
    THERMAL_WARNING,
    THROTTLE_MS,
    is_thermal_warning,
    watch_text,
)

SAVE_DELAY_SEC = 0.5       # 設定の保存は最後の変更から 0.5 秒後
POLL_SEC = 0.1             # status の更新間隔
HOUSEKEEP_SEC = 1.0
PREVIEW_STALE_SEC = 1.5    # これより古い映像は「映像なし」


class CallbackList:
    """Qt を使わない簡易シグナル。connect(fn) で登録、emit(*args) で全員に通知 (例外は握りつぶす)。"""

    def __init__(self):
        self._cbs = []

    def connect(self, fn):
        if fn not in self._cbs:
            self._cbs.append(fn)
        return fn

    def disconnect(self, fn):
        try:
            self._cbs.remove(fn)
        except ValueError:
            pass

    def emit(self, *args, **kw):
        for fn in list(self._cbs):
            try:
                fn(*args, **kw)
            except Exception:
                pass


class Status(dict):
    """status スナップショット。status["key"] でも status.key でも読める (読み取り専用として扱う)。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def value_to_label(pairs, value):
    for v, label in pairs:
        if v == value:
            return label
    return pairs[0][1]


def label_to_value(pairs, label):
    for v, lab in pairs:
        if lab == label:
            return v
    return pairs[0][0]


class Controller:
    def __init__(self, settings=None, persist=True, demo=False):
        """settings=None なら設定ファイルから読む。persist=False は保存しない (CI のスクリーンショット用)。
        demo=True は受信・音声・使用中検出を動かさず、tick() で status を作り直すだけにする。"""
        self.persist = bool(persist)
        self.demo = bool(demo)
        self.demo_latency_ms = None   # demo: 遅延 (ms) の固定表示値
        if settings is None:
            if self.persist:
                migrate_legacy_settings()
            settings = load_settings() if self.persist else sanitize_settings(dict(DEFAULT_SETTINGS))
        self.settings = settings
        set_language(self.settings.get("language", "system"))

        self.state = SharedState()
        self.audio = AudioEngine(lambda: (self.settings["mic_volume"], self.settings["mic_mute"],
                                          self.settings["mic_delay_ms"]))
        self.stream = StreamController(self.settings)
        self.link = Link(self.settings, self.state, self._get_mode_ip, audio=self.audio, stream=self.stream)
        self.watcher = ConsumerWatcher(SoftcamMemoryReader())
        # カメラを作り直す直前に共有メモリの監視を手放す (掴んだままだと同じ名前で作り直せない)
        self.link.vcam.release_hooks.append(lambda: self.watcher.reader.close(hold=CAM_RELEASE_HOLD_SEC))
        self.thermal_log = ThermalLog()

        # 通知 (UI が connect する)
        self.status_changed = CallbackList()    # emit(status)
        self.settings_changed = CallbackList()  # emit(key, value)   (UI 自身の操作でも発火する)
        self.toast = CallbackList()             # emit(text, kind)    kind: "info" / "error"
        self.update_changed = CallbackList()    # emit(banner)        banner: None か dict(text, actions=[(label, fn)])

        self.wanted = False                     # 「接続」を押した状態
        self.closing = False
        self.update_banner = None
        self.status = Status()
        self._lock = threading.Lock()
        self._save_due = None
        self._send_due = {}
        self._next_poll = 0.0
        self._next_stream = 0.0
        self._next_house = 0.0
        self._last_sample = 0.0
        self._vcam_gen = -1
        self._was_connected = False
        self._screen_user_t = 0.0
        self._audio_retry_t = 0.0
        self._rec_lock_until = 0.0
        self._edit_seq = None
        self._phone_note = None                 # (text, until)
        self._meter_db = METER_MIN_DB
        self._meter_t = time.monotonic()
        self._preview = None                    # (img, fmt, t, seq)
        self._preview_seq = 0
        self.stream_state_text = "-"
        self.camera_state = "-"
        self.run_note = ""
        self.watch_state_text = "-"
        if not self.demo:
            self.apply_audio()
        self.poll()

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------
    def _get_mode_ip(self):
        return self.settings["mode"], self.settings["ip"].strip()

    def get(self, key, default=None):
        """設定値。特別キー "mirror_current" は現在のレンズに対応する左右反転。"""
        if key == "mirror_current":
            key = mirror_key(self.settings)
        return self.settings.get(key, default)

    def changed(self, key, value, group=None):
        """設定を変更する。保存は 0.5 秒後 (tick)、group を渡すと iPhone へ 50ms 間引いて送る。
        UI 操作の結果と同じ値で呼んでもよい (settings_changed が発火するので、ウィジェット側は
        「今の表示と同じなら何もしない」で無限ループを避けること)。"""
        alias = key
        if key == "mirror_current":
            key = mirror_key(self.settings)
        self.settings[key] = value
        now = time.monotonic()
        self._save_due = now + SAVE_DELAY_SEC
        if group:
            self._send_due.setdefault(group, now + THROTTLE_MS / 1000.0)
        self.settings_changed.emit(alias, value)

    def _flush(self, now, force=False):
        if self._save_due is not None and (force or now >= self._save_due):
            self._save_due = None
            if self.persist:
                save_settings(self.settings)
        for group, due in list(self._send_due.items()):
            if force or now >= due:
                self._send_due.pop(group, None)
                if not self.demo:
                    self.link.send_control(build_control(self.settings, group, self.link.last_frame_size))

    def save_now(self):
        self._flush(time.monotonic(), force=True)

    def set_theme(self, mode):
        """"system" / "light" / "dark" (Qt 側は settings_changed("theme") で色を切り替える)。"""
        self.changed("theme", mode if mode in ("system", "light", "dark") else "system", None)

    def set_language(self, lang):
        """言語を保存する (文言は次回起動で切り替わる)。"""
        self.changed("language", lang, None)
        set_language(lang)

    # ------------------------------------------------------------------
    # 接続・配信
    # ------------------------------------------------------------------
    def toggle_connect(self):
        """接続 / 切断。失敗したら理由 (文字列) を返し toast("error") も出す。成功は None。"""
        if not self.wanted:
            s = self.settings
            err = None
            if s["mode"] == "wifi" and not s["ip"].strip():
                err = t("connection.error.ip")
            elif s["mode"] == "wifi" and not valid_pair_code(s.get("pair_code", "")):
                err = t("connection.error.code")
            elif av is None or np is None or (s.get("backend") != "phonecam" and pyvirtualcam is None):
                err = t("connection.error.libs")
            if err:
                self.toast.emit(err, "error")
                return err
            self.wanted = True
            self.link.start()   # 認証失敗で止まっていた場合も、新しく始めるので再試行になる
        else:
            self.wanted = False
            self.link.stop()
            self.stream.reset_user()
        self.poll()
        return None

    def start_pause(self):
        """開始 / 一時停止ボタン。"""
        self.stream.press_button(self.state.snapshot()["connected"], time.monotonic())
        self._stream_tick_once(time.monotonic())
        self.poll()

    def set_mode(self, mode):
        """接続方法 "usb" / "wifi"。"""
        self.changed("mode", mode, None)
        self.link.retry_auth()   # 認証失敗で止まっていたら、つなぎ方を変えたのでもう一度試す

    def set_pair_code(self, raw):
        """ペアリングコード入力。数字6桁にそろえた値を返す (UI の欄をその値に直す)。"""
        code = normalize_pair_code(raw)
        if code != self.settings.get("pair_code", ""):
            self.changed("pair_code", code, None)
            if valid_pair_code(code):
                self.link.retry_auth()
        return code

    def set_ip(self, ip):
        if ip != self.settings.get("ip"):
            self.changed("ip", ip, None)

    def set_lens(self, value, send=True):
        """レンズ変更。左右反転は設定のレンズごとの値に切り替わる (settings_changed("mirror_current"))。
        send=False は iPhone 側の操作を取り込むとき (送り返さない)。"""
        if value not in LENSES:
            return
        self.changed("lens", value, "video" if send else None)
        self.settings_changed.emit("mirror_current", bool(self.settings[mirror_key(self.settings)]))

    def set_run_mode(self, value):
        self.changed("run_mode", value, None)
        self.stream.mode_changed()
        self._stream_tick_once(time.monotonic())

    def set_idle_stop(self, sec):
        self.changed("idle_stop_sec", sec, None)

    def set_backend(self, value):
        self.changed("backend", value, None)
        self.link.vcam.request_reset()
        self.stream.note_in_use(False, time.monotonic())
        self._stream_tick_once(time.monotonic())

    def set_hw_decode(self, on):
        self.link.hw_disabled = False   # 前回失敗していても、入れ直したら試す
        self.changed("hw_decode", bool(on), None)

    def set_screen(self, key, value):
        """省電力 (screen_black / screen_auto)。iPhone 側の値で上書きされないよう時刻を覚える。"""
        self._screen_user_t = time.monotonic()
        self.changed(key, value, "screen")

    def toggle_record(self):
        """録画の開始/停止を iPhone へ依頼する。送れたら True。"""
        rec = self.state.snapshot()["phone_status"].get("recording")
        active = bool(rec.get("active")) if isinstance(rec, dict) else False
        if self.link.send_control({"record": "stop" if active else "start"}):
            self._rec_lock_until = time.monotonic() + 1.5
            return True
        return False

    def send_control(self, control):
        """iPhone へ任意の control メッセージを送る (ズームボタンなど)。"""
        return self.link.send_control(control)

    def apply_audio(self):
        """設定に合わせて音声ストリームを開く/閉じる。"""
        if not AUDIO_AVAILABLE:
            return
        self._audio_retry_t = time.monotonic()
        if self.settings["mic_enabled"]:
            self.audio.low_latency = self.settings["mic_priority"] == "low"
            self.audio.start(self.settings["mic_device"], self.settings["mic_exclusive"])
        else:
            self.audio.stop()

    def meter_db(self):
        """入力レベル (dBFS)。ピークを保持して 60dB/秒 で下げる。UI が好きな間隔で呼んでよい。"""
        now = time.monotonic()
        dt = max(0.0, now - self._meter_t)
        self._meter_t = now
        db = self.audio.level_db() if (AUDIO_AVAILABLE and self.audio.running) else -100.0
        if db >= self._meter_db:
            self._meter_db = db
        else:
            self._meter_db = max(db, self._meter_db - 60.0 * dt)
        return max(METER_MIN_DB, min(0.0, self._meter_db))

    # ------------------------------------------------------------------
    # プレビュー (UI へのフレーム受け渡し)
    # ------------------------------------------------------------------
    def _hook_frames(self):
        """vcam.send を包んで最新フレームの参照を保存する。Link が vcam を作り直しても
        毎 tick で呼べば包み直す (二重には包まない)。core は変更しない。"""
        try:
            vc = self.link.vcam
            orig = vc.send
            if getattr(orig, "_pc_wrapped", False):
                return
        except Exception:
            return

        def send(img, fps):
            try:
                self._preview_seq += 1
                self._preview = (img, vc.frame_format(), time.monotonic(), self._preview_seq)
            except Exception:
                pass
            return orig(img, fps)

        send._pc_wrapped = True
        try:
            vc.send = send
        except Exception:
            pass

    def set_preview_frame(self, img, fmt="rgb24"):
        """demo / テスト用: プレビューに出すフレーム (H, W, 3) を直接入れる。"""
        self._preview_seq += 1
        self._preview = (img, fmt, time.monotonic(), self._preview_seq)

    def preview_frame(self):
        """最新のプレビュー用フレーム (img[H,W,3 uint8 numpy], fmt "rgb24"/"bgr24", seq)。
        接続していない/古いときは None。seq が前回と同じなら描き直し不要。"""
        item = self._preview
        if item is None:
            return None
        if not self.demo:
            if time.monotonic() - item[2] > PREVIEW_STALE_SEC or not self.state.snapshot()["connected"]:
                return None
        return item[0], item[1], item[3]

    # ------------------------------------------------------------------
    # 周期処理
    # ------------------------------------------------------------------
    def tick(self, now=None):
        """UI のタイマーから 50ms 前後ごとに呼ぶ。保存/送信の期限処理 + 周期的な status 更新。"""
        if self.closing:
            return
        now = time.monotonic() if now is None else now
        self._flush(now)
        if now >= self._next_poll:
            self._next_poll = now + POLL_SEC
            self.poll(now)
        if not self.demo and now >= self._next_stream:
            self._next_stream = now + WATCH_INTERVAL_MS / 1000.0
            try:
                self._stream_tick_once(now)
                self.watcher.last_error = ""
            except Exception as e:
                self.watcher.last_error = "%s: %s" % (type(e).__name__, e)
        if not self.demo and now >= self._next_house:
            self._next_house = now + HOUSEKEEP_SEC
            self._housekeeping(now)

    def _housekeeping(self, now):
        """音声ストリームが止まっていたら、3秒以上あけて開き直す。"""
        if AUDIO_AVAILABLE and self.settings["mic_enabled"] and not self.audio.running:
            if now - self._audio_retry_t >= 3.0:
                self.apply_audio()

    def _stream_tick_once(self, now):
        """待機フレーム送出 → 使用中の検出 → iPhone のカメラの起動/停止指示 → 表示用文字列。"""
        if self.demo:
            return
        vc = self.link.vcam
        if self.settings.get("backend") == "phonecam":
            vc.standby(canvas_size(self.settings), now)
            if self._vcam_gen != vc.generation:
                self.watcher.reset()
                self._vcam_gen = vc.generation
            self.watcher.poll(now)
            self.stream.note_in_use(self.watcher.in_use(now), now)
            self.watch_state_text = watch_text(self.watcher, now)
        else:
            if self._vcam_gen != -2:
                self.watcher.reset()
                self._vcam_gen = -2
            self.stream.note_in_use(False, now)
            self.watch_state_text = t("connection.watch.na")
        snap = self.state.snapshot()
        connected = snap["connected"]
        want = self.stream.pending(connected, now)
        if want is not None and self.link.send_control({"stream": {"active": want}}):
            self.stream.mark_sent(want)
        st = snap["phone_status"] if connected else {}
        self.stream_state_text = self.stream.state_text(connected, st, now)
        self.camera_state = camera_state_text(connected, st)
        self.run_note = AUTO_UNSUPPORTED_NOTE if self.stream.auto_unsupported() else ""

    # ------------------------------------------------------------------
    # iPhone 側の操作の取り込み
    # ------------------------------------------------------------------
    def _apply_phone_edit(self, st):
        """status の phoneEdit を取り込む。seq が前回より増えたときだけ lens / micMuted を設定へ反映する
        (iPhone へは送り返さない)。戻り値: 通知文 (無ければ None)。"""
        if not st:
            self._edit_seq = None
            return None
        pe = st.get("phoneEdit")
        if not isinstance(pe, dict):
            return None
        seq = pe.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            return None
        last = self._edit_seq
        self._edit_seq = seq
        if last is None or seq <= last:
            return None
        notes = []
        lens = pe.get("lens")
        if lens in LENSES and lens != self.settings.get("lens"):
            self.set_lens(lens, send=False)
            notes.append(t("shell.sync.lens", name=dict(LENS_LABELS)[lens]))
        muted = pe.get("micMuted")
        if isinstance(muted, bool) and muted != bool(self.settings.get("mic_muted", False)):
            self.changed("mic_muted", muted, None)
            notes.append(t("shell.sync.muted") if muted else t("shell.sync.unmuted"))
        return " / ".join(notes) if notes else None

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def poll(self, now=None):
        """最新の状態を集めて self.status を作り直し、変わっていれば status_changed を発火する。"""
        now = time.monotonic() if now is None else now
        if not self.demo:
            self._hook_frames()
        snap = self.state.snapshot()
        connected = snap["connected"]
        st = snap["phone_status"] if connected else {}
        if not self.demo:
            note = self._apply_phone_edit(st if connected else None)
            if note:
                self._phone_note = (note, now + PHONE_NOTE_SECONDS)
                self.toast.emit(note, "info")
            if connected and not self._was_connected:
                self._screen_user_t = now   # 接続直後は送った設定が反映される前なので、iPhone側の値で上書きしない
            self._was_connected = connected
            self._sync_screen(st, connected, now)
        # 温度履歴 (1秒ごと)
        if now - self._last_sample >= 1.0:
            self._last_sample = now
            self.thermal_log.sample(now, connected, st.get("thermalState"), snap["recv_fps"])

        # 上部の状態
        if not connected:
            key = "off"
        elif self.stream.desired(True, now):
            key = "live"
        elif getattr(self.stream, "user_paused", False):
            key = "paused"
        else:
            key = "idle"
        way = "Wi-Fi" if self.settings.get("mode") == "wifi" else "USB"
        device = ""
        sub = ""
        if connected:
            name = st.get("deviceName") or st.get("name") or t("connection.device.default")
            device = "%s · %s" % (name, way)
            sub = self.stream_state_text if key != "live" else ""
        elif self.wanted:
            sub = snap["status_text"]
        status_text = snap["status_text"]
        if self._phone_note and now < self._phone_note[1]:
            status_text = self._phone_note[0]

        res = snap["resolution"] or "-"
        if "→" in res:
            res = res.split("→", 1)[1].replace("出力", "").strip()
        fps = snap["recv_fps"]
        res_fps = "%s · %.0ffps" % (res, fps) if connected and res != "-" else "-"
        if self.demo and self.demo_latency_ms is not None and connected:
            lat = self.demo_latency_ms
        else:
            lat = self.link.latency.averages().get("total") if connected else None
        thermal = st.get("thermalState")
        thermal_text = t("connection.thermal." + thermal) if thermal in ("nominal", "fair", "serious", "critical") else "-"
        battery = "-"
        if "batteryLevel" in st:
            try:
                lv = float(st.get("batteryLevel", -1))
            except Exception:
                lv = -1.0
            if lv >= 0:
                battery = "%d%%" % round(lv * 100)
        rec = st.get("recording")
        rec = rec if isinstance(rec, dict) else {}
        s = Status(
            connected=connected,
            wanted=self.wanted,
            key=key,                                   # "live" / "idle" / "paused" / "off"
            state_text=t("connection.state." + key),
            status_text=status_text,                   # 受信スレッドの状態文 (+ iPhone 操作の通知)
            device=device,                             # "iPhone 13 · USB" (未接続は "")
            sub=sub,
            error_text=snap["error_text"],
            auth_state=snap["auth_state"],
            res_fps=res_fps,
            resolution=snap["resolution"],
            recv_fps=fps,
            decode_ms=snap["decode_ms"],
            latency_text="%d ms" % round(lat) if lat is not None else "-",
            thermal=thermal,
            thermal_text=thermal_text,
            battery_text=battery,
            warn_text=THERMAL_WARNING if (connected and is_thermal_warning(thermal)) else "",
            phone_muted=bool(connected and st.get("micMuted")),
            stream_text=self.stream_state_text,
            camera_text=self.camera_state,
            run_note=self.run_note,
            watch_text=self.watch_state_text,
            start_is_pause=self.stream.desired(connected, now),   # True なら「一時停止」、False なら「開始」
            start_enabled=connected,
            idle_enabled=self.stream.mode() == "auto",
            rec_active=bool(rec.get("active")),
            rec_enabled=bool(connected and now >= self._rec_lock_until),
            phone=st,                                  # iPhone の status JSON (接続中のみ。未接続は {})
        )
        if s != self.status:
            self.status = s
            self.status_changed.emit(s)
        return s

    def _sync_screen(self, st, connected, now):
        """iPhone 側の画面消灯状態を設定へ取り込む (UI 操作から 3 秒以内は無視)。"""
        if connected and "screenBlack" in st:
            black = bool(st.get("screenBlack"))
            if now - self._screen_user_t > 3.0 and black != self.settings["screen_black"]:
                self.settings["screen_black"] = black
                self._save_due = now + SAVE_DELAY_SEC
                self.settings_changed.emit("screen_black", black)

    # ------------------------------------------------------------------
    # 終了
    # ------------------------------------------------------------------
    def close(self):
        """アプリ終了時に呼ぶ。受信・音声を止め、設定を保存する。"""
        self.closing = True
        try:
            self.link.stop()
            self.link.vcam.close()
            self.watcher.reset()
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        if self.persist:
            save_settings(self.settings)

    # ------------------------------------------------------------------
    # demo (CI のスクリーンショット用)
    # ------------------------------------------------------------------
    @classmethod
    def demo_connected(cls, language="ja"):
        """接続中のサンプル値 (iPhone 13 / USB / 1080p60 / 31ms / 正常 / 82%) を入れた demo コントローラ。"""
        c = cls(settings=None, persist=False, demo=True)
        c.settings["language"] = language
        set_language(language)
        c.settings["mode"] = "usb"
        c.settings["run_mode"] = "always"
        c.wanted = True
        c.demo_latency_ms = 31
        c.state.update(
            connected=True, status_text="接続中", recv_fps=60.0, decode_ms=3.2, resolution="1080p",
            codec_in_use="h264", decoder="hw", audio_path="dedicated",
            phone_status={"deviceName": "iPhone 13", "thermalState": "nominal", "batteryLevel": 0.82,
                          "batteryState": "unplugged", "cameraRunning": True, "streamActive": True})
        c.stream.mode_changed()
        c.stream_state_text = "配信中"
        c.camera_state = "動作中"
        c.poll()
        return c
