# Architecture

This document explains the data model, the read/write paths, and the reasoning
behind each major component. It is meant to be enough to defend the design in a
viva.

## Components

| Component | File | Responsibility |
|---|---|---|
| HTTP server | `backend/server.py` | Routing, JSON IO, static hosting. Entry point. |
| App logic | `backend/app.py` | One method per endpoint; wires everything. |
| Primary store | `backend/store.py` | Durable `query_counts` table in SQLite. |
| Serving index | `backend/trie.py` | In-memory Trie with cached top-K per node. |
| Distributed cache | `backend/cache.py` | N logical cache nodes, each a TTL dict. |
| Ring | `backend/consistent_hash.py` | Maps a prefix key → cache node. |
| Batch writer | `backend/batch_writer.py` | Buffers + flushes aggregated writes. |
| Trending | `backend/trending.py` | Time-decayed recency score. |
| Metrics | `backend/metrics.py` | Latency percentiles + counters. |

## Data model

**SQLite `query_counts(query PRIMARY KEY, count, last_searched)`** is the durable
source of truth. At startup we ingest the dataset (if the DB is empty) and then
build the Trie from every row.

The **Trie** is the in-memory representation of that data optimized for the read
path. Each node caches the top-K (`TRIE_CAP`) completions of the prefix that ends
at that node, sorted by count. A suggestion lookup walks `len(prefix)` nodes and
returns the cached list — no subtree scan.

## Read path: `GET /suggest`

```
prefix -> key = f"{mode}:{prefix}"
       -> ring.get_node(key)            # consistent hashing picks a cache node
       -> node.get(key)                 # TTL cache
            HIT  -> return cached list
            MISS -> trie.suggest(prefix)             # fall back to primary index
                    (mode=trending? re-rank via recency)
                 -> cache.set(prefix, mode, result)  # fill cache
                 -> return result
```

The cache stores the *materialized suggestion list per prefix*, which is the
expensive thing to recompute for hot prefixes. Misses are cheap anyway because the
Trie lookup is O(len(prefix)).

## Write path: `POST /search`

```
POST /search -> batch.submit(query)     # O(1): buffer[query] += 1, return "Searched"

background thread, every BATCH_FLUSH_INTERVAL or BATCH_MAX_SIZE:
  drain buffer -> aggregated deltas
  store.flush_deltas(deltas)            # ONE SQLite transaction (upsert +=)
  for each (query, new_count): trie.update(query, new_count)   # refresh top-K
  trending.record(deltas, now)          # feed recency tracker
  cache.invalidate_prefixes(prefixes)   # drop stale cached lists
```

This is the batching requirement: many per-search writes become one aggregated
transaction per flush.

## Consistent hashing

The ring is a sorted array of `(hash, node)` points. Each physical cache node is
placed at `VNODES_PER_NODE` positions (`hash("node#i")`). To find the owner of a
key we binary-search for the first point `>= hash(key)`, wrapping around at the
end. Virtual nodes smooth the distribution (≈ 1/N per node) and ensure that
adding/removing a node only remaps the keys in the arcs that node owned (~1/N),
not the whole keyspace. `GET /cache/debug` exposes the owning node, the key/point
hashes, and a sample distribution so the behavior is observable.

## Trending: combining popularity and recency

- **Basic (`mode=count`)**: rank by total `count`. Historically popular wins.
- **Enhanced (`mode=trending`)**: `score = count + RECENCY_WEIGHT * recent`.

`recent` is an exponential moving sum maintained per query:

```
on a flush with `delta` new searches at time `now`:
    recent <- recent * exp(-(now - last)/TAU) + delta
when scoring at time `now`:
    recent_now <- recent * exp(-(now - last)/TAU)
```

Because the contribution decays exponentially (time constant `TAU`), a query that
spikes briefly loses its boost within a few `TAU` and reverts to count-based
ranking. This is the answer to "avoid permanently over-ranking a short-lived
trend." The hot-set is pruned once a query's decayed value falls below a small
epsilon, keeping the structure small. Trending re-ranks a candidate set (the
prefix's top-K by count ∪ hot queries matching the prefix), so it is cheap.

## Failure & consistency trade-offs

- **Buffered-but-unflushed loss:** a crash loses at most one flush window of
  submissions (≤ `BATCH_FLUSH_INTERVAL` seconds or `BATCH_MAX_SIZE` queries). We
  accept this bounded loss for a large write-amplification reduction. A WAL on the
  buffer would eliminate it at the cost of complexity.
- **Eventual consistency of suggestions:** counts from new searches appear after
  the next flush + cache invalidation (a couple of seconds), not instantly. This
  is fine for typeahead and keeps the read path fast.
- **In-memory recency state:** the recency tracker is not persisted; on restart
  trending falls back to pure counts until new activity accumulates. Acceptable
  for the assignment; could be snapshotted if needed.

## Tunables (`backend/config.py`)

| Knob | Default | Effect |
|---|---|---|
| `CACHE_NODES` | 4 | number of logical cache nodes on the ring |
| `VNODES_PER_NODE` | 150 | distribution smoothness / remap cost |
| `CACHE_TTL_SECONDS` | 30 | staleness safety net |
| `BATCH_MAX_SIZE` | 500 | size-triggered flush |
| `BATCH_FLUSH_INTERVAL` | 2 s | time-triggered flush; durability window |
| `RECENCY_TAU_SECONDS` | 300 | how fast a trend fades |
| `RECENCY_WEIGHT` | 20000 | how strongly recency can lift ranking |
| `TRIE_CAP` / `SUGGEST_LIMIT` | 10 | suggestions cached/returned |
