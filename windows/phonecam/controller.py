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

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from queue import Empty, SimpleQueue

from phonecam.core.audio_engine import AudioEngine, find_vbcable_control_panel
from phonecam.core.canvas import canvas_size
from phonecam.core.deps import av, np, phonecam_dshowctl, phonecam_hotkeys, phonecam_update, pyvirtualcam
from phonecam.core.elevate import run_elevated
from phonecam.core.softcam_reg import camera_namespace, registration_status
from phonecam.core.version import APP_REPO, APP_VERSION
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
    settings_has_key,
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
    SHUTTER_VALUES,
    THERMAL_WARNING,
    THROTTLE_MS,
    is_thermal_warning,
    watch_text,
)

SAVE_DELAY_SEC = 0.5       # 設定の保存は最後の変更から 0.5 秒後
POLL_SEC = 0.1             # status の更新間隔
HOUSEKEEP_SEC = 1.0
STATUS_JITTER_SEC = 0.5     # 下の STATUS_JITTER_KEYS だけが変わったときの反映間隔
STATUS_JITTER_KEYS = frozenset(("latency_text", "audio_summary", "decode_ms"))
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


class Controller:
    def __init__(self, settings=None, persist=True, demo=False, secondary=False, audio_enabled=lambda: True):
        """settings=None なら設定ファイルから読む。persist=False は保存しない (CI のスクリーンショット用)。
        demo=True は受信・音声・使用中検出を動かさず、tick() で status を作り直すだけにする。"""
        self.secondary = secondary
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
        slot = settings.get("camera_slot", 1)
        self.link = Link(self.settings, self.state, self._get_mode_ip, audio=self.audio, stream=self.stream, slot=slot)
        self.link.audio_enabled = audio_enabled
        self.watcher = ConsumerWatcher(SoftcamMemoryReader(name=camera_namespace(slot) + "/SharedMemory"))
        # カメラを作り直す直前に共有メモリの監視を手放す (掴んだままだと同じ名前で作り直せない)
        self.link.vcam.release_hooks.append(lambda: self.watcher.reader.close(hold=CAM_RELEASE_HOLD_SEC))
        self.thermal_log = ThermalLog()

        # 通知 (UI が connect する)
        self.status_changed = CallbackList()    # emit(status)
        self.settings_changed = CallbackList()  # emit(key, value)   (UI 自身の操作でも発火する)
        self.toast = CallbackList()             # emit(text, kind)    kind: "info" / "error"
        self.update_changed = CallbackList()    # emit(banner)        banner: None か dict(text, actions=[(label, fn)])

        self.update_status_changed = CallbackList()
        self.update_action = CallbackList()
        self._update_status = ("update.status.never", {})
        self.update_check_busy = False
        self._update_started = 0.0
        self._dismissed_update = None
        self._setup_process = None
        self._setup_open = False
        self.setup_requested = CallbackList()   # emit(diagnose, after_update)  画面 (Qt) が同じプロセスのダイアログでセットアップを開く
        self._next_update = time.monotonic() + 3
        self._results = SimpleQueue()
        self.frozen = bool(getattr(sys, "frozen", False))
        if self.frozen and not secondary and not demo and phonecam_update is not None:
            phonecam_update.cleanup_old(os.path.dirname(sys.executable), prefix=Path(sys.executable).stem)
        self.camera_registration = registration_status(self.link.vcam.slot) if self.frozen and not demo else "unavailable"
        self.camera_registration_busy = False
        self.camera_registration_changed = CallbackList()
        self.camera_prompt = CallbackList()     # emit(state)   起動時に専用カメラの登録/更新を確認する ("missing" / "outdated")
        self._camera_prompted = False
        self.hotkey_conflicts = set()
        self._hotkeys = None
        if not secondary and not demo and phonecam_hotkeys is not None and phonecam_hotkeys.is_windows():
            self._hotkeys = phonecam_hotkeys.HotkeyManager()
            self._apply_hotkeys()

        self._dshow = None
        self._next_dshow_retry = 0
        self._dshow_reason = "shell.dshow.nomodule"
        if not demo and phonecam_dshowctl is not None:
            self._dshow = phonecam_dshowctl.ControlBridge(shutter_snap=SHUTTER_VALUES, namespace=camera_namespace(slot))
            self._dshow_reason = "shell.dshow.notready" if os.name == "nt" else "shell.dshow.winonly"

        self.wanted = False                     # 「接続」を押した状態
        self.closing = False
        self.update_banner = None
        self.status = Status()
        self._lock = threading.Lock()
        self._save_due = None
        self._send_due = {}
        self._next_poll = 0.0
        self._last_emit = 0.0
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
        if key in ("hotkeys", "hotkeys_enabled"):
            self._apply_hotkeys()
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
                    self.link.send_control(self.link.controls(group))

    def save_now(self):
        self._flush(time.monotonic(), force=True)

    def set_theme(self, mode):
        """"system" / "light" / "dark" (Qt 側は settings_changed("theme") で色を切り替える)。"""
        self.changed("theme", mode if mode in ("system", "light", "dark") else "system", None)

    def set_language(self, lang):
        """言語を保存する (文言は次回起動で切り替わる)。"""
        self.changed("language", lang, None)

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
            if not self.link.start():
                self.wanted = False
                err = t("multi.stopping")
                self.toast.emit(err, "error")
                return err
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

    def open_vbcable_panel(self):
        path = find_vbcable_control_panel() if os.name == "nt" else None
        if not path:
            return
        try:
            try:
                os.startfile(path, "runas")
            except OSError:
                os.startfile(path)
        except OSError as error:
            self.toast.emit(t("audio.vbcable.openfail", err=error), "error")

    def open_setup(self, diagnose=False, after_update=None):
        group = getattr(self, "camera_group", None)
        if group is not None and group.primary is not self:
            return group.primary.open_setup(diagnose, after_update)   # セットアップは本体 (カメラ 1) の設定を使う
        if self.demo or self._setup_process is not None or self._setup_open:
            return
        self.save_now()
        if self.setup_requested._cbs:
            # 画面があるとき: 別プロセスではなく、同じプロセスのダイアログで開く。閉じたら setup_dialog_closed() が呼ばれる
            self._setup_open = True
            try:
                self.setup_requested.emit(bool(diagnose), after_update)
            except Exception:
                self._setup_open = False
            return
        args = [sys.executable]
        if not self.frozen:
            args.append(str(Path(__file__).resolve().parents[1] / "phonecam_qt.py"))
        args.append("--setup-diagnose" if diagnose else "--setup-wizard")
        if after_update:
            args.extend(("--after-update", after_update))
        try:
            process = subprocess.Popen(args)
            self._setup_process = process
            self.run_background(process.wait, lambda result: self._setup_finished(process))
        except OSError as error:
            self.toast.emit(t("settings.setup.launch_error", error=error), "error")

    def setup_dialog_closed(self):
        """同じプロセスのセットアップ (ウィザード / 診断) が閉じた: 仮想カメラの登録状態を取り直す。"""
        self._setup_open = False
        self.refresh_all_camera_registrations()

    def _setup_finished(self, process):
        if self._setup_process is not process:
            return
        self._setup_process = None
        self.refresh_all_camera_registrations()  # ウィザード (別プロセス) が登録した結果を、起動時に覚えた古い状態のままにしない
        current = load_settings()
        for key in ("setup_done", "setup_shown_version", "audio_defaults_backup", "vbcable_restore_pending"):
            self.changed(key, current[key])

    def first_run_setup(self):
        if self.demo or not self.frozen or phonecam_update is None:
            return
        if os.environ.get("PHONECAM_SMOKE") == "1":
            return  # CI の起動テスト用: ウィザード・登録確認 (UAC) を出さない
        version = phonecam_update.parse_version(APP_VERSION)
        version = phonecam_update.display_version(APP_VERSION) if version and version != (0, 0, 0) else ""
        if not self.get("setup_done"):
            if settings_has_key("setup_done") is False and self.camera_registration == "ok":
                self.changed("setup_done", True)
                if version:
                    self.changed("setup_shown_version", version)
                self.save_now()
                return
            if version:
                self.changed("setup_shown_version", version)
            self.open_setup()
            return
        elif version and self.get("setup_shown_version") != version:
            self.changed("setup_shown_version", version)
            self.open_setup(after_update=version)
            return
        self.prompt_camera_registration()

    def refresh_camera_registration(self):
        if self.demo or not self.frozen:
            return
        try:
            self.camera_registration = registration_status(self.link.vcam.slot)
            self.camera_registration_changed.emit()
            self.link.vcam.request_reset()
        except Exception:
            pass

    def refresh_all_camera_registrations(self):
        """全スロット (カメラ 1〜4) の登録状態を取り直す。別プロセスのセットアップが何番を登録したか分からないため。"""
        group = getattr(self, "camera_group", None)
        for camera in (group.cameras if group is not None else [self]):
            camera.refresh_camera_registration()

    def prompt_camera_registration(self):
        """セットアップ済みなのに専用カメラが「更新が必要 / 未登録」のとき、起動時に1回だけ確認を出す
        (設定ページの表示に気づかなくても、登録の更新ができるようにする)。"""
        if self.demo or not self.frozen or self._camera_prompted or self.get("backend") != "phonecam":
            return
        state = registration_status(self.link.vcam.slot)
        self.camera_registration = state
        if state in ("missing", "outdated"):
            self._camera_prompted = True
            self.camera_prompt.emit(state)

    def run_background(self, work, done):
        def run():
            try:
                result = work()
            except Exception as error:
                result = error
            self._results.put((done, result))

        threading.Thread(target=run, daemon=True).start()

    def change_camera_registration(self, action):
        if self.demo or not self.frozen or self.camera_registration_busy or action not in ("register", "unregister"):
            return
        self.camera_registration_busy = True
        self.camera_registration_changed.emit()
        self.toast.emit(t("video.vcam.elev_wait"), "info")

        def finished(result):
            self.camera_registration_busy = False
            self.camera_registration = registration_status(self.link.vcam.slot)
            self.camera_registration_changed.emit()
            self.link.vcam.request_reset()
            kind = result[0] if isinstance(result, tuple) else "error"
            expected = ("ok",) if action == "register" else ("missing", "unavailable")
            word = t(f"video.vcam.word_{action}")
            if kind == "ok" and self.camera_registration in expected:
                self.toast.emit(t("video.vcam.registered" if action == "register" else "video.vcam.unregistered"), "info")
            else:
                key = {"ok": "unchanged", "cancelled": "cancelled", "timeout": "timeout"}.get(kind, "failed")
                self.toast.emit(t(f"video.vcam.{key}", word=word), "error")

        slot = self.link.vcam.slot
        self.run_background(lambda: run_elevated(f"--{action} --camera-slot {slot}"), finished)

    def version_text(self):
        return phonecam_update.display_version(APP_VERSION) if phonecam_update else APP_VERSION

    def app_title(self):
        """ウィンドウタイトル / ナビ上部に出す「PhoneCam v2.1.1」(版数が取れない開発時は「PhoneCam dev」)。"""
        ver = self.version_text()
        if ver and ver[:1].isdigit():
            ver = "v" + ver
        return ("PhoneCam " + ver).strip()

    def phone_version_note(self):
        """iPhone アプリと PC の major.minor が違うときの注意文 (違わなければ "")。Tk の _update_note_phone_version と同じ判定。"""
        ver = self.status.phone.get("appVersion") if self.status else None
        if not isinstance(ver, str) or not ver or phonecam_update is None:
            return ""
        try:
            if (phonecam_update.parse_version(ver) is not None and phonecam_update.parse_version(APP_VERSION) is not None
                    and not phonecam_update.same_major_minor(ver, APP_VERSION)):
                return t("update.note.version_mismatch", pc=phonecam_update.display_version(APP_VERSION),
                         iphone=phonecam_update.display_version(ver))
        except Exception:
            pass
        return ""

    def screen_state_text(self):
        """接続中の iPhone の画面状態 (「現在のiPhoneの画面: 黒（自動: 30秒）」)。未接続なら ""。Tk の phone_sync と同じ。"""
        from phonecam.core.settings import SCREEN_AUTO_VALUES
        phone = self.status.phone if self.status else {}
        if not (self.status and self.status.connected and "screenBlack" in phone):
            return ""
        auto = phone.get("autoBlackSeconds")
        state = t("shell.sync.screen_black") if phone.get("screenBlack") else t("shell.sync.screen_normal")
        if auto in SCREEN_AUTO_VALUES:
            label = t("labels.screen_auto.0") if auto == 0 else t(f"labels.sec.{auto}")
            return t("shell.sync.screen_with_auto", state=state, auto=label)
        return t("shell.sync.screen", state=state)

    @property
    def update_status_text(self):
        key, args = self._update_status
        return t(key, **args)

    def _show_update_status(self, key, *, busy=False, **args):
        self._update_status = (key, args)
        self.update_check_busy = busy
        self.update_status_changed.emit(self.update_status_text, busy)

    def check_updates(self, auto=False):
        if self.update_check_busy or (auto and not self.get("update_check", True)):
            return
        if not phonecam_update or not APP_REPO or phonecam_update.parse_version(APP_VERSION) is None or self.demo:
            if not auto:
                self._show_update_status("update.status.dev")
            return
        self._show_update_status("update.status.checking", busy=True)
        self._update_started = time.monotonic()
        self.run_background(lambda: phonecam_update.fetch_latest(APP_REPO, APP_VERSION), lambda result: self._finish_update_check(result, auto))

    def _finish_update_check(self, result, auto):
        try:
            self._apply_update_result(result, auto)
        except Exception as error:   # 確認中のまま (ボタンが無効のまま) 固まらないよう、必ず結果の表示に戻す
            self._show_update_status("update.status.error", time=time.strftime("%m/%d %H:%M"), error=error)

    def _apply_update_result(self, result, auto):
        stamp = time.strftime("%m/%d %H:%M")
        if isinstance(result, Exception):
            self._show_update_status("update.status.error", time=stamp, error=result)
            return
        tag = result["tag"]
        version = result["version"]
        newer = phonecam_update.is_newer(tag, APP_VERSION)
        key = "update.status.available" if newer else "update.status.latest"
        self._show_update_status(key, time=stamp, version=version)
        if not auto:
            self._dismissed_update = None
        if tag == self._dismissed_update or not phonecam_update.should_notify(
                tag, APP_VERSION, self.get("update_skip_version", "") if auto else ""):
            return

        def dismiss():
            self._dismissed_update = tag
            self.update_banner = None
            self.update_changed.emit(None)

        def skip():
            self.changed("update_skip_version", version)
            dismiss()

        self.update_banner = {
            "text": t("update.banner.title", version=version),
            "actions": [(t("update.banner.now"), lambda: self.update_action.emit("install", result)),
                        (t("update.banner.notes"), lambda: self.update_action.emit("notes", result)),
                        (t("update.banner.later"), dismiss), (t("update.banner.skip"), skip)],
        }
        self.update_changed.emit(self.update_banner)

    def _apply_hotkeys(self):
        if self._hotkeys is None:
            return
        bindings = (self.get("hotkeys") or {}) if self.get("hotkeys_enabled", True) else {}
        try:
            result = self._hotkeys.apply(bindings)
        except (OSError, RuntimeError):
            result = dict.fromkeys(bindings, False)
        self.hotkey_conflicts = {action for action, ok in result.items() if ok is False}

    def _poll_dshow(self, now):
        bridge = self._dshow
        if bridge is None:
            return
        try:
            if not bridge.is_open:
                if now < self._next_dshow_retry:
                    return
                self._next_dshow_retry = now + 2
                if not bridge.open():
                    return
            for change in bridge.poll_changes():
                self.changed(change["key"], change["value"], change.get("group"))
            snapshot = self.state.snapshot()
            bridge.publish(snapshot["phone_status"] if snapshot["connected"] else {}, self.settings)
        except (OSError, ValueError):
            bridge.close()

    def dshow_status_text(self):
        if self._dshow is not None and self._dshow.is_open:
            return t("shell.dshow.on")
        return t("shell.dshow.off", reason=t(self._dshow_reason))

    def _poll_hotkeys(self):
        if self._hotkeys is None:
            return
        while (action := self._hotkeys.poll()) is not None:
            try:
                message = self._run_hotkey(action)
            except Exception as error:
                self.toast.emit(t("shortcuts.toast.failed", err=error), "error")
            else:
                if message:
                    self.toast.emit(message, "info")

    def _run_hotkey(self, action):
        if action == "camera":
            available = [v for v in (self.status.phone.get("lenses") or ()) if v in LENSES] or LENSES
            current = self.get("lens")
            lens = available[(available.index(current) + 1) % len(available)] if current in available else available[0]
            if lens == current:
                return t("shortcuts.toast.no_lens")
            self.set_lens(lens)
            return t("shortcuts.toast.camera", name=dict(LENS_LABELS)[lens])
        if action == "mic":
            muted = not self.get("mic_muted")
            self.changed("mic_muted", muted, "micMuted")
            return t("shortcuts.toast.mic_muted" if muted else "shortcuts.toast.mic_unmuted")
        if action == "blur":
            enabled = self.get("bg_mode") == "off"
            self.changed("bg_mode", "blur" if enabled else "off", "background")
            return t("shortcuts.toast.blur_on" if enabled else "shortcuts.toast.blur_off")
        if action == "pause":
            if not self.status.connected:
                return t("shortcuts.toast.not_connected")
            running = self.stream.desired(True, time.monotonic())
            self.start_pause()
            return t("shortcuts.toast.paused" if running else "shortcuts.toast.started")
        if action == "rec":
            if not self.status.connected:
                return t("shortcuts.toast.rec_not_connected")
            if self.toggle_record():
                return t("shortcuts.toast.rec_stop" if self.status.rec_active else "shortcuts.toast.rec_start")
        if action in ("zoom_in", "zoom_out"):
            delta = .25 if action == "zoom_in" else -.25
            maximum = self.status.phone.get("zoomMax") or 10
            zoom = min(maximum, max(1, round((self.get("zoom", 1) + delta) * 4) / 4))
            self.changed("zoom", zoom, "zoom")
            return t("shortcuts.toast.zoom", v=zoom)
        return None

    def toggle_record(self):
        """録画の開始/停止を iPhone へ依頼する。送れたら True。"""
        if self.demo or time.monotonic() < self._rec_lock_until:
            return False
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
        if self.settings["mic_enabled"] and self.link.audio_enabled():
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
        self._poll_hotkeys()
        while True:
            try:
                done, result = self._results.get_nowait()
            except Empty:
                break
            done(result)
        if (self.update_check_busy and phonecam_update
                and time.monotonic() - self._update_started > phonecam_update.CHECK_TIMEOUT_SEC + 15):
            # 応答が返らないまま確認中で止まったら、ボタンを押せる状態へ戻す
            self._show_update_status("update.status.error", time=time.strftime("%m/%d %H:%M"),
                                     error=t("update.status.failed_short"))
        if not self.secondary and not self.demo and phonecam_update and now >= self._next_update:
            if self._setup_process is not None or self._setup_open:
                self._next_update = now + 1.0
            else:
                self._next_update = now + phonecam_update.CHECK_INTERVAL_SEC
                self.check_updates(auto=True)
        if now >= self._next_poll:
            self._next_poll = now + POLL_SEC
            self._poll_dshow(now)
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
        if AUDIO_AVAILABLE and self.link.audio_enabled() and self.settings["mic_enabled"] and not self.audio.running:
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
        # ズーム (ピンチ) とライト: 設定だけ更新して画質ページへ反映する。iPhone へは送り返さない (group=None)
        zoom = pe.get("zoom")
        if isinstance(zoom, (int, float)) and not isinstance(zoom, bool) and zoom == zoom and zoom >= 1.0:
            zoom = round(float(zoom), 2)
            if abs(zoom - float(self.settings.get("zoom", 1.0))) >= 0.005:
                self.changed("zoom", zoom, None)
                notes.append(t("shell.sync.zoom", v="%.1f" % zoom))
        torch = pe.get("torch")
        if isinstance(torch, bool) and torch != bool(self.settings.get("torch", False)):
            self.changed("torch", torch, None)
            notes.append(t("shell.sync.torch_on") if torch else t("shell.sync.torch_off"))
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
        way = "Wi-Fi" if (snap.get("transport") or self.settings.get("mode")) == "wifi" else "USB"
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
            peer=snap.get("peer", ""),
            transport=snap.get("transport") or ("wifi" if self.settings.get("mode") == "wifi" else "usb"),
            res_fps=res_fps,
            resolution=snap["resolution"],
            recv_fps=fps,
            rx_fps=snap.get("rx_fps", 0.0),
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
            phone=st,
            dshow_text=self.dshow_status_text(),
            thin_total=snap.get("thin_total", 0),
            thin_last=snap.get("thin_last", 0),
            audio_summary=(self.audio.running, self.audio.error, self.audio.notice, self.audio.device_name,
                           self.audio.mode, self.audio.mode_detail, self.audio.underruns, snap.get("audio_path"),
                           round(self.audio.buffered_ms()) if self.audio.running else None,
                           round(self.audio.latency_ms() or 0) if self.audio.running else None),                                  # iPhone の status JSON (接続中のみ。未接続は {})
        )
        if s != self.status:
            # 遅延 (ms) や音声バッファのように毎回少しずつ変わる値だけの違いは、0.5秒に1回だけ反映する。
            # status_changed には全ページの更新が繋がっていて、10回/秒で GUI スレッド (GIL) を使うため。
            if (self.status and now - self._last_emit < STATUS_JITTER_SEC
                    and all(s.get(k) == self.status.get(k) for k in s if k not in STATUS_JITTER_KEYS)):
                return s
            self._last_emit = now
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
        if self.closing:
            return
        self.closing = True
        if self._hotkeys is not None:
            self._hotkeys.stop()
        if self._dshow is not None:
            self._dshow.close()
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
        if self._setup_process is not None:
            if self._setup_process.poll() is None:
                self.save_now()
                self.persist = False
            else:
                self._setup_finished(self._setup_process)
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
