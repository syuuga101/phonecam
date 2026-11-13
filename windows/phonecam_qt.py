# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ (Qt UI の入口)

phonecam_receiver.py (Tk 版) は全ページの移植が終わるまで配布の入口のまま。
--register / --unregister / --setup のヘルパ起動は Tk 版と同じ (phonecam.core.softcam_reg.helper_main)。
"""

import sys

if __name__ == "__main__":
    try:
        from phonecam.qt.app import main
        code = main()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "PhoneCam を起動できませんでした。\n\n" + detail[-1500:], "PhoneCam", 0x10)
        except Exception:
            sys.stderr.write(detail)
        code = 1
    sys.exit(code)
