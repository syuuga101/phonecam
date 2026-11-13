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


def install(root=None):
    """faulthandler (ネイティブのクラッシュ)・未処理例外・Tk コールバックの例外を記録する。"""
    global _crash_file
    try:
        import faulthandler
        os.makedirs(_appdata_dir(), exist_ok=True)
        _crash_file = open(_path(CRASH_LOG), "a", encoding="utf-8")
        _crash_file.write("--- %s 起動 ---\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
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
