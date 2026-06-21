"""Consistent hashing ring with virtual nodes.

Used to decide which logical cache node owns a given prefix key. Virtual nodes
(replicas) spread each physical node across many points on the ring so that keys
distribute evenly and only a small fraction of keys move when a node is
added/removed.

The ring is a sorted list of (hash, node_name) points. To find the owner of a
key we hash the key and walk clockwise to the first point with hash >= key_hash
(wrapping around at the end) via binary search.
"""
import bisect
import hashlib
from typing import Dict, List, Tuple


def _hash(value: str) -> int:
    """Stable 64-bit hash (md5 is not for security here, just distribution)."""
    digest = hashlib.md5(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class ConsistentHashRing:
    def __init__(self, nodes: List[str], vnodes_per_node: int = 150):
        self.vnodes_per_node = vnodes_per_node
        self._ring_hashes: List[int] = []           # sorted hash points
        self._ring_nodes: List[str] = []            # node name per point
        self._nodes: List[str] = []
        for node in nodes:
            self.add_node(node)

    def add_node(self, node: str) -> None:
        if node in self._nodes:
            return
        self._nodes.append(node)
        for v in range(self.vnodes_per_node):
            h = _hash(f"{node}#{v}")
            pos = bisect.bisect(self._ring_hashes, h)
            self._ring_hashes.insert(pos, h)
            self._ring_nodes.insert(pos, node)

    def remove_node(self, node: str) -> None:
        if node not in self._nodes:
            return
        self._nodes.remove(node)
        keep_h: List[int] = []
        keep_n: List[str] = []
        for h, n in zip(self._ring_hashes, self._ring_nodes):
            if n != node:
                keep_h.append(h)
                keep_n.append(n)
        self._ring_hashes, self._ring_nodes = keep_h, keep_n

    def get_node(self, key: str) -> str:
        """Return the node responsible for `key`."""
        if not self._ring_hashes:
            raise RuntimeError("hash ring is empty")
        h = _hash(key)
        pos = bisect.bisect(self._ring_hashes, h)
        if pos == len(self._ring_hashes):
            pos = 0  # wrap around the ring
        return self._ring_nodes[pos]

    @property
    def nodes(self) -> List[str]:
        return list(self._nodes)

    def distribution(self, sample_keys: List[str]) -> Dict[str, int]:
        """Count how a sample of keys maps across nodes (for debugging/demo)."""
        counts: Dict[str, int] = {n: 0 for n in self._nodes}
        for k in sample_keys:
            counts[self.get_node(k)] += 1
        return counts

    def ring_points(self, key: str) -> Tuple[int, int]:
        """Return (key_hash, owning_point_hash) for debug output."""
        h = _hash(key)
        pos = bisect.bisect(self._ring_hashes, h)
        if pos == len(self._ring_hashes):
            pos = 0
        return h, self._ring_hashes[pos]
