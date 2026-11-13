# -*- coding: utf-8 -*-
"""pyi-makespec が作った .spec に「不要な同梱物を取り除く」処理を差し込む。

    python .github/scripts/slim_spec.py windows/PhoneCamReceiver.spec

PyInstaller の --exclude-module はモジュールしか除けない (Qt の DLL・プラグイン・翻訳は除けない) ので、
Analysis の結果 (a.binaries / a.datas) から名前で取り除く。ここに挙げたものはこのアプリのコードが一切使わない:
  - opengl32sw.dll      ソフトウェア OpenGL (QOpenGL* / Qt Quick を使わない Widgets アプリには不要)
  - Qt6Quick / Qt6Qml / Qt6QmlModels / Qt6VirtualKeyboard とそのプラグイン (仮想キーボードのプラグインが引き込んでいた)
  - Qt6Pdf と PDF の画像プラグイン
  - Qt の TLS プラグイン (QSslSocket を使わない) と、それが引き込んでいた Git for Windows の OpenSSL (-x64 付きの方)
    Python 本体の OpenSSL (libcrypto-3.dll / libssl-3.dll) は残す (更新の確認の https で使う)
  - qdirect2d (Direct2D の代替プラットフォーム。通常は qwindows を使う)
  - Qt の翻訳 (QTranslator を使っていない)
  - Pillow の AVIF (_avif。AvifImagePlugin は ImportError を捕まえて無効になるだけ)
"""
import sys

DROP_BASENAMES = (
    "opengl32sw.dll",
    "qt6quick.dll", "qt6qml.dll", "qt6qmlmodels.dll", "qt6virtualkeyboard.dll",
    "qtvirtualkeyboardplugin.dll",
    "qt6pdf.dll", "qpdf.dll",
    "libcrypto-3-x64.dll", "libssl-3-x64.dll",
    "qdirect2d.dll",
)
DROP_PREFIXES = (
    "pyside6/plugins/tls/",
    "pyside6/translations/",
    "pil/_avif",
)

BLOCK = '''
# ---- slim_spec.py: 不要な同梱物を取り除く ----
_DROP_BASENAMES = %r
_DROP_PREFIXES = %r


def _slim_drop(entry):
    name = entry[0].replace("\\\\", "/").lower()
    return name.rsplit("/", 1)[-1] in _DROP_BASENAMES or name.startswith(_DROP_PREFIXES)


_dropped = [e[0] for e in list(a.binaries) + list(a.datas) if _slim_drop(e)]
a.binaries = [e for e in a.binaries if not _slim_drop(e)]
a.datas = [e for e in a.datas if not _slim_drop(e)]
print("slim_spec: dropped %%d entries" %% len(_dropped))
for _n in sorted(_dropped):
    print("  - " + _n)
# ---------------------------------------------
''' % (DROP_BASENAMES, DROP_PREFIXES)


def main():
    path = sys.argv[1]
    text = open(path, encoding="utf-8").read()
    marker = "\npyz = PYZ("
    if marker not in text:
        raise SystemExit("pyz = PYZ( が見つかりません: " + path)
    if "slim_spec.py" in text:
        raise SystemExit("すでに差し込み済みです")
    text = text.replace(marker, BLOCK + marker, 1)
    open(path, "w", encoding="utf-8").write(text)
    print("slim_spec: patched " + path)


if __name__ == "__main__":
    main()
