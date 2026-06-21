"""Lightweight metrics: per-endpoint latency percentiles + counters.

Kept in-process and dependency-free. Latency samples are stored in a bounded
deque per endpoint so memory stays constant; percentiles are computed on demand.
"""
from collections import deque
from threading import Lock
from typing import Deque, Dict

from . import config


class Metrics:
    def __init__(self, window: int = config.LATENCY_WINDOW):
        self.window = window
        self._lock = Lock()
        self._latencies: Dict[str, Deque[float]] = {}
        self.counters: Dict[str, int] = {}

    def record_latency(self, endpoint: str, millis: float) -> None:
        with self._lock:
            dq = self._latencies.get(endpoint)
            if dq is None:
                dq = deque(maxlen=self.window)
                self._latencies[endpoint] = dq
            dq.append(millis)

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + by

    @staticmethod
    def _percentile(sorted_vals, pct: float) -> float:
        if not sorted_vals:
            return 0.0
        k = (len(sorted_vals) - 1) * (pct / 100.0)
        lo = int(k)
        hi = min(lo + 1, len(sorted_vals) - 1)
        frac = k - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    def latency_report(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            report = {}
            for ep, dq in self._latencies.items():
                vals = sorted(dq)
                report[ep] = {
                    "count": len(vals),
                    "p50_ms": round(self._percentile(vals, 50), 3),
                    "p95_ms": round(self._percentile(vals, 95), 3),
                    "p99_ms": round(self._percentile(vals, 99), 3),
                    "max_ms": round(vals[-1], 3) if vals else 0.0,
                }
            return report

    def snapshot(self) -> Dict:
        return {
            "latency": self.latency_report(),
            "counters": dict(self.counters),
        }
