"""GPU 메모리 표본 (nvidia-smi). 실행하는 동안 0.5초마다 사용량을 적는다."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time


def used_mib() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.strip().splitlines()[0])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


def ollama_ps() -> str:
    if not shutil.which("ollama"):
        return ""
    try:
        return subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout.strip()
    except subprocess.SubprocessError:
        return ""


class Sampler:
    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.samples: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()

        def loop():
            while not self._stop.is_set():
                v = used_mib()
                if v is not None:
                    self.samples.append((round(time.perf_counter() - self._t0, 1), v))
                self._stop.wait(self.interval)
        self._th = threading.Thread(target=loop, daemon=True)
        self._th.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._th.join(2)

    def summary(self) -> dict:
        vals = [v for _, v in self.samples]
        return {"min_mib": min(vals), "max_mib": max(vals), "samples": len(vals)} if vals else {}
