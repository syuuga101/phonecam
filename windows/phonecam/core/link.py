# -*- coding: utf-8 -*-
"""受信スレッド群 (Link): 接続・認証・映像/音声受信・制御送信
"""

import json
import socket
import threading
import time
from phonecam.core.crashlog import log_error
from phonecam.core.canvas import CanvasFitter, build_control, canvas_size, pad_color_for
from phonecam.core.decoder import (
    BacklogGuard,
    DecoderManager,
    FrameMailbox,
    KEYFRAME_MIN_INTERVAL,
    codec_for_msg,
    is_keyframe_packet,
    make_decoder,
)
from phonecam.core.deps import av, np
from phonecam.core.protocol import (
    AUDIO_PORT,
    AUDIO_RCVBUF,
    AUDIO_RETRY_SEC,
    AUTH_REFUSED_RETRY_SEC,
    MAIN_RCVBUF,
    MSG_AUDIO,
    MSG_CONTROL,
    MSG_PONG,
    MSG_STATUS,
    MSG_TIMING,
    PHONE_PORT,
    PhoneCamError,
    RECONNECT_SEC,
    encode_msg,
    parse_pong,
    parse_timing,
    read_msg,
)
from phonecam.core.settings import (
    AUTH_FAIL_TEXT,
    AUTH_REFUSED_TEXT,
    auth_failure_kind,
    auth_status_effect,
    build_auth_control,
    mirror_key,
    valid_pair_code,
)
from phonecam.core.stats import LatencyTracker
from phonecam.core.usbmux import usbmux_available, usbmux_connect, usbmux_devices, wifi_connect
from phonecam.core.wireless import discover, local_ipv4, merge_devices, read_device
from phonecam.core.vcam import VirtualCamOutput


class Link:
    def __init__(self, settings, state, get_mode_ip, audio=None, stream=None, slot=1):
        self.stream = stream
        self.settings = settings
        self.state = state
        self.get_mode_ip = get_mode_ip
        self.audio = audio
        self.latency = LatencyTracker()
        self.vcam = VirtualCamOutput(settings, state, slot)
        self.routes = None
        self.audio_enabled = lambda: True
        self._usb_serial = None
        self._usb_retry_after = 0.0
        self._claimed = None
        self._thread = None
        self._stop = threading.Event()
        self._sock = None
        self._send_lock = threading.Lock()
        self._tx_cond = threading.Condition()
        self._tx_pending = {}      # GUI スレッドから渡された、まだ送っていない制御 (キーごとに最新値)
        self._tx_thread = None
        self.last_frame_size = None  # 最後にデコードしたフレームの (幅, 高さ)
        self.hw_disabled = False   # ハードウェアデコードが失敗した (設定を入れ直すまで、再接続しても使わない)
        self._decoder_key = None
        self._mode, self._ip, self._code = "usb", "", None   # 今回の接続で実際に使う方式 / IP / 登録済み端末のコード
        self._last_wifi = None     # auto で最後に Wi-Fi でつながれた (ip, code)。つながらなくなったら捨てる
        self._device_id = None     # USB 接続した iPhone の usbmux デバイスID (音声チャンネルも同じ iPhone へ)
        self._audio_sock = None
        # 認証 (v1.5): Wi-Fi ではペアリングコードが通るまで音声チャンネルを開かない
        self._auth_ok = threading.Event()
        self._auth_wake = threading.Event()   # 認証失敗で止まっているとき、再試行の合図
        self._authed = False
        self._saw_required = False
        self._auth_blocked = False
        self._kf_lock = threading.Lock()
        self._last_kf_req = 0.0    # 最後にキーフレームを要求した時刻 (monotonic)
        self._out_error = None
        self._decode_avg = 0.0
        self._fps_count = 0
        self._fps_t0 = time.time()
        if self.audio is not None:
            self.audio.offset_fn = self.latency.offset

    # -- 公開 API --------------------------------------------------------
    def start(self):
        self.stop()
        if self._thread is not None and self._thread.is_alive():
            return False
        self._last_wifi = None
        self._usb_retry_after = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="phonecam-link", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        t = self._thread
        self._stop.set()
        self._auth_wake.set()
        for sock in (self._sock, self._audio_sock):
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        if t is None or not t.is_alive():
            self._thread = None
            self.vcam.park()
        self.state.update(status_text="停止中", connected=False, recv_fps=0.0, decode_ms=0.0, resolution="-",
                          codec_in_use="-", decoder="-", decoder_note="", audio_path="-", auth_state="", transport="", peer="")
        self._decoder_key = None
        self.latency.reset()

    def send_control(self, control):
        """iPhone へ制御メッセージを送る。GUI (メイン) スレッドから呼ばれたときは、送信が詰まっても画面を止めないよう
        バックグラウンドの送信スレッドに渡す (スライダーを速く動かすと 1 秒に何度も呼ばれる)。"""
        if self._sock is None:
            return False
        if threading.current_thread() is threading.main_thread():
            return self._queue_control(control)
        return self._send_control_now(control)

    def _queue_control(self, control):
        with self._tx_cond:
            pending = self._tx_pending
            for k, v in control.items():
                pending[k] = v  # 同じキーは最新値で上書き (bitrate などは最後の値だけ送れば足りる)
            if self._tx_thread is None or not self._tx_thread.is_alive():
                self._tx_thread = threading.Thread(target=self._tx_loop, name="phonecam-tx", daemon=True)
                self._tx_thread.start()
            self._tx_cond.notify()
        return True

    def _tx_loop(self):
        while True:
            with self._tx_cond:
                while not self._tx_pending:
                    if not self._tx_cond.wait(30.0) and not self._tx_pending:
                        self._tx_thread = None
                        return  # 使われなくなったら終わる (次回また作る)
                pending, self._tx_pending = self._tx_pending, {}
            self._send_control_now(pending)

    def _send_control_now(self, control):
        sock = self._sock
        if sock is None:
            return False
        data = encode_msg(MSG_CONTROL, json.dumps(control).encode("utf-8"))
        try:
            with self._send_lock:
                sock.sendall(data)
            return True
        except Exception:
            # 途中まで送ってしまった可能性がある (タイムアウト等)。メッセージの区切りが壊れるので接続ごと閉じて作り直す
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            return False

    def retry_auth(self):
        """認証に失敗して自動再接続を止めているとき、もう一度つなぐ (コードを直したとき・接続ボタン)。"""
        self._auth_wake.set()

    # -- 内部 -------------------------------------------------------------
    def controls(self, group):
        control = build_control(self.settings, group, self.last_frame_size)
        if not self.audio_enabled() and "mic" in control:
            control["mic"]["enabled"] = False
        return control

    def _eligible_usb(self, stop=None):
        target = self.settings.get("target_device")
        preferred = self.settings.get("usb_serial")
        if self.routes is None:
            return usbmux_available()
        serials = self.routes.available_usb(self)
        if preferred and preferred in serials and self.routes.unbound_usb(self, preferred):
            return preferred
        if preferred and not target:
            return None
        for serial in serials:
            if stop and stop.is_set():
                break
            if not target and self.routes.unbound_usb(self, serial):
                return serial
            if target and self.routes.usb_identity(serial) == target and self.routes.unbound_usb(self, serial):
                return serial
        return None

    def _resolve(self):
        """今回つなぐ方式と相手を決める -> (方式, ip, コード)。設定が auto なら USB 優先、なければ Wi-Fi。"""
        mode, ip = self.get_mode_ip()
        self._usb_serial = self._eligible_usb(self._stop) if mode == "usb" or (mode == "auto" and time.monotonic() >= self._usb_retry_after) else None
        if self._usb_serial:
            return "usb", "", None
        if mode == "usb":
            if self.routes is None:
                return mode, ip, None
            raise PhoneCamError("USB iPhone not available")
        if mode == "wifi" and self.routes is None:
            return mode, ip, None
        target = self.settings.get("target_device")
        allowed = lambda address: self.routes is None or self.routes.can_claim(self, ("wifi", address))
        if target:
            known = [d for entry in self.settings.get("known_devices", []) if (d := read_device(entry)) and d.id == target]
            if not known:
                if ip and valid_pair_code(self.settings.get("pair_code", "")) and allowed(ip):
                    return "wifi", ip, None
                raise PhoneCamError("Selected iPhone is no longer registered")
            # Refresh the address of the selected device, never switch to another UUID.
            if self._last_wifi and allowed(self._last_wifi[0]):
                return "wifi", self._last_wifi[0], known[0].code
            fresh = discover(local_ipv4())
            for d in merge_devices(known, [d for d in fresh if d.id == target]):
                for address in d.addresses:
                    if allowed(address) and self._probe(address):
                        self._last_wifi = (address, d.code)
                        return "wifi", address, d.code
            raise PhoneCamError("Selected iPhone is not reachable")
        if mode == "wifi":
            return mode, ip, None
        if self._last_wifi and allowed(self._last_wifi[0]):
            return "wifi", self._last_wifi[0], self._last_wifi[1]
        code = self.settings.get("pair_code", "")
        if ip and valid_pair_code(code) and allowed(ip) and self._probe(ip):   # 一覧で選んだ / 手で入れた相手
            return "wifi", ip, None
        known = [d for entry in self.settings.get("known_devices", []) if (d := read_device(entry))]
        if self.routes is not None:
            known = [d for d in known if self.routes.unbound_device(self, d.id)]
        if known:
            found = discover(local_ipv4())
            live = {a for d in found for a in d.addresses}
            for d in merge_devices(known, found):
                for a in d.addresses:
                    if d.code and a in live and allowed(a):
                        self._last_wifi = (a, d.code)
                        return "wifi", a, d.code
            raise PhoneCamError("登録済みの iPhone が見つかりません。USB でつなぐか、PhoneCam を開いて同じ Wi-Fi につないでください")
        raise PhoneCamError("iPhone が見つかりません。USB でつなぐか、「iPhoneを追加」で登録してください")

    @staticmethod
    def _probe(ip):
        try:
            socket.create_connection((ip, PHONE_PORT), timeout=0.8).close()
            return True
        except OSError:
            return False

    def _watch_usb(self, sock, stop, ping_stop):
        """auto で Wi-Fi につないでいる間、USB が挿されたら Wi-Fi を切って USB につなぎ直す。"""
        while not stop.is_set() and not ping_stop.wait(2.0):
            if time.monotonic() >= self._usb_retry_after and self._eligible_usb(stop):
                self.state.update(status_text="USBがつながりました。切り替えます…")
                self._last_wifi = None
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                return

    def _connect(self, port=PHONE_PORT, info=None):
        mode, ip = self._mode, self._ip
        if mode == "usb":
            if port == PHONE_PORT and info is None:
                return usbmux_connect(serial=self._usb_serial if self.routes is not None else None)
            return usbmux_connect(port=port, device_id=(self._device_id if port != PHONE_PORT else None),
                                  info=info, serial=self._usb_serial if self.routes is not None else None)
        return wifi_connect(ip, port=port)

    @staticmethod
    def _tune_socket(sock, rcvbuf):
        """TCP_NODELAY と受信バッファの上限。受信バッファが大きいと、PCが遅れたとき映像がそこに溜まって遅延が増える。"""
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(rcvbuf))
        except Exception:
            pass

    def request_keyframe(self, force=False):
        """キーフレームを要求する。force でなければ 1 秒に 1 回まで。要求したら True。"""
        now = time.monotonic()
        with self._kf_lock:
            if not force and now - self._last_kf_req < KEYFRAME_MIN_INTERVAL:
                return False
            self._last_kf_req = now
        self.send_control({"keyframe": True})
        return True

    def _run(self):
        stop = self._stop
        while not stop.is_set():
            self.state.update(status_text="接続中…", connected=False)
            try:
                self._mode, self._ip, self._code = self._resolve()
                self.state.update(transport=self._mode, peer=self._ip if self._mode == "wifi" else "")
                if self.routes is not None:
                    resource = ("usb", self._usb_serial) if self._mode == "usb" else ("wifi", self._ip)
                    if stop.is_set() or not self.routes.claim(self, resource):
                        raise PhoneCamError("iPhone is in use by another output")
                    self._claimed = resource
                info = {}
                sock = self._connect(info=info)
                self._device_id = info.get("device_id")
                self.state.update(usb_serial=info.get("usb_serial", ""))
            except PhoneCamError as e:
                if self._mode == "usb":
                    self._usb_retry_after = time.monotonic() + 5
                self._release_route()
                self._last_wifi = None
                self.state.update(status_text="待機中（再接続を試行します）", error_text=str(e), connected=False)
                stop.wait(RECONNECT_SEC)
                continue
            except Exception as e:
                if self._mode == "usb":
                    self._usb_retry_after = time.monotonic() + 5
                self._release_route()
                self._last_wifi = None
                self.state.update(status_text="待機中（再接続を試行します）", error_text="接続エラー: %s" % e, connected=False)
                stop.wait(RECONNECT_SEC)
                continue

            if stop.is_set():
                sock.close()
                self._release_route()
                break
            sock.settimeout(1.0)
            self._tune_socket(sock, MAIN_RCVBUF)
            self._sock = sock
            self._authed = False
            self._saw_required = False
            self._auth_ok.clear()
            session_t0 = time.monotonic()
            try:
                self._session(sock, stop)
            except Exception as e:
                if not stop.is_set():
                    self.state.update(error_text="切断: %s" % e)
                    if not isinstance(e, (OSError, ConnectionError, PhoneCamError)):
                        log_error("link session")  # 想定外の例外だけ記録する (通常の切断は記録しない)
            finally:
                self._release_route()
                self._sock = None
                try:
                    sock.close()
                except Exception:
                    pass
            self.state.update(connected=False, recv_fps=0.0)
            if stop.is_set():
                break
            self._last_wifi = None
            kind = auth_failure_kind(self._mode, self._authed, self._saw_required)
            if kind == "code":
                # コード違い: 何度も再接続すると iPhone に締め出されるので、コードを直す/接続を押すまで待つ
                self.state.update(status_text="認証できません（ペアリングコードを確認してください）",
                                  error_text=AUTH_FAIL_TEXT, auth_state="failed")
                self._auth_blocked = True
                self._auth_wake.clear()
                while not stop.is_set() and not self._auth_wake.wait(0.3):
                    pass
                self._auth_blocked = False
                if stop.is_set():
                    break
                self.state.update(auth_state="")
                continue
            if kind == "refused":
                self.state.update(status_text="iPhoneに接続を断られました。しばらくしてから再接続します…",
                                  error_text=AUTH_REFUSED_TEXT, auth_state="refused")
                stop.wait(max(RECONNECT_SEC, AUTH_REFUSED_RETRY_SEC - (time.monotonic() - session_t0)))
                continue
            self.state.update(status_text="切断されました。再接続します…")
            stop.wait(RECONNECT_SEC)
        self.vcam.park()

    def _release_route(self):
        if self._claimed is not None:
            self.routes.release(self, self._claimed)
            self._claimed = None

    def _session(self, sock, stop):
        if av is None or np is None:
            raise PhoneCamError("av / numpy がインストールされていません。install.bat を実行してください")
        decoders = DecoderManager(make_decoder, hw=self._hw_wanted())
        self._decoder_key = None
        self.state.update(status_text="接続済み", connected=self._mode == "usb", error_text="", audio_path="shared",
                          thin_total=0, thin_last=0.0, phone_status={})  # 前の接続のステータス (phoneEdit の seq など) を持ち越さない

        # 最初にペアリングコードを送る (Wi-Fi で必須。USB では iPhone が無視する)。USB は認証不要なので音声チャンネルもすぐ開く
        mode = self._mode
        if mode == "wifi" or valid_pair_code(self.settings.get("pair_code", "")):
            auth = {"auth": {"code": self._code}} if self._code else build_auth_control(self.settings)
            with self._send_lock:
                sock.sendall(encode_msg(MSG_CONTROL, json.dumps(auth).encode("utf-8")))
        if mode == "usb":
            self._authed = True
            self._auth_ok.set()
        # 保存済み設定を全部送る + キーフレーム要求
        self._send_full(sock)

        last_kf = 0.0
        frame_count = 0
        fps_t0 = time.time()
        decode_avg = 0.0
        fitter = CanvasFitter()
        self.latency.reset()
        if self.audio is not None:
            self.audio.flush()
        pending_timing = None  # 次の映像パケットに対応する (captureTime, sendTime)
        ping_stop = threading.Event()
        threading.Thread(target=self._ping_loop, args=(ping_stop,), name="phonecam-ping", daemon=True).start()
        if mode == "wifi" and self.get_mode_ip()[0] == "auto":
            threading.Thread(target=self._watch_usb, args=(sock, stop, ping_stop), name="phonecam-usb-watch",
                             daemon=True).start()
        if self.audio is not None:
            threading.Thread(target=self._audio_channel_loop, args=(ping_stop, stop), name="phonecam-audio-ch",
                             daemon=True).start()
        try:
            self._receive_loop(sock, stop, decoders, fitter, last_kf, frame_count, fps_t0, decode_avg, pending_timing)
        finally:
            ping_stop.set()  # 映像の接続が閉じたら音声チャンネルも閉じる
            asock = self._audio_sock
            if asock is not None:
                try:
                    asock.close()
                except Exception:
                    pass

    def _send_full(self, sock=None):
        """保存済みの設定を全部 + キーフレーム要求を送る。"""
        sock = sock or self._sock
        if sock is None:
            return
        full = self.controls("all")
        full["keyframe"] = True
        if self.stream is not None:
            # 新しい接続では iPhone は既定で動作中として扱うので、PC側の希望をすぐ伝える
            full["stream"] = {"active": self.stream.on_connect(time.monotonic())}
        with self._send_lock:
            sock.sendall(encode_msg(MSG_CONTROL, json.dumps(full).encode("utf-8")))

    def _note_auth_status(self, st):
        """iPhone のステータスから認証の進み具合を記録する。認証待ち → 認証済みになったら設定を送り直す。"""
        effect = auth_status_effect(st)
        if effect == "required":
            self._saw_required = True
            self.state.update(auth_state="required", status_text="ペアリングコードを確認中…")
        elif effect == "ok":
            was_waiting = self._saw_required and not self._authed
            self._authed = True
            if not self.state.connected or self.state.auth_state:
                self.state.update(connected=True, auth_state="", status_text="接続済み")
            self._auth_ok.set()
            if was_waiting:
                try:
                    self._send_full()  # 認証前に送った分は無視されている可能性があるので送り直す
                except Exception:
                    pass

    # -- 音声チャンネル (ポート 5556) -------------------------------------
    def _audio_channel_loop(self, sess_stop, stop):
        """映像の接続が続く間、音声専用チャンネルを 2 秒ごとに繋ぎ直す。繋がらなければ (古いiPhoneアプリ)
        何も表示せず、映像チャンネルで届く音声をそのまま使う。"""
        while not sess_stop.is_set() and not stop.is_set():
            asock = None
            if not self.audio_enabled():
                sess_stop.wait(0.25)
                continue
            if not self._auth_ok.wait(0.2):
                continue  # 認証が通るまで (Wi-Fi) 音声チャンネルは開かない
            try:
                asock = self._connect(port=AUDIO_PORT)
                asock.settimeout(1.0)
                self._tune_socket(asock, AUDIO_RCVBUF)
                self._audio_sock = asock
                try:
                    asock.sendall(encode_msg(MSG_CONTROL, json.dumps({"hello": "audio"}).encode("utf-8")))
                except Exception:
                    pass
                self.state.update(audio_path="dedicated")
                self._audio_read_loop(asock, sess_stop, stop)
            except Exception:
                pass  # ポートが開いていない/切断: 静かに再試行
            finally:
                self._audio_sock = None
                if asock is not None:
                    try:
                        asock.close()
                    except Exception:
                        pass
                if not sess_stop.is_set():
                    self.state.update(audio_path="shared")
            if sess_stop.wait(AUDIO_RETRY_SEC):
                break

    def _audio_read_loop(self, asock, sess_stop, stop):
        class _Either:
            def is_set(self_inner):
                return sess_stop.is_set() or stop.is_set() or not self.audio_enabled()
        either = _Either()
        while not either.is_set():
            msg_type, payload = read_msg(asock, either)
            if msg_type == MSG_AUDIO and self.audio is not None:
                try:
                    if self.audio_enabled():
                        self.audio.feed(payload)
                except Exception:
                    pass
            # それ以外 (type 10 など) は無視する

    def _hw_wanted(self):
        return bool(self.settings.get("hw_decode", True)) and not self.hw_disabled

    def _report_decoder(self, decoders, codec_name):
        """使っているコーデックとデコーダー (ハードウェア/ソフトウェア) を表示用に記録する。"""
        kind = "hw" if decoders.hw_active else "sw"
        note = ""
        if not decoders.hw_active and self.settings.get("hw_decode", True):
            note = decoders.hw_error or "ハードウェアデコードを使えないため、ソフトウェアで処理しています"
        key = (codec_name, kind, note)
        if key != self._decoder_key:
            self._decoder_key = key
            self.state.update(codec_in_use=codec_name, decoder=kind, decoder_note=note)

    def _frame_to_image(self, frame, fmt, fitter):
        """デコード結果 → 出力サイズの BGR/RGB 配列。縮小と形式変換は reformat の1回で行う。"""
        canvas = canvas_size(self.settings)
        self.last_frame_size = (frame.width, frame.height)
        if canvas is None or canvas == (frame.width, frame.height):
            return frame.to_ndarray(format=fmt)
        return fitter.process(frame, fmt, canvas, self.settings.get("fit", "pad"), pad_color_for(self.settings))

    def _ping_loop(self, ping_stop):
        """1秒ごとに ping を送る (遅延計測用)。"""
        while not ping_stop.is_set():
            self.send_control({"ping": round(time.perf_counter() * 1000.0, 3)})
            if ping_stop.wait(1.0):
                break

    def _note_thinned(self, mailbox, guard):
        total = guard.skipped + mailbox.skipped
        if total != self.state.thin_total:
            self.state.update(thin_total=total, thin_last=time.monotonic())

    def _output_loop(self, mailbox, decoders, fitter):
        """出力スレッド: 最新フレームを取り出し、サイズ合わせ・反転・仮想カメラへの送信を行う。
        受信/デコードのスレッドはここを待たない (遅れたら mailbox が古いフレームを捨てる)。"""
        try:
            while True:
                item = mailbox.get(0.5)
                if item is None:
                    if mailbox.closed:
                        break
                    continue
                frame, timing, t_recv = item
                self.vcam.apply_pending_reset()
                fmt = self.vcam.frame_format()
                try:
                    img = self._frame_to_image(frame, fmt, fitter)
                except Exception:
                    if not decoders.hw_active:
                        raise
                    # ハードウェアのフレームを変換できない: ソフトウェアに切り替えて続ける
                    decoders.disable_hw("ハードウェアのフレームを変換できませんでした")
                    self.hw_disabled = True
                    self.request_keyframe(force=True)
                    continue
                decoders.note_ok()
                if self.settings.get(mirror_key(self.settings)):
                    img = img[:, ::-1]
                if self.settings.get("rotate180"):
                    img = img[::-1, ::-1]
                img = np.ascontiguousarray(img)
                fps_val = self.state.phone_status.get("fps", 60) or 60
                self.vcam.send(img, fps_val)
                if timing is not None:
                    self.latency.add_frame(timing[0], timing[1], t_recv, time.perf_counter())
                self._fps_count += 1
                now = time.time()
                if now - self._fps_t0 >= 1.0:
                    out_w, out_h = img.shape[1], img.shape[0]
                    if (frame.width, frame.height) == (out_w, out_h):
                        res_text = "%dx%d" % (out_w, out_h)
                    else:
                        res_text = "受信 %dx%d → 出力 %dx%d" % (frame.width, frame.height, out_w, out_h)
                    self.state.update(
                        recv_fps=self._fps_count / (now - self._fps_t0),
                        decode_ms=self._decode_avg,
                        resolution=res_text,
                    )
                    self._fps_count = 0
                    self._fps_t0 = now
        except Exception as e:
            log_error("output loop")
            self._out_error = e  # 受信スレッドが次のパケットで取り出して再接続する

    def _receive_loop(self, sock, stop, decoders, fitter, last_kf, frame_count, fps_t0, decode_avg, pending_timing):
        """受信スレッド: パケットを読んでデコードするだけ (順番を守る必要があるため)。
        デコードしたフレームは出力スレッドへ「最新の1枚」として渡す。pong など軽いものはここで即処理する。"""
        self._fps_count = frame_count
        self._fps_t0 = fps_t0
        self._decode_avg = decode_avg
        self._out_error = None
        mailbox = FrameMailbox()
        guard = BacklogGuard()
        worker = threading.Thread(target=self._output_loop, args=(mailbox, decoders, fitter),
                                  name="phonecam-output", daemon=True)
        worker.start()
        prev_capture = None
        try:
            while not stop.is_set():
                msg_type, payload = read_msg(sock, stop)
                t_recv = time.perf_counter()
                if self._out_error is not None:
                    raise self._out_error
                codec_name = codec_for_msg(msg_type)
                if codec_name is not None:
                    timing, pending_timing = pending_timing, None
                    # フレームを作る間隔と、通信の遅れ (時計のずれ込み) を見積もる
                    interval_ms = 1000.0 / float(self.state.phone_status.get("fps", 30) or 30)
                    if timing is not None:
                        if prev_capture is not None and 0.0 < timing[0] - prev_capture < 0.5:
                            interval_ms = (timing[0] - prev_capture) * 1000.0
                        prev_capture = timing[0]
                        off = self.latency.offset()
                        guard.note_arrival(((t_recv + off) - timing[1]) * 1000.0 if off is not None else None)
                    if not guard.decide(lambda: is_keyframe_packet(codec_name, payload)):
                        # 遅れているので、次のキーフレームまでデコードを省略して追いつく
                        self.request_keyframe()
                        self._note_thinned(mailbox, guard)
                        continue
                    t0 = t_recv
                    decoders.hw_wanted = bool(self.settings.get("hw_decode", True))  # 設定の切替をすぐ反映する
                    decoders.hw_disabled = self.hw_disabled
                    try:
                        decoder, switched = decoders.get(codec_name)
                        if switched:
                            self.request_keyframe(force=True)
                        self._report_decoder(decoders, codec_name)
                        frames = decoder.decode(av.Packet(payload))
                    except Exception:
                        if decoders.note_error():
                            self.hw_disabled = True  # ソフトウェアで作り直して続ける
                        self.request_keyframe()
                        continue
                    dt = (time.perf_counter() - t0) * 1000.0
                    self._decode_avg = dt if self._decode_avg == 0.0 else self._decode_avg * 0.9 + dt * 0.1
                    guard.note_decode(dt, interval_ms)
                    if guard.behind() and not guard.lagging:
                        self.request_keyframe()  # 遅れ始めた: 次のパケットから省略に入るので、先にキーフレームを要求
                    if not frames:
                        continue
                    mailbox.put((frames[-1], timing, t_recv))
                    self._note_thinned(mailbox, guard)
                elif msg_type == MSG_STATUS:
                    identity_ok = True
                    try:
                        st = json.loads(payload.decode("utf-8"))
                        if isinstance(st, dict):
                            self._note_auth_status(st)
                            if self._authed:
                                if self.routes is not None:
                                    identity_ok = self.routes.note_identity(self, st.get("deviceId"))
                                if identity_ok:
                                    self.state.update(phone_status=st)
                    except Exception:
                        pass
                    if not identity_ok:
                        raise PhoneCamError("The iPhone belongs to another camera profile")
                    # カメラ停止中は映像が来ない (タイムアウトにはしない)。表示だけ 0 に戻す
                    now = time.time()
                    if now - self._fps_t0 >= 2.0:
                        self.state.update(recv_fps=0.0)
                        self._fps_count = 0
                        self._fps_t0 = now
                elif msg_type == MSG_TIMING:
                    pending_timing = parse_timing(payload)
                elif msg_type == MSG_PONG:
                    p = parse_pong(payload)
                    if p is not None:
                        self.latency.add_pong(p[0] / 1000.0, t_recv, p[1])
                elif msg_type == MSG_AUDIO:
                    if self.audio is not None:
                        try:
                            if self.audio_enabled():
                                self.audio.feed(payload)
                        except Exception:
                            pass
                # それ以外の種別は無視する (将来の拡張用)
        finally:
            mailbox.close()
            if worker is not threading.current_thread():
                worker.join(timeout=2.0)
