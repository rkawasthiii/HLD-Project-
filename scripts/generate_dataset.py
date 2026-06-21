"""Generate a synthetic but realistic search-query dataset (>= 100,000 rows).

Output format (one row per line):   query<TAB>count

Counts follow a Zipf-like (power-law) distribution so a few head queries are very
popular and a long tail is rare -- exactly the shape real search traffic has, and
the shape that makes the typeahead + caching interesting.

Why synthetic: it guarantees the project runs out-of-the-box with the required
>=100k size and no licensing/download friction. To use a REAL open-source dataset
instead (e.g. Wikipedia page titles, the AOL query log, or an e-commerce product
catalogue), just produce the same "query<TAB>count" format and drop it at
data/queries.txt -- see the README "Swapping in a real dataset" section.

Usage:
    python scripts/generate_dataset.py            # default 120,000 queries
    python scripts/generate_dataset.py 250000     # custom size
"""
import os
import random
import sys

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "queries.txt")

BRANDS = ["iphone", "samsung", "google", "amazon", "sony", "dell", "hp", "lenovo",
          "asus", "nike", "adidas", "puma", "lg", "bosch", "philips", "canon",
          "nikon", "intel", "amd", "nvidia", "logitech", "xiaomi", "oneplus",
          "realme", "boat", "jbl", "oppo", "vivo", "microsoft", "apple"]

PRODUCTS = ["phone", "laptop", "charger", "headphones", "earbuds", "tv", "watch",
            "tablet", "keyboard", "mouse", "monitor", "camera", "speaker",
            "router", "ssd", "hard drive", "power bank", "smartwatch", "printer",
            "graphics card", "processor", "ram", "cooler", "cable", "case"]

TOPICS = ["java", "python", "javascript", "react", "node", "sql", "docker",
          "kubernetes", "system design", "data structures", "algorithms",
          "machine learning", "html", "css", "git", "linux", "aws", "spring boot",
          "consistent hashing", "caching", "load balancing", "rest api"]

TAILS = ["tutorial", "for beginners", "interview questions", "cheat sheet",
         "online", "near me", "price", "review", "vs", "2026", "best",
         "how to", "download", "free", "course", "example", "documentation",
         "specifications", "comparison", "deals", "offers", "buy"]

ADJ = ["best", "cheap", "top", "new", "used", "wireless", "gaming", "budget",
       "premium", "portable", "fast", "mini", "pro", "ultra"]


def build_pool():
    """Build distinct queries in popularity tiers.

    Shorter, cleaner queries are emitted first so they receive the highest
    (Zipf) counts -> a realistic typeahead head like: iphone, iphone 15,
    iphone charger ... rather than long numbered tail queries on top.
    """
    seen = set()
    ordered = []

    def add(q):
        if q not in seen:
            seen.add(q)
            ordered.append(q)

    # tier 1: single, very popular terms
    for b in BRANDS:
        add(b)
    for t in TOPICS:
        add(t)
    # tier 2: clean two-word queries
    for b in BRANDS:
        for p in PRODUCTS:
            add(f"{b} {p}")
    for a in ADJ:
        for p in PRODUCTS:
            add(f"{a} {p}")
    for t in TOPICS:
        for tail in TAILS:
            add(f"{t} {tail}")
    # tier 3: three-word long-tail queries
    for b in BRANDS:
        for p in PRODUCTS:
            for tail in TAILS:
                add(f"{b} {p} {tail}")
    for a in ADJ:
        for p in PRODUCTS:
            for tail in TAILS:
                add(f"{a} {p} {tail}")
    for a in ADJ:
        for b in BRANDS:
            for p in PRODUCTS:
                add(f"{a} {b} {p}")
    return ordered


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 120_000
    random.seed(42)

    pool = build_pool()
    # Pad the long tail with numbered variants if we need more rows. These are
    # appended AFTER the clean tiers, so they get the smallest counts.
    i = 0
    while len(pool) < target:
        base = pool[i % min(len(pool), 4000)]
        pool.append(f"{base} {i}")
        i += 1
    pool = pool[:target]  # keep tier order: clean head first, numbered tail last

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for rank, query in enumerate(pool, start=1):
            # Zipf-like count by rank: head queries ~1e6, long tail ~ tens.
            # Small jitter avoids exact ties without reordering the tiers.
            count = int(1_000_000 / rank) + random.randint(0, 9)
            fh.write(f"{query}\t{count}\n")

    print(f"Wrote {len(pool):,} queries to {os.path.abspath(OUT)}")
    print("Format: query<TAB>count  (Zipf-like counts)")


if __name__ == "__main__":
    main()
