# -*- coding: utf-8 -*-
"""クラッシュの記録: %APPDATA%/PhoneCam に Python の例外 (phonecam_error.log) とネイティブのクラッシュ (phonecam_crash.log) を残す。
--windowed の exe には画面もコンソールも無いので、落ちた理由を後から見られるようにする。ここで例外は出さない。
"""

import os
import sys
import threading
import time
import traceback
from phonecam.core.paths import _appdata_dir

ERROR_LOG = "phonecam_error.log"
CRASH_LOG = "phonecam_crash.log"
CONNECTION_LOG = "phonecam_connection.log"
CONNECTION_REPEAT_SEC = 60.0
MAX_BYTES = 512 * 1024

_crash_file = None


def _path(name):
    return os.path.join(_appdata_dir(), name)


def log_error(tag, exc_text=None):
    """例外の詳細を phonecam_error.log に追記する (サイズが大きくなったら古い分を捨てる)。"""
    try:
        d = _appdata_dir()
        os.makedirs(d, exist_ok=True)
        p = _path(ERROR_LOG)
        try:
            if os.path.getsize(p) > MAX_BYTES:
                os.replace(p, p + ".1")
        except OSError:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), tag, exc_text or traceback.format_exc()))
    except Exception:
        pass


_conn_lock = threading.Lock()
_conn_last = None      # (直前に書いた文, 書いた時刻, 省略した回数)


def log_connection(event, now=None):
    """接続の切り替え・切断・失敗の理由を 1 行ずつ phonecam_connection.log に残す。
    直前と同じ文が 60 秒以内に続くときは書かず、次に違う文を書くとき「同じ理由を N 回省略」と添える。ここで例外は出さない。"""
    global _conn_last
    try:
        now = time.monotonic() if now is None else now
        with _conn_lock:
            last = _conn_last
            lines = []
            if last is not None and last[0] == event and now - last[1] < CONNECTION_REPEAT_SEC:
                _conn_last = (event, last[1], last[2] + 1)
                return
            if last is not None and last[2]:
                lines.append("(直前と同じ理由を %d 回省略)" % last[2])
            lines.append(event)
            _conn_last = (event, now, 0)
            os.makedirs(_appdata_dir(), exist_ok=True)
            p = _path(CONNECTION_LOG)
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    os.replace(p, p + ".1")
            except OSError:
                pass
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(p, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write("[%s] %s\n" % (stamp, line))
    except Exception:
        pass


def install(root=None):
    """faulthandler (ネイティブのクラッシュ)・未処理例外・Tk コールバックの例外を記録する。"""
    global _crash_file
    try:
        import faulthandler
        os.makedirs(_appdata_dir(), exist_ok=True)
        _crash_file = open(_path(CRASH_LOG), "a", encoding="utf-8")
        _crash_file.write("--- %s 起動 ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        # faulthandler は Windows では、処理済みの例外 (0x8001010d = COM の RPC_E_CANTCALLOUT_ININPUTSYNCCALL など) も
        # 「Windows fatal exception」として書く。本当のクラッシュは access violation や Fatal Python error で、
        # そのあとに下の「正常終了」の印が無いもの。
        _crash_file.write("(note) 'Windows fatal exception: code 0x...' alone can be a handled first-chance exception; "
                          "a real crash has no '正常終了' line after it)\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:
        pass

    def _sys_hook(et, ev, tb):
        log_error("uncaught", "".join(traceback.format_exception(et, ev, tb)))

    def _thread_hook(args):
        log_error("thread %s" % getattr(args.thread, "name", "?"),
                  "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    try:
        sys.excepthook = _sys_hook
        threading.excepthook = _thread_hook
    except Exception:
        pass
    if root is not None:
        try:
            root.report_callback_exception = lambda et, ev, tb: log_error(
                "tk callback", "".join(traceback.format_exception(et, ev, tb)))
        except Exception:
            pass


def mark_exit():
    """アプリが普通に終わったことを crash.log に残す (この印が無いまま次の「起動」が来たら、前回は落ちている)。"""
    try:
        if _crash_file is not None:
            _crash_file.write("--- %s 正常終了 ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            _crash_file.flush()
    except Exception:
        pass
