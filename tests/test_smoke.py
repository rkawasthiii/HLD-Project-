"""Smoke tests for the core components — no server or network required.

Run:  python -m unittest discover -s tests
These exercise the data structures directly so they are fast and deterministic.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.consistent_hash import ConsistentHashRing  # noqa: E402
from backend.trie import Trie  # noqa: E402
from backend.trending import TrendingRanker  # noqa: E402
from backend.cache import CacheNode  # noqa: E402


class TestTrie(unittest.TestCase):
    def setUp(self):
        self.t = Trie(cap=10)
        for q, c in [("iphone", 100), ("iphone 15", 80), ("iphone charger", 60),
                     ("ipad", 50), ("java", 40), ("javascript", 90)]:
            self.t.insert(q, c)

    def test_prefix_sorted_by_count(self):
        res = self.t.suggest("ip")
        self.assertEqual(res[0], ("iphone", 100))
        self.assertTrue(all(q.startswith("ip") for q, _ in res))
        counts = [c for _, c in res]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_no_match(self):
        self.assertEqual(self.t.suggest("zzz"), [])

    def test_update_reflows_topk(self):
        self.t.update("java", 999)  # was 40, now should beat javascript for "ja"
        self.assertEqual(self.t.suggest("ja")[0], ("java", 999))


class TestConsistentHashing(unittest.TestCase):
    def test_stable_routing(self):
        ring = ConsistentHashRing(["a", "b", "c", "d"], vnodes_per_node=100)
        self.assertEqual(ring.get_node("iphone"), ring.get_node("iphone"))

    def test_balanced_distribution(self):
        ring = ConsistentHashRing(["a", "b", "c", "d"], vnodes_per_node=200)
        keys = [f"prefix-{i}" for i in range(8000)]
        dist = ring.distribution(keys)
        share = [v / 8000 for v in dist.values()]
        # each of 4 nodes should get roughly a quarter (allow generous slack)
        self.assertTrue(all(0.15 < s < 0.35 for s in share), dist)

    def test_minimal_remap_on_node_removal(self):
        nodes = ["a", "b", "c", "d"]
        ring = ConsistentHashRing(nodes, vnodes_per_node=200)
        keys = [f"k{i}" for i in range(5000)]
        before = {k: ring.get_node(k) for k in keys}
        ring.remove_node("c")
        moved = sum(1 for k in keys if ring.get_node(k) != before[k])
        # only keys that were on 'c' (~25%) should move, not everything
        self.assertLess(moved, len(keys) * 0.45)


class TestCacheTTL(unittest.TestCase):
    def test_hit_then_expire(self):
        node = CacheNode("n", ttl=0.2, max_keys=100)
        node.set("k", [1, 2, 3])
        self.assertEqual(node.get("k"), [1, 2, 3])
        self.assertEqual(node.hits, 1)
        import time
        time.sleep(0.25)
        self.assertIsNone(node.get("k"))  # expired
        self.assertEqual(node.misses, 1)


class TestTrending(unittest.TestCase):
    def test_recency_boost_then_decay(self):
        import time
        tr = TrendingRanker()
        now = time.time()
        tr.record({"spark": 500}, now)
        boosted = tr.score("spark", count=10, now=now)
        self.assertGreater(boosted, 10)  # recent activity lifts the score
        faded = tr.score("spark", count=10, now=now + 10_000)  # long after
        self.assertAlmostEqual(faded, 10, delta=1.0)  # boost decayed away


if __name__ == "__main__":
    unittest.main(verbosity=2)
