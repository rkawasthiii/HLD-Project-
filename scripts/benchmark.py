"""Performance + correctness demo for the report.

Drives the running server over HTTP and prints:
  * suggestion latency (cold vs warm cache) with p95,
  * cache hit-rate,
  * batch write-reduction evidence (submissions vs DB write transactions),
  * a before/after demonstration of recency-aware trending.

Start the server first (python -m backend.server), then in another terminal:
    python scripts/benchmark.py
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read())


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def percentile(values, pct):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def bench_suggestions(prefixes, rounds=20):
    print("\n=== Suggestion latency ===")
    cold, warm = [], []
    # first pass = cold (cache misses), subsequent = warm (cache hits)
    for p in prefixes:
        t0 = time.perf_counter()
        get(f"/suggest?q={p}")
        cold.append((time.perf_counter() - t0) * 1000)
    for _ in range(rounds):
        for p in prefixes:
            t0 = time.perf_counter()
            get(f"/suggest?q={p}")
            warm.append((time.perf_counter() - t0) * 1000)
    print(f"cold (cache miss): p50={percentile(cold,50):.2f}ms  "
          f"p95={percentile(cold,95):.2f}ms")
    print(f"warm (cache hit) : p50={percentile(warm,50):.2f}ms  "
          f"p95={percentile(warm,95):.2f}ms  (n={len(warm)})")


def demo_batch_writes(query="hld demo query", n=2000):
    print("\n=== Batch-write reduction ===")
    before = get("/metrics")["batch"]
    for _ in range(n):
        post("/search", {"query": query})
    print(f"submitted {n} searches for '{query}', waiting for flush ...")
    time.sleep(3)
    after = get("/metrics")["batch"]
    dw = after["db_write_ops"] - before["db_write_ops"]
    print(f"DB write transactions used for {n} submissions: {dw}")
    print(f"=> ~{n // max(dw,1)}x fewer writes than 1-write-per-search")
    print(f"current overall write_reduction = {after['write_reduction_x']}x")


def demo_trending(spike="consistent hashing", n=1500):
    print("\n=== Recency-aware trending ===")
    base = get("/suggest?q=co&mode=count")["suggestions"][:5]
    print("BASIC (by count)   co* ->", [s["query"] for s in base])
    for _ in range(n):
        post("/search", {"query": spike})
    time.sleep(3)
    trend = get("/suggest?q=co&mode=trending")["suggestions"][:5]
    print(f"after {n} searches of '{spike}':")
    print("TRENDING (recency) co* ->",
          [(s["query"], s.get("score")) for s in trend])


def show_cache_debug():
    print("\n=== Consistent hashing routing ===")
    d = get("/cache/debug?prefix=iphone")
    print(f"prefix 'iphone' -> node {d['owner_node']} ({d['state']})")
    print("sample distribution across nodes:",
          json.dumps(d["sample_distribution"]))


def main():
    try:
        get("/healthz")
    except urllib.error.URLError:
        print("Server not reachable. Start it with: python -m backend.server")
        return
    show_cache_debug()
    bench_suggestions(["i", "ip", "iphone", "ja", "java", "best", "amazon", "how"])
    demo_batch_writes()
    demo_trending()
    print("\n=== Final metrics snapshot ===")
    m = get("/metrics")
    print("cache hit rate:", m["cache"]["hit_rate"])
    print("suggest p95:", m["latency"].get("suggest", {}).get("p95_ms"), "ms")
    print("write reduction:", m["batch"]["write_reduction_x"], "x")


if __name__ == "__main__":
    main()
