"""Opt-in, per-camera pipeline timings. File I/O stays off the pipeline threads."""

import atexit
import json
import math
import os
from collections import defaultdict
from threading import Event, Lock, Thread
import time

from phonecam.core.paths import _appdata_dir


class PipelineProfile:
    def __init__(self):
        self.lock = Lock()
        self.timings = defaultdict(list)
        self.counts = defaultdict(int)
        self.metadata = {}
        self.stop = Event()
        self.worker = Thread(target=self._write, name="phonecam-profile", daemon=True)
        self.worker.start()
        atexit.register(self.close)

    def add(self, slot, stage, elapsed_ms):
        with self.lock:
            self.timings[slot, stage].append(elapsed_ms)

    def count(self, slot, name, amount=1):
        if amount:
            with self.lock:
                self.counts[slot, name] += amount

    def describe(self, slot, **values):
        with self.lock:
            self.metadata[slot] = values

    def _snapshot(self, start, end):
        with self.lock:
            timings, self.timings = self.timings, defaultdict(list)
            counts, self.counts = self.counts, defaultdict(int)
            metadata = self.metadata.copy()
        slots = {slot for slot, _ in timings} | {slot for slot, _ in counts}
        rows = {slot: {"slot": slot, "settings": metadata.get(slot, {}), "ms": {}, "counts": {}}
                for slot in sorted(slots)}
        for (slot, stage), values in timings.items():
            values.sort()
            n = len(values)
            rows[slot]["ms"][stage] = {"n": n, "mean": round(sum(values) / n, 3),
                                        "p95": round(values[math.ceil(n * .95) - 1], 3),
                                        "max": round(values[-1], 3)}
        for (slot, name), count in counts.items():
            rows[slot]["counts"][name] = count
        return {"time": time.time(), "pid": os.getpid(), "seconds": round(end - start, 4),
                "cameras": list(rows.values())}

    def _write(self):
        last = time.monotonic()
        path = os.path.join(_appdata_dir(), "profile.log")
        while True:
            finished = self.stop.wait(1.0)
            now = time.monotonic()
            row = self._snapshot(last, now)
            last = now
            if row["cameras"]:
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "a", encoding="utf-8") as log:
                        print(json.dumps(row, ensure_ascii=False), file=log)
                except OSError:
                    pass
            if finished:
                return

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)


PROFILE = PipelineProfile() if os.environ.get("PHONECAM_PROFILE") == "1" else None
