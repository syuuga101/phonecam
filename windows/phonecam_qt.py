# -*- coding: utf-8 -*-
"""PhoneCam Windows 受信アプリ (Qt UI の入口)

--register / --unregister / --setup <job> / --camera-slot N の管理者ヘルパ起動は phonecam.core.softcam_reg.helper_main が処理する。
"""

import sys


def _selftest():
    """--selftest: 同梱した主要ライブラリの import と QR・QImage の生成を確かめ、結果を selftest.txt に書く (CI の起動テスト用)。"""
    import os
    lines = []
    code = 0
    try:
        import av, numpy, zeroconf, qrcode, PIL.Image, PIL.ImageDraw  # noqa: F401
        lines.append("imports ok: av %s numpy %s qrcode PIL %s" % (av.__version__, numpy.__version__, PIL.__version__))
        # 通信の暗号 (v2): X25519・HKDF・ChaCha20-Poly1305 が同梱されているか、Windows では DPAPI も往復させる
        from phonecam.core import secure
        a, b = os.urandom(32), os.urandom(32)
        if secure.x25519(a, secure.x25519_public(b)) != secure.x25519(b, secure.x25519_public(a)):
            raise RuntimeError("x25519 mismatch")
        k = secure.hkdf(b"selftest", b"salt", b"info", 32)
        ch = secure.Channel(k, b"AAAA", k, b"AAAA")
        if ch.open(ch.seal(1, b"hello")[5:])[1] != b"hello":
            raise RuntimeError("aead round trip failed")
        if sys.platform == "win32":
            if secure.unprotect_secret(*secure.protect_secret(b"x" * 32)) != b"x" * 32:
                raise RuntimeError("dpapi round trip failed")
        lines.append("crypto ok")
        qr = qrcode.QRCode(border=1)
        qr.add_data("http://192.168.0.1:8080")
        qr.make(fit=True)
        img = qr.make_image().convert("RGB")
        lines.append("qr ok: %dx%d" % img.size)
        from PySide6.QtGui import QImage, QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
        if qimg.isNull():
            raise RuntimeError("QImage is null")
        lines.append("qimage ok")
        from phonecam.qt.app import main  # noqa: F401  アプリ本体の import (Qt の Widgets など)
        lines.append("app import ok")
    except Exception:
        import traceback
        lines.append("Traceback (most recent call last):\n" + traceback.format_exc())
        code = 1
    text = "\n".join(lines)
    try:
        from phonecam.core.paths import _appdata_dir
        d = _appdata_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return code


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--selftest"]:
            sys.exit(_selftest())
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
