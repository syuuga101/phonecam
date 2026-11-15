# -*- coding: utf-8 -*-
"""通信の暗号化 (v2) の 1 フレームあたりの時間を測る。1080p・20 Mbps・60 fps ≒ 1 フレーム約 40 KB。
使い方: python tools/bench_secure.py   (CI の windows-latest でも実行して、PR に値を書く)"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from phonecam.core import secure  # noqa: E402


def main(size=40_000, rounds=2000):
    k1, k2 = os.urandom(32), os.urandom(32)
    pc = secure.Channel(k1, b"AAAA", k2, b"BBBB")
    ph = secure.Channel(k2, b"BBBB", k1, b"AAAA")
    frame = os.urandom(size)
    seal, opened = [], []
    for _ in range(rounds):
        t0 = time.perf_counter()
        sealed = pc.seal(1, frame)
        t1 = time.perf_counter()
        ph.open(sealed[5:])
        t2 = time.perf_counter()
        seal.append(t1 - t0)
        opened.append(t2 - t1)
    for name, values in (("暗号化", seal), ("復号", opened)):
        values.sort()
        print("%s: 平均 %.3f ms / 中央値 %.3f ms / 99%% %.3f ms (%d バイト x %d)" % (
            name, statistics.mean(values) * 1000, statistics.median(values) * 1000,
            values[int(len(values) * 0.99)] * 1000, size, rounds))
    total = (statistics.mean(seal) + statistics.mean(opened)) * 1000
    print("1 フレームの暗号化 + 復号: %.3f ms (目標 1 ms 未満) / 60 fps の 1 コアあたり %.1f%%" % (total, total * 60 / 10))
    return 0 if total < 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
