# -*- coding: utf-8 -*-
"""受信スレッドと UI が共有する状態 (SharedState)
"""

import threading

from phonecam.i18n import t


# ---------------------------------------------------------------------------
# 共有状態 (スレッド間)
# ---------------------------------------------------------------------------

class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self.status_text = t("core.link.stopped")
        self.error_text = ""
        self.recv_fps = 0.0
        self.rx_fps = 0.0            # PCに届いた映像パケットの毎秒数 (間引き・デコード前。recv_fps は出力まで終えた数)
        self.decode_ms = 0.0
        self.resolution = "-"
        self.phone_status = {}
        self.connected = False
        self.codec_in_use = "-"      # 実際に受信している映像のコーデック ("h264" / "hevc")
        self.decoder = "-"           # "hw" / "sw"
        self.decoder_note = ""       # ハードウェアが使えなかった理由など
        self.audio_path = "-"        # "dedicated" (専用チャンネル) / "shared" (映像と共用) / "-"
        self.thin_total = 0          # PCの処理が追いつかず間引いたフレーム数 (デコード省略 + 出力で上書き)
        self.thin_last = 0.0         # 最後に間引いた時刻 (time.monotonic)
        self.usb_serial = ""
        self.peer = ""               # Wi-Fi でつないでいる (つなごうとしている) iPhone の IP
        self.transport = ""          # 今つないでいる方式: "usb" / "wifi" / ""
        self.auth_state = ""         # "" / "required" (認証待ち) / "failed" (コード違い) / "refused" (拒否)

    def update(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def snapshot(self):
        with self._lock:
            return {
                "status_text": self.status_text,
                "error_text": self.error_text,
                "recv_fps": self.recv_fps,
                "rx_fps": self.rx_fps,
                "decode_ms": self.decode_ms,
                "resolution": self.resolution,
                "phone_status": dict(self.phone_status),
                "connected": self.connected,
                "codec_in_use": self.codec_in_use,
                "decoder": self.decoder,
                "decoder_note": self.decoder_note,
                "audio_path": self.audio_path,
                "thin_total": self.thin_total,
                "thin_last": self.thin_last,
                "auth_state": self.auth_state,
                "transport": self.transport,
                "peer": self.peer,
                "usb_serial": self.usb_serial,
            }
