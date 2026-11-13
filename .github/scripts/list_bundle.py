# -*- coding: utf-8 -*-
"""PyInstaller の onefile exe の中身を、圧縮後のサイズ順に並べる (CI のログで同梱物を確認するための診断用)。

    python .github/scripts/list_bundle.py windows/dist/PhoneCamReceiver.exe [上位の件数]
"""
import sys
from collections import defaultdict

from PyInstaller.archive.readers import CArchiveReader


def main():
    exe = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    reader = CArchiveReader(exe)
    rows = []
    for name, ent in reader.toc.items():
        pos, dlen, ulen, flag, typ = ent[:5]
        rows.append((dlen, ulen, name.replace("\\", "/"), typ))
    rows.sort(reverse=True)
    mb = 1048576.0
    print("entries: %d, compressed %.1f MB, uncompressed %.1f MB" % (
        len(rows), sum(r[0] for r in rows) / mb, sum(r[1] for r in rows) / mb))
    print("---- top %d (compressed MB / uncompressed MB / type / name) ----" % top)
    for dlen, ulen, name, typ in rows[:top]:
        print("%7.2f %7.2f  %-3s %s" % (dlen / mb, ulen / mb, typ, name))
    groups = defaultdict(lambda: [0, 0, 0])
    for dlen, ulen, name, typ in rows:
        parts = name.split("/")
        if parts[0] in ("PySide6", "PIL", "numpy", "av", "tcl8.6", "tk8.6", "_tcl_data", "_tk_data") and len(parts) > 2:
            key = "/".join(parts[:2])
        elif len(parts) > 1:
            key = parts[0]
        elif name.lower().endswith((".dll", ".pyd")):
            key = "(top-level dll/pyd)"
        else:
            key = "(top-level other)"
        g = groups[key]
        g[0] += dlen
        g[1] += ulen
        g[2] += 1
    print("---- by group (compressed MB / uncompressed MB / count) ----")
    for key, g in sorted(groups.items(), key=lambda kv: -kv[1][0])[:45]:
        print("%7.2f %7.2f %4d  %s" % (g[0] / mb, g[1] / mb, g[2], key))
    print("---- top-level dll/pyd, translations, plugins ----")
    for dlen, ulen, name, typ in sorted(rows, key=lambda r: r[2]):
        n = name.lower()
        if ("/" not in n and n.endswith((".dll", ".pyd"))) or "translations" in n or "/plugins/" in n:
            print("%7.3f %7.3f  %s" % (dlen / mb, ulen / mb, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
