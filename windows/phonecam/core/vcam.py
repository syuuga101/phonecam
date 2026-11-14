# -*- coding: utf-8 -*-
"""仮想カメラ出力 (softcam / pyvirtualcam) と SoftcamSender
"""

import ctypes
import os
import threading
import time
from phonecam.core.profiling import PROFILE
from phonecam.core.deps import np, pyvirtualcam
from phonecam.core.paths import _is_frozen
from phonecam.core.protocol import PhoneCamError
from phonecam.core.settings import BACKENDS, RESOLUTIONS
from phonecam.core.softcam_reg import (
    SOFTCAM_TOP_DOWN,
    _same_path,
    register_hint,
    registration_status,
    softcam_load_path,
    softcam_registered_path,
)
from phonecam.core.softcam_shmem import STANDBY_INTERVAL_SEC


# ---------------------------------------------------------------------------
# 仮想カメラ出力
# ---------------------------------------------------------------------------

class SoftcamSender:
    """softcam.dll (専用仮想カメラ) への ctypes ラッパー。BGR24 のフレームを送る。"""

    def __init__(self, dll_path):
        lib = ctypes.CDLL(dll_path)  # softcam は __cdecl
        lib.scCreateCamera.restype = ctypes.c_void_p
        lib.scCreateCamera.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float]
        lib.scDeleteCamera.restype = None
        lib.scDeleteCamera.argtypes = [ctypes.c_void_p]
        lib.scSendFrame.restype = None
        lib.scSendFrame.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.scIsConnected.restype = ctypes.c_bool
        lib.scIsConnected.argtypes = [ctypes.c_void_p]
        self._lib = lib
        self._handle = None

    def create(self, width, height):
        # framerate=0: 受け取ったフレームを待たずに即送る (低遅延)
        h = self._lib.scCreateCamera(int(width), int(height), 0.0)
        if not h:
            raise RuntimeError("scCreateCamera が失敗しました（別のアプリがPhoneCamカメラを使用中の可能性があります）")
        self._handle = h

    def send(self, img):
        """img: C 連続な uint8 (H, W, 3) BGR 配列"""
        self._lib.scSendFrame(self._handle, img.ctypes.data_as(ctypes.c_void_p))

    def close(self):
        h, self._handle = self._handle, None
        if h:
            self._lib.scDeleteCamera(h)  # 同一プロセスでは1台のみ。作り直す前に必ず削除


SIZE_CHANGED_NOTICE = "出力サイズが変わりました。OBSはソースを非表示→表示、Discordはカメラを一度オフ→オンにしてください"


CAM_RELEASE_HOLD_SEC = 1.5  # カメラを作り直す間、共有メモリの監視を止める時間


class VirtualCamOutput:
    def __init__(self, settings, state, slot=1):
        self.slot = slot
        self.profile_video_send = False
        self.settings = settings
        self.state = state
        self.cam = None
        self.size = None
        self.fail_until = 0.0
        self.reset_requested = False
        self._softcam = None
        self._softcam_path = None
        self._cam_kind = None  # 現在開いているバックエンド
        self._notice_until = 0.0  # 出力サイズ変更の案内を消す時刻
        self._lock = threading.RLock()  # 受信スレッドと GUI スレッド(待機フレーム)で同じカメラを触るため
        self.generation = 0       # カメラを作る/閉じるたびに増える (共有メモリの掴み直し用)
        self.last_frame_t = 0.0   # 最後に映像を渡した時刻 (monotonic)
        self._last_standby_t = 0.0
        self._black = None
        self.release_hooks = []   # カメラを閉じる/作り直す直前に呼ぶ (共有メモリの監視を手放すため)

    # -- 補助 -----------------------------------------------------------
    def backend(self):
        b = self.settings.get("backend")
        return b if b in BACKENDS else "obs"

    def frame_format(self):
        """デコード結果をどの画素形式で渡すか。"""
        return "bgr24" if self.backend() == "phonecam" else "rgb24"

    def apply_pending_reset(self):
        if self.reset_requested:
            self.reset_requested = False
            self.close()
            self.fail_until = 0.0

    def _release_watchers(self):
        for fn in list(self.release_hooks):
            try:
                fn()
            except Exception:
                pass

    def close(self):
        with self._lock:
            if self.cam is not None:
                self._release_watchers()
                try:
                    self.cam.close()
                except Exception:
                    pass
                self.generation += 1
            self.cam = None
            self.size = None
            self._cam_kind = None

    def park(self):
        """切断したとき。PhoneCam専用カメラは、使うアプリが繋がったままにできるよう残して待機フレームを送る。
        それ以外の出力先は閉じる。"""
        with self._lock:
            if self.cam is not None and self._cam_kind == "phonecam" and self.backend() == "phonecam":
                return
            self.close()

    def standby(self, canvas, now):
        """PhoneCam専用カメラを作った状態に保ち、映像が来ないときは約1秒ごとに黒フレームを送る。
        canvas: canvas_size() の結果 (None なら現在のサイズ。無ければ横長の既定)。"""
        with self._lock:
            self.apply_pending_reset()
            if self.backend() != "phonecam":
                return
            if now - self.last_frame_t < STANDBY_INTERVAL_SEC or now - self._last_standby_t < STANDBY_INTERVAL_SEC:
                return
            self._last_standby_t = now
            w, h = canvas or self.size or RESOLUTIONS.get(self.settings.get("output_size"), (1920, 1080))
            w, h = w // 4 * 4, h // 4 * 4
            if self._black is None or self._black.shape[:2] != (h, w):
                self._black = np.zeros((h, w, 3), dtype=np.uint8)
            self._send_locked(self._black, 30)

    def request_reset(self):
        self.reset_requested = True

    # -- 出力 -----------------------------------------------------------
    def _open_softcam(self, w, h):
        path = softcam_load_path(self.slot)
        if not os.path.isfile(path):
            if _is_frozen():
                raise PhoneCamError(
                    "exe に内蔵された softcam.dll が見つかりません。PhoneCamReceiver.exe をダウンロードし直してください。"
                    "OBS Virtual Camera など他の出力先も選べます")
            raise PhoneCamError(
                "softcam.dll が見つかりません。ソース実行では windows フォルダに softcam.dll を置き、"
                "register.bat を実行してください。OBS Virtual Camera など他の出力先も選べます")
        if self._softcam is not None and self._softcam_path != path:
            # 登録(更新)で読み込み元が変わった: 古い方のカメラを閉じて新しい DLL を使う
            self._softcam.close()
            self._softcam = None
        if self._softcam is None:
            try:
                self._softcam = SoftcamSender(path)
                self._softcam_path = path
            except OSError as e:
                raise PhoneCamError("softcam.dll を読み込めません: %s" % e)
        sender = self._softcam
        self._release_watchers()
        sender.close()
        sender.create(w, h)
        if _is_frozen():
            st = registration_status(self.slot)
            if st == "missing":
                self.state.update(error_text="PhoneCamカメラが未登録です。%s" % register_hint())
            elif st == "outdated":
                self.state.update(error_text="PhoneCamカメラの更新が必要です。%s" % register_hint())
            else:
                self.state.update(error_text="")
            return sender
        reg = softcam_registered_path(self.slot)
        if reg is None:
            self.state.update(error_text="PhoneCamカメラが未登録です。%s" % register_hint())
        elif not _same_path(reg, path):
            self.state.update(error_text="PhoneCamカメラの登録先が別のフォルダです。フォルダを移動した場合は register.bat をもう一度実行してください")
        else:
            self.state.update(error_text="")
        return sender

    def send(self, img, fps):
        with self._lock:
            self.last_frame_t = time.monotonic()
            self.profile_video_send = PROFILE is not None
            try:
                self._send_locked(img, fps)
            finally:
                self.profile_video_send = False

    def _send_locked(self, img, fps):
        self.apply_pending_reset()
        backend = self.backend()
        if backend == "phonecam":
            # softcam は幅・高さが4の倍数である必要がある
            h4, w4 = img.shape[0] // 4 * 4, img.shape[1] // 4 * 4
            if (h4, w4) != (img.shape[0], img.shape[1]):
                img = np.ascontiguousarray(img[:h4, :w4])
            if not SOFTCAM_TOP_DOWN:
                img = np.ascontiguousarray(img[::-1])
        h, w = img.shape[0], img.shape[1]
        size_changed = False
        if self.cam is not None and (self.size != (w, h) or self._cam_kind != backend):
            size_changed = self._cam_kind == backend and self.size != (w, h)
            self.close()
        if self.cam is None:
            now = time.time()
            if now < self.fail_until:
                return
            if backend == "phonecam":
                try:
                    self.cam = self._open_softcam(w, h)
                    self.size = (w, h)
                    self._cam_kind = backend
                    self.generation += 1
                except PhoneCamError as e:
                    self.cam = None
                    self.fail_until = now + 1.5
                    self.state.update(error_text=str(e))
                    return
                except Exception as e:
                    self.cam = None
                    self.fail_until = now + 1.5
                    self.state.update(error_text="PhoneCamカメラを開始できません。登録済みか確認してください（%s）。詳細: %s" % (register_hint(), e))
                    return
            else:
                if pyvirtualcam is None:
                    self.state.update(error_text="pyvirtualcam がインストールされていません。install.bat を実行してください")
                    return
                pv_backend = "unitycapture" if backend == "unitycapture" else "obs"
                try:
                    self.cam = pyvirtualcam.Camera(
                        w, h, max(1, int(fps)), fmt=pyvirtualcam.PixelFormat.RGB, backend=pv_backend
                    )
                    self.size = (w, h)
                    self._cam_kind = backend
                    self.generation += 1
                    self.state.update(error_text="")
                except Exception as e:
                    self.cam = None
                    self.fail_until = now + 3.0
                    if pv_backend == "obs":
                        msg = ("仮想カメラ(OBS Virtual Camera)を開始できません。OBS Studio 30以上をインストールし、"
                               "OBSの「仮想カメラ開始」を使用中の場合は停止してください。詳細: %s" % e)
                    else:
                        msg = ("仮想カメラ(Unity Capture)を開始できません。Unity Captureドライバーを"
                               "インストールしてください。詳細: %s" % e)
                    self.state.update(error_text=msg)
                    return
            if size_changed:
                self._notice_until = time.time() + 10.0
                self.state.update(error_text=SIZE_CHANGED_NOTICE)
        if self._notice_until and time.time() >= self._notice_until:
            self._notice_until = 0.0
            if self.state.snapshot()["error_text"] == SIZE_CHANGED_NOTICE:
                self.state.update(error_text="")
        try:
            if PROFILE is not None and backend == "phonecam":
                start = time.perf_counter()
                self.cam.send(img)
                PROFILE.add(self.slot, "softcam_send", (time.perf_counter() - start) * 1000)
                if self.profile_video_send:
                    PROFILE.count(self.slot, "softcam_frames")
            else:
                self.cam.send(img)
        except Exception as e:
            self.state.update(error_text="仮想カメラへの出力に失敗しました: %s" % e)
            self.close()
