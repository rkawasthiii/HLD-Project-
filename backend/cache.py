"""Distributed suggestion cache.

Multiple *logical* cache nodes live in this process (each its own dict + TTL),
standing in for separate cache servers (e.g. a Redis cluster). A consistent-hash
ring decides which node owns a given prefix key, so the read path is:

    key = f"{mode}:{prefix}"
    node = ring.get_node(key)        # consistent hashing
    hit  = node.get(key)             # per-node TTL cache

Each node tracks hits/misses so we can report a cache hit rate, and entries
expire after a TTL so stale suggestions don't live forever. On a batch flush we
also actively invalidate the prefixes whose counts changed.
"""
import time
from threading import Lock
from typing import Dict, List, Optional, Tuple

from . import config
from .consistent_hash import ConsistentHashRing


class CacheNode:
    def __init__(self, name: str, ttl: float, max_keys: int):
        self.name = name
        self.ttl = ttl
        self.max_keys = max_keys
        self._store: Dict[str, Tuple[float, object]] = {}  # key -> (expiry, value)
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expiry, value = entry
            if expiry < time.time():
                del self._store[key]            # lazy expiry
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value) -> None:
        with self._lock:
            if len(self._store) >= self.max_keys and key not in self._store:
                # simple eviction: drop the soonest-to-expire entry
                oldest = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest]
            self._store[key] = (time.time() + self.ttl, value)

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def size(self) -> int:
        with self._lock:
            return len(self._store)


class DistributedCache:
    def __init__(self):
        self.ring = ConsistentHashRing(config.CACHE_NODES, config.VNODES_PER_NODE)
        self.nodes: Dict[str, CacheNode] = {
            name: CacheNode(name, config.CACHE_TTL_SECONDS, config.CACHE_MAX_KEYS)
            for name in config.CACHE_NODES
        }

    @staticmethod
    def make_key(prefix: str, mode: str) -> str:
        return f"{mode}:{prefix}"

    def node_for(self, key: str) -> CacheNode:
        return self.nodes[self.ring.get_node(key)]

    def get(self, prefix: str, mode: str):
        key = self.make_key(prefix, mode)
        return self.node_for(key).get(key)

    def set(self, prefix: str, mode: str, value) -> None:
        key = self.make_key(prefix, mode)
        self.node_for(key).set(key, value)

    def invalidate_prefixes(self, prefixes, modes=("count", "trending")) -> int:
        """Delete cached entries for the given prefixes across all modes."""
        removed = 0
        for p in prefixes:
            for mode in modes:
                key = self.make_key(p, mode)
                if self.node_for(key).delete(key):
                    removed += 1
        return removed

    # ---- reporting ---------------------------------------------------------
    def hit_stats(self) -> Dict:
        total_hits = sum(n.hits for n in self.nodes.values())
        total_miss = sum(n.misses for n in self.nodes.values())
        total = total_hits + total_miss
        return {
            "hits": total_hits,
            "misses": total_miss,
            "hit_rate": round(total_hits / total, 4) if total else 0.0,
            "per_node": {
                n.name: {"hits": n.hits, "misses": n.misses, "keys": n.size()}
                for n in self.nodes.values()
            },
        }

    def debug(self, prefix: str, mode: str = "count") -> Dict:
        """Explain routing for a prefix: which node owns it and hit/miss state."""
        key = self.make_key(prefix, mode)
        node = self.node_for(key)
        key_hash, point_hash = self.ring.ring_points(key)
        present = node.get(key) is not None  # note: counts as a hit/miss probe
        return {
            "prefix": prefix,
            "mode": mode,
            "cache_key": key,
            "owner_node": node.name,
            "key_hash": key_hash,
            "ring_point_hash": point_hash,
            "state": "HIT" if present else "MISS",
            "node_keys": node.size(),
        }

    def distribution(self, sample_prefixes: List[str], mode: str = "count") -> Dict:
        keys = [self.make_key(p, mode) for p in sample_prefixes]
        return self.ring.distribution(keys)
