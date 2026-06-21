"""Prefix Trie with a cached top-K list at every node.

This is the in-memory serving index (the "primary store" for the read path).
Each node keeps the best `cap` completions (sorted by count desc) for the prefix
that ends at that node, so a lookup is O(len(prefix) + K): walk down to the node,
return its cached list. No scanning of the whole subtree at query time.

Correctness of the incremental top-K:
  Search counts only ever INCREASE (searches add, never subtract). At build time
  every completion is offered to every prefix node, and each node keeps the top
  `cap`, so node.top is exact. On an update, a query's count grows; we re-offer it
  to each prefix node. Because counts are monotonic, the only way a query enters a
  node's true top-K is by growing, which we always catch. Hence node.top stays
  exact for the top `cap` (>= the K we return).
"""
from threading import RLock
from typing import Dict, List, Optional, Tuple

Suggestion = Tuple[str, int]  # (query, count)


class _Node:
    __slots__ = ("children", "top")

    def __init__(self) -> None:
        self.children: Dict[str, "_Node"] = {}
        # top is a list of [count, query] kept sorted by count desc, len <= cap
        self.top: List[List] = []


class Trie:
    def __init__(self, cap: int = 10):
        self.cap = cap
        self.root = _Node()
        self._lock = RLock()
        self.size = 0  # number of distinct queries inserted

    # -- internal: offer (query, count) to a single node's top list ----------
    def _offer(self, node: _Node, query: str, count: int) -> None:
        top = node.top
        # update in place if the query is already tracked at this node
        for entry in top:
            if entry[1] == query:
                entry[0] = count
                top.sort(key=lambda e: (-e[0], e[1]))
                return
        if len(top) < self.cap:
            top.append([count, query])
            top.sort(key=lambda e: (-e[0], e[1]))
        elif count > top[-1][0]:
            top[-1] = [count, query]
            top.sort(key=lambda e: (-e[0], e[1]))

    def insert(self, query: str, count: int) -> None:
        """Insert or overwrite a query with an absolute count."""
        with self._lock:
            node = self.root
            self._offer(node, query, count)          # root holds global top-K
            for ch in query:
                nxt = node.children.get(ch)
                if nxt is None:
                    nxt = _Node()
                    node.children[ch] = nxt
                node = nxt
                self._offer(node, query, count)
            self.size += 1

    def bulk_load(self, rows) -> None:
        """Load many (query, count) pairs efficiently."""
        for query, count in rows:
            self.insert(query, count)

    def update(self, query: str, count: int) -> None:
        """Re-offer a query (with its new, larger count) along its prefixes."""
        with self._lock:
            node = self.root
            self._offer(node, query, count)
            for ch in query:
                nxt = node.children.get(ch)
                if nxt is None:
                    nxt = _Node()
                    node.children[ch] = nxt
                node = nxt
                self._offer(node, query, count)

    def _find(self, prefix: str) -> Optional[_Node]:
        node = self.root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return None
        return node

    def suggest(self, prefix: str, limit: int = 10) -> List[Suggestion]:
        """Return up to `limit` completions of `prefix`, sorted by count desc."""
        with self._lock:
            node = self._find(prefix)
            if node is None:
                return []
            return [(q, c) for c, q in node.top[:limit]]

    def top_overall(self, limit: int = 10) -> List[Suggestion]:
        return self.suggest("", limit)
