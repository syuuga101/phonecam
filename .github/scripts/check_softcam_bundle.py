# -*- coding: utf-8 -*-
"""exe の中に、カメラ 1〜4 の softcam DLL (64bit と 32bit) と softcam.rev が入っているか確かめる (CI の起動テスト用)。

    python .github/scripts/check_softcam_bundle.py windows/dist/PhoneCamReceiver.exe

足りないものがあれば、名前を出して終了コード 1 にする (slim_spec の取り除きや --add-binary の漏れで、
カメラ 2 以降が OBS に出なくなるのを、配布前に止める)。32bit は CI で作れなかったときだけ無いので、警告にとどめる。
"""
import sys

from PyInstaller.archive.readers import CArchiveReader


def main():
    reader = CArchiveReader(sys.argv[1])
    names = {name.replace("\\", "/").lower() for name in reader.toc}
    missing = []
    for slot in (1, 2, 3, 4):
        folder = "softcam" if slot == 1 else "softcam/%d" % slot
        if folder + "/softcam.dll" not in names:
            missing.append("%s/softcam.dll (camera %d, x64)" % (folder, slot))
        if folder + "/softcam32.dll" not in names:
            print("::warning::%s/softcam32.dll (camera %d, x86) is not bundled" % (folder, slot))
    if "softcam/softcam.rev" not in names:
        missing.append("softcam/softcam.rev")
    for name in missing:
        print("::error::missing from exe: %s" % name)
    if missing:
        return 1
    print("softcam bundle ok: cameras 1-4 x64 DLLs and softcam.rev are in the exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
