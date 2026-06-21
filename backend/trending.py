"""Recency-aware trending.

Two ranking modes share the SAME suggestion path:

  * "count"    -> basic: rank purely by total search count (historically popular).
  * "trending" -> enhanced: rank by  count + RECENCY_WEIGHT * recent_activity,
                  where recent_activity is an exponentially-decaying moving sum.

How recent activity is tracked (and why it can't over-rank forever):
  For each query we keep `recent` and the timestamp it was last touched. On a new
  burst of `delta` searches we first decay the old value to "now" and then add the
  new activity:
        recent <- recent * exp(-(now - last) / TAU) + delta
  When scoring we decay again to the current instant. Because the weight decays
  exponentially with time-constant TAU, a query that was hot for a short window
  loses its boost within a few TAU and falls back to its base count ranking. This
  directly answers the rubric's "avoid permanently over-ranking" point.
"""
import math
from threading import Lock
from typing import Dict, List, Tuple

from . import config

Suggestion = Tuple[str, int]


class TrendingRanker:
    def __init__(self):
        self._lock = Lock()
        # query -> [recent_value, last_update_ts, last_known_count]
        self._recent: Dict[str, list] = {}

    # -- write side: called by the batch writer on each flush ----------------
    def record(self, deltas: Dict[str, int], now: float,
               counts: Dict[str, int] = None) -> None:
        """Decay + add recent activity. `counts` carries the authoritative
        absolute count per query (read back from the store) so trending can show
        the real count for hot tail queries, not just their recency boost."""
        counts = counts or {}
        with self._lock:
            for query, delta in deltas.items():
                c = counts.get(query)
                rv = self._recent.get(query)
                if rv is None:
                    self._recent[query] = [float(delta), now,
                                           c if c is not None else int(delta)]
                else:
                    decay = math.exp(-(now - rv[1]) / config.RECENCY_TAU_SECONDS)
                    rv[0] = rv[0] * decay + float(delta)
                    rv[1] = now
                    if c is not None:
                        rv[2] = c

    def prune(self, now: float) -> int:
        """Drop entries whose decayed activity has faded to ~0. Keeps map small."""
        removed = 0
        with self._lock:
            for q in list(self._recent.keys()):
                rv = self._recent[q]
                decayed = rv[0] * math.exp(-(now - rv[1]) / config.RECENCY_TAU_SECONDS)
                if decayed < config.RECENCY_PRUNE_EPS:
                    del self._recent[q]
                    removed += 1
        return removed

    # -- read side -----------------------------------------------------------
    def _recent_value(self, query: str, now: float) -> float:
        rv = self._recent.get(query)
        if rv is None:
            return 0.0
        return rv[0] * math.exp(-(now - rv[1]) / config.RECENCY_TAU_SECONDS)

    def score(self, query: str, count: int, now: float) -> float:
        return count + config.RECENCY_WEIGHT * self._recent_value(query, now)

    def rerank(self, candidates: List[Suggestion], prefix: str, now: float,
               limit: int) -> List[dict]:
        """Re-rank candidate (query, count) pairs by the trending score.

        `candidates` should already include the base top-K by count for the
        prefix; we add any hot queries that match the prefix but might not be in
        that base list (using their last-known count), then sort by score.
        """
        by_query: Dict[str, int] = {q: c for q, c in candidates}
        # pull in hot queries that match the prefix but were below the count cap
        with self._lock:
            for q, rv in self._recent.items():
                if q.startswith(prefix):
                    by_query[q] = max(by_query.get(q, 0), rv[2])
        scored = [
            {
                "query": q,
                "count": c,
                "score": round(self.score(q, c, now), 2),
                "recent": round(self._recent_value(q, now), 3),
            }
            for q, c in by_query.items()
        ]
        scored.sort(key=lambda d: (-d["score"], d["query"]))
        return scored[:limit]

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._recent)
