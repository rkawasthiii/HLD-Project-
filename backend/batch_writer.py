"""Batch writer: turn many per-search writes into few aggregated DB writes.

POST /search does NOT touch the database. It just adds +1 to an in-memory buffer
and returns immediately ("Searched"). A background thread flushes the buffer to
SQLite either every BATCH_FLUSH_INTERVAL seconds or once BATCH_MAX_SIZE distinct
submissions accumulate. Repeated queries are aggregated, so 1000 searches for
"iphone" become a single "+1000" upsert.

On flush we, in order:
  1. snapshot + clear the buffer (so new searches keep arriving),
  2. write aggregated deltas to SQLite in one transaction,
  3. refresh the Trie's cached top-K for the affected queries,
  4. feed the recency tracker so trending updates,
  5. invalidate cached suggestion entries for the affected prefixes.

Failure trade-off (discussed in the README): submissions buffered but not yet
flushed are lost if the process crashes. The exposure is bounded by the flush
interval / batch size, which trades a little durability for a large reduction in
write amplification. A write-ahead log on the buffer would close this gap at the
cost of complexity.
"""
import threading
import time
from typing import Dict

from . import config


class BatchWriter:
    def __init__(self, store, trie, trending, cache, metrics):
        self.store = store
        self.trie = trie
        self.trending = trending
        self.cache = cache
        self.metrics = metrics

        self._buffer: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                         name="batch-writer")
        # stats
        self.submissions = 0   # total /search calls accepted
        self.flushes = 0       # number of flush cycles that wrote anything

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.flush()  # best-effort final flush

    def submit(self, query: str) -> None:
        """Record a search submission. O(1), no DB access."""
        query = query.strip().lower()
        if not query:
            return
        with self._lock:
            self._buffer[query] = self._buffer.get(query, 0) + 1
            self.submissions += 1
            buffered = len(self._buffer)
        self.metrics.incr("search_submissions")
        if buffered >= config.BATCH_MAX_SIZE:
            self.flush()  # size-triggered flush

    def _drain(self) -> Dict[str, int]:
        with self._lock:
            if not self._buffer:
                return {}
            deltas = self._buffer
            self._buffer = {}
            return deltas

    def flush(self) -> int:
        """Flush buffered submissions to the store. Returns rows written."""
        deltas = self._drain()
        if not deltas:
            return 0
        now = time.time()
        # 1+2: single aggregated transaction -> authoritative new counts
        new_counts = self.store.flush_deltas(deltas)
        counts_map = dict(new_counts)
        # 3: refresh in-memory serving index
        for query, count in new_counts:
            self.trie.update(query, count)
        # 4: recency tracker for trending (with authoritative counts)
        self.trending.record(deltas, now, counts_map)
        # 5: invalidate affected cached prefixes
        prefixes = set()
        for query in deltas:
            for i in range(1, min(len(query), 25) + 1):
                prefixes.add(query[:i])
            prefixes.add("")  # global/trending list may change too
        self.cache.invalidate_prefixes(prefixes)
        self.trending.prune(now)

        self.flushes += 1
        self.metrics.incr("batch_flushes")
        self.metrics.incr("queries_flushed", by=len(deltas))
        return len(new_counts)

    def _run(self) -> None:
        while not self._stop.wait(config.BATCH_FLUSH_INTERVAL):
            try:
                self.flush()  # time-triggered flush
            except Exception as exc:  # keep the thread alive on errors
                print(f"[batch-writer] flush error: {exc}")

    def stats(self) -> Dict:
        with self._lock:
            buffered = len(self._buffer)
        db = self.store.stats()
        # write reduction = submissions handled per physical write transaction
        reduction = (self.submissions / db["write_ops"]) if db["write_ops"] else 0
        return {
            "submissions": self.submissions,
            "buffered": buffered,
            "flushes": self.flushes,
            "db_write_ops": db["write_ops"],
            "db_rows_written": db["rows_written"],
            "write_reduction_x": round(reduction, 2),
        }
