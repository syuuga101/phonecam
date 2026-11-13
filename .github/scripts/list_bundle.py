# -*- coding: utf-8 -*-
"""PyInstaller が exe に入れたファイルをサイズ順に並べる (CI のログで同梱物を確認するための診断用)。

    python .github/scripts/list_bundle.py windows/build/PhoneCamReceiver [上位の件数]
"""
import ast
import os
import sys
from collections import defaultdict


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.literal_eval(f.read())


def main():
    base = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    rows = []
    toc = os.path.join(base, "PKG-00.toc")
    if not os.path.exists(toc):
        print("PKG-00.toc がありません:", toc)
        return 0
    data = load(toc)
    if isinstance(data, dict):
        print("PKG-00.toc は dict (keys: %s)" % list(data.keys())[:10])
        data = data.get("toc") or data.get("entries") or list(data.values())[0] if data else []
    if data:
        print("sample entry:", repr(list(data)[0])[:300])
    for ent in data:
        if isinstance(ent, dict):
            name = ent.get("dest_name") or ent.get("name")
            src = ent.get("src_name") or ent.get("src") or ent.get("path")
            typ = ent.get("typecode") or ent.get("type") or ""
        else:
            name, src, typ = ent[0], ent[1], ent[2]
        if typ in ("PYMODULE", "PYSOURCE", "PYZ") or not src or not os.path.isfile(src):
            continue
        rows.append((os.path.getsize(src), name, typ))
    rows.sort(reverse=True)
    total = sum(r[0] for r in rows)
    print("files: %d, total (uncompressed, excluding .pyc): %.1f MB" % (len(rows), total / 1048576))
    print("---- top %d ----" % top)
    for size, name, typ in rows[:top]:
        print("%8.2f MB  %-8s %s" % (size / 1048576, typ, name))
    groups = defaultdict(int)
    for size, name, typ in rows:
        parts = name.replace("\\", "/").split("/")
        key = "/".join(parts[:3]) if parts[0] == "PySide6" else parts[0]
        groups[key] += size
    print("---- by group ----")
    for key, size in sorted(groups.items(), key=lambda kv: -kv[1])[:40]:
        print("%8.2f MB  %s" % (size / 1048576, key))
    print("---- PySide6 dll/pyd names ----")
    for size, name, typ in rows:
        n = name.replace("\\", "/")
        if n.startswith("PySide6/") and n.lower().endswith((".dll", ".pyd")):
            print("%8.2f MB  %s" % (size / 1048576, n))
    print("---- translations / plugins ----")
    for size, name, typ in sorted(rows, key=lambda r: r[1]):
        n = name.replace("\\", "/").lower()
        if "translations" in n or "/plugins/" in n:
            print("%8.3f MB  %s" % (size / 1048576, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
