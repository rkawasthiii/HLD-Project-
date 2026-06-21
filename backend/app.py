"""Application context: wires every component together and holds the business
logic for each endpoint. The HTTP layer (server.py) only does routing/IO and
calls into here, so the core logic is easy to read, test, and explain.
"""
import time
from typing import Dict, List

from . import config, ingest
from .batch_writer import BatchWriter
from .cache import DistributedCache
from .metrics import Metrics
from .store import PrimaryStore
from .trending import TrendingRanker
from .trie import Trie


class App:
    def __init__(self):
        self.metrics = Metrics()
        self.store = PrimaryStore()
        self.trie = Trie(cap=config.TRIE_CAP)
        self.cache = DistributedCache()
        self.trending = TrendingRanker()
        self.batch = BatchWriter(self.store, self.trie, self.trending,
                                 self.cache, self.metrics)

    # -- lifecycle -----------------------------------------------------------
    def startup(self) -> None:
        ingest.ingest_if_needed(self.store)
        ingest.build_trie(self.store, self.trie)
        self.batch.start()
        print(f"[app] ready: {self.store.count_rows():,} queries, "
              f"cache nodes={config.CACHE_NODES}")

    def shutdown(self) -> None:
        self.batch.stop()
        self.store.close()

    # -- /suggest ------------------------------------------------------------
    def suggest(self, prefix: str, mode: str = "count",
                limit: int = config.SUGGEST_LIMIT) -> Dict:
        """Cache-first prefix suggestions. mode = 'count' | 'trending'."""
        t0 = time.perf_counter()
        prefix = (prefix or "").strip().lower()
        mode = mode if mode in ("count", "trending") else "count"
        limit = max(1, min(limit, config.SUGGEST_LIMIT))

        cached = self.cache.get(prefix, mode)
        if cached is not None:
            self.metrics.record_latency("suggest", (time.perf_counter() - t0) * 1000)
            return {"prefix": prefix, "mode": mode, "source": "cache",
                    "suggestions": cached[:limit]}

        # cache miss -> fall back to the primary in-memory index (the Trie)
        self.metrics.incr("store_reads")
        base = self.trie.suggest(prefix, config.TRIE_CAP)
        if mode == "trending":
            now = time.time()
            ranked = self.trending.rerank(base, prefix, now, config.TRIE_CAP)
            suggestions = ranked
        else:
            suggestions = [{"query": q, "count": c} for q, c in base]

        self.cache.set(prefix, mode, suggestions)
        self.metrics.record_latency("suggest", (time.perf_counter() - t0) * 1000)
        return {"prefix": prefix, "mode": mode, "source": "store",
                "suggestions": suggestions[:limit]}

    # -- /search -------------------------------------------------------------
    def search(self, query: str) -> Dict:
        """Dummy search endpoint: record the submission, return 'Searched'."""
        t0 = time.perf_counter()
        query = (query or "").strip()
        if not query:
            return {"message": "Searched", "query": "", "recorded": False}
        self.batch.submit(query)  # buffered, no DB write here
        self.metrics.record_latency("search", (time.perf_counter() - t0) * 1000)
        return {"message": "Searched", "query": query, "recorded": True}

    # -- /trending -----------------------------------------------------------
    def trending_now(self, limit: int = config.SUGGEST_LIMIT) -> Dict:
        now = time.time()
        base = self.trie.top_overall(config.TRIE_CAP)
        ranked = self.trending.rerank(base, "", now, limit)
        return {"mode": "trending", "suggestions": ranked,
                "tracked_recent_queries": self.trending.tracked_count()}

    # -- /cache/debug --------------------------------------------------------
    def cache_debug(self, prefix: str, mode: str = "count") -> Dict:
        prefix = (prefix or "").strip().lower()
        info = self.cache.debug(prefix, mode)
        # sample a few prefixes to show how keys spread across nodes
        sample = ["a", "b", "c", "i", "iphone", "java", "amazon", "best", "how",
                  "the", "new", "buy", "free", "online", "near"]
        info["sample_distribution"] = self.cache.distribution(sample, mode)
        info["ring_nodes"] = self.cache.ring.nodes
        info["vnodes_per_node"] = config.VNODES_PER_NODE
        return info

    # -- /metrics ------------------------------------------------------------
    def metrics_report(self) -> Dict:
        return {
            "latency": self.metrics.latency_report(),
            "cache": self.cache.hit_stats(),
            "batch": self.batch.stats(),
            "store": self.store.stats(),
            "trie_size": self.trie.size,
            "counters": dict(self.metrics.counters),
        }
